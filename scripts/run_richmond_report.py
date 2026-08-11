"""Autopilot step: the Richmond review (owner directive 2026-08-11: "Load the
Richmond Data now... I want to review it all at 3:00AM eastern. Properties,
sales, units, taxes, etc.").

One published file - reports/richmond-review.txt - that answers the review in
a single read, and says LOUDLY what is still missing (a gap named is a gap
the owner can unblock; a gap hidden looks like completeness).

Sections:
  1. Backbone properties (properties_8r, market Richmond): total, MF count,
     unit distribution, year-built range, assessed-value stats.
  2. Raw assessor rows on hand per source (muni_records assessor%): where the
     backbone's inputs come from and how fresh they are.
  3. Sales (muni_records kind='sales' Richmond): row count, date range,
     consideration stats, latest 5 transfers.
  4. Assessed value -> estimated annual tax at the city rate
     (ER_RICHMOND_TAX_RATE per $100, default 1.20 - Richmond's current real
     estate rate; env-tunable so a rate change never needs a code change).
  5. GAPS: explicit list (assessments feed missing/403, sales missing/403,
     unit coverage thin) with the exact unblock step for each.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0  # noqa: E402

RATE_PER_100 = float(os.environ.get("ER_RICHMOND_TAX_RATE", "1.20"))


def _stats(vals: list[float]) -> str:
    if not vals:
        return "none"
    return (f"n={len(vals):,}  min={min(vals):,.0f}  "
            f"median={statistics.median(vals):,.0f}  max={max(vals):,.0f}")


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("richmond-review: no workbench.db on this box - skipping")
        return 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    gaps: list[str] = []
    try:
        # ---- 1. backbone ------------------------------------------------
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM properties_8r "
            "WHERE COALESCE(r8_market, city) = 'Richmond'").fetchone()["n"]
        # MF = unit-count evidence ONLY. derive_8r_form defaults every parcel
        # to "garden", so "r8_form IS NOT NULL" matched ALL rows and the
        # midnight-cycle review claimed 102,232 MF properties - a number the
        # owner cannot trust. Honest version: count rows with units data,
        # then MF among them, and gap loudly when unit coverage is thin.
        with_units = conn.execute(
            "SELECT COUNT(*) AS n FROM properties_8r "
            "WHERE COALESCE(r8_market, city) = 'Richmond' "
            "AND units IS NOT NULL AND units > 0").fetchone()["n"]
        mf = conn.execute(
            "SELECT units, year_built, assessed_value FROM properties_8r "
            "WHERE COALESCE(r8_market, city) = 'Richmond' "
            "AND units IS NOT NULL AND units >= ?",
            (phase0.MIN_MF_UNITS,)).fetchall()
        units = [int(r["units"]) for r in mf if r["units"]]
        yrs = [int(r["year_built"]) for r in mf if r["year_built"]]
        vals = [float(r["assessed_value"]) for r in mf if r["assessed_value"]]

        print("=" * 64)
        print("RICHMOND REVIEW  @",
              dt.datetime.now().isoformat(timespec="seconds"))
        print("=" * 64)
        print("\n-- 1. Backbone properties (properties_8r) --")
        print(f"Richmond properties total: {total:,}")
        print(f"  with unit data:  {with_units:,}")
        print(f"Multifamily (units>={phase0.MIN_MF_UNITS}): {len(mf):,}")
        if total and with_units < total * 0.01:
            gaps.append(f"Unit coverage is thin: {with_units:,} of {total:,} "
                        "parcels carry unit counts - the COR ownership layer "
                        "has none, so units depend on the rva.gov Public "
                        "Data Set files or a discover-sales-latest candidate "
                        "(no token chases - owner 2026-08-11).")
        if units:
            print(f"  units:          {_stats([float(u) for u in units])}")
        else:
            gaps.append("Unit counts: no Richmond MF rows carry units - "
                        "unit data must come from a richer assessor layer "
                        "(rva.gov files / discover-sales-latest candidates).")
        if yrs:
            print(f"  year built:     {min(yrs)}-{max(yrs)}")
        if vals:
            print(f"  assessed value: {_stats(vals)}")

        # ---- 2. assessor inputs ----------------------------------------
        print("\n-- 2. Raw Richmond assessor rows on hand --")
        srcs = conn.execute(
            "SELECT source_url, COUNT(*) AS n, MAX(pulled_at) AS at "
            "FROM muni_records WHERE market='Richmond' AND kind LIKE "
            "'assessor%' GROUP BY source_url ORDER BY n DESC").fetchall()
        if not srcs:
            gaps.append("NO Richmond assessor rows at all - discovery/pull "
                        "has not landed any Richmond feed.")
        for r in srcs:
            print(f"  {r['n']:>8,}  {r['at'][:19] if r['at'] else '?':<19}  "
                  f"{r['source_url'][:80]}")
        if not any(("richmondgov" in (r["source_url"] or ""))
                   or ("rva.gov" in (r["source_url"] or ""))
                   or ("il6vO1TutlF580Ku" in (r["source_url"] or ""))
                   for r in srcs):
            gaps.append("Assessments have NOT landed by ANY path (the COR "
                        "parcels Esri API or the rva.gov monthly files) - "
                        "check arcgis-sales-latest.txt for the failing "
                        "fetch, and discover-sales-latest.txt for ranked "
                        "alternate sources.")

        # ---- 2b. field-mapping health per assessor source ---------------
        # The 3AM 2026-08-11 review looked complete (76,976 rva.gov rows
        # "on hand") while every one of them sat orphaned: the workbook's
        # parcel key (PID) had no alias, so nothing merged onto the
        # COR/VDEM parcels and units/values never reached the backbone.
        # This section makes that failure mode visible in one read: per
        # source, how many rows map an apn/units/assessed value, plus the
        # apn overlap between the rva.gov files and the API feeds - the
        # join is HEALTHY only when that overlap is large.
        print("\n-- 2b. Field mapping per source (spine-visible attributes) --")
        from core import spine
        per: dict[str, dict] = {}
        for src, rec in conn.execute(
                "SELECT source_url, record FROM muni_records "
                "WHERE market='Richmond' AND kind LIKE 'assessor%'"):
            raw = phase0._decode_muni_record(rec)
            if not raw:
                continue
            m = phase0.normalize_record("Richmond", "VA", raw)
            d = per.setdefault(src or "?", {
                "n": 0, "apn": 0, "units": 0, "val": 0, "use": 0,
                "sample": None, "apns": set()})
            d["n"] += 1
            apn_norm = spine.normalize_apn(str(m.get("apn") or ""))
            if apn_norm:
                d["apn"] += 1
                d["apns"].add(apn_norm)
                if d["sample"] is None:
                    d["sample"] = str(m.get("apn"))
            if m.get("units"):
                d["units"] += 1
            if m.get("assessed_value"):
                d["val"] += 1
            if m.get("use_code"):
                d["use"] += 1
        for src, d in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {src[:60]}")
            print(f"    rows={d['n']:,}  apn={d['apn']:,} "
                  f"(sample: {d['sample'] or 'NONE'})  units={d['units']:,}  "
                  f"assessed_value={d['val']:,}  use_code={d['use']:,}")
        files_src = next((s for s in per if s.startswith("files:")), None)
        if files_src:
            fset = per[files_src]["apns"]
            others: set = set()
            for s, d in per.items():
                if s != files_src:
                    others |= d["apns"]
            overlap = len(fset & others)
            print(f"  rva.gov-files join health: {overlap:,} of "
                  f"{len(fset):,} workbook parcels match an API-feed parcel")
            if fset and overlap < len(fset) * 0.5:
                gaps.append(
                    f"rva.gov workbook rows do NOT join the parcel "
                    f"backbone ({overlap:,}/{len(fset):,} apn matches) - "
                    "their units/values enrich nothing. Compare the apn "
                    "samples in section 2b and add the right alias in "
                    "core/phase0.py.")
            if per[files_src]["units"] == 0:
                gaps.append(
                    "The rva.gov Public Data Set maps NO unit counts - "
                    "check section 2b and the phase0 unmapped-keys list "
                    "for the workbook's unit column, or promote a units-"
                    "bearing candidate from discover-sales-latest.txt.")

        # ---- 3. sales ---------------------------------------------------
        print("\n-- 3. Richmond sales (muni_records kind='sales') --")
        sales = conn.execute(
            "SELECT record FROM muni_records WHERE market='Richmond' AND "
            "kind='sales'").fetchall()
        print(f"sale rows: {len(sales):,}")
        if sales:
            from core.sale_history import extract_sale_records
            recs = []
            for (raw,) in sales:
                try:
                    parsed = extract_sale_records(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    parsed = []
                recs.extend(parsed)
            dates = sorted(r["date"] for r in recs if r.get("date"))
            prices = [r["price"] for r in recs if r.get("price")]
            if dates:
                print(f"  transfer dates: {dates[0]} .. {dates[-1]}")
            if prices:
                print(f"  consideration:  {_stats(prices)}")
            for r in sorted(recs, key=lambda x: x.get("date") or "",
                            reverse=True)[:5]:
                print(f"    {r.get('date')}  ${(r.get('price') or 0):,.0f}  "
                      f"-> {(r.get('grantee') or '?')[:40]}")
        else:
            gaps.append("NO Richmond sales rows - the rva.gov transfers "
                        "workbook has not landed. Check "
                        "arcgis-sales-latest.txt [sales:Richmond-files] "
                        "and send the report line to Claude.")

        # ---- 4. taxes ---------------------------------------------------
        print("\n-- 4. Estimated real-estate tax (rate "
              f"${RATE_PER_100:.2f}/$100 assessed; ER_RICHMOND_TAX_RATE) --")
        if vals:
            taxes = [v * RATE_PER_100 / 100.0 for v in vals]
            print(f"  MF annual tax:  {_stats(taxes)}")
            print(f"  MF tax base:    ${sum(vals):,.0f} assessed -> "
                  f"${sum(taxes):,.0f}/yr estimated")
        else:
            gaps.append("Tax estimates: blocked on assessed values (needs "
                        "the assessments feed above).")

        # ---- 5. gaps ----------------------------------------------------
        print("\n-- 5. GAPS (each names its unblock) --")
        if gaps:
            for g in gaps:
                print(f"  [GAP] {g}")
            return 1        # visible non-zero: review is NOT complete
        print("  none - Richmond properties, MF, units, sales, values and "
              "tax estimates are all present.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
