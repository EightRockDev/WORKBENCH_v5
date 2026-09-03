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
import re
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


# Column names that could plausibly carry a unit count or building
# character - flagged with samples in section 2c so the alias decision
# rests on evidence, not name guessing (the FRM_PRCL lesson, 2026-09-01).
_UNITISH = re.compile(r"unit|apart|dwell|stor(y|ies)|room|bldg|"
                      r"building|improv|res", re.IGNORECASE)


def _files_column_inventory(conn, files_src: str, gaps: list[str]) -> None:
    """Section 2c: full column set of every rva.gov workbook on hand.

    The Public Data Set is a landing page linking THREE workbooks
    (parcels, land, building characteristics), and until 2026-09-03 the
    file-follower kept only the FIRST one - so "the workbook maps no
    units" really meant "the unit-bearing workbook was never downloaded".
    Every ingested row carries its resolved file URL in _file; this lists
    each file's columns with unit-suspect ones flagged and sampled, so
    the next re-pull PROVES which files landed and names the unit column
    to alias in core/phase0.py."""
    print("\n-- 2c. rva.gov files on hand (columns per workbook) --")
    by_file: dict[str, dict] = {}
    for (rec4,) in conn.execute(
            "SELECT record FROM muni_records WHERE market='Richmond' "
            "AND source_url=?", (files_src,)):
        raw4 = phase0._decode_muni_record(rec4)
        if not raw4:
            continue
        fkey = str(raw4.get("_file", "?")).rsplit("/", 1)[-1]
        d4 = by_file.setdefault(fkey, {"n": 0, "cols": {}, "samples": {}})
        d4["n"] += 1
        for k, v in raw4.items():
            if k == "_file":
                continue
            d4["cols"][k] = d4["cols"].get(k, 0) + 1
            if (_UNITISH.search(k) and k not in d4["samples"]
                    and v not in (None, "", "None")):
                d4["samples"][k] = str(v)[:32]
    for fkey, d4 in sorted(by_file.items()):
        cols = sorted(d4["cols"])
        print(f"  {fkey}: {d4['n']:,} rows, {len(cols)} columns")
        print("    " + ", ".join(cols)[:600])
        flagged = [k for k in cols if _UNITISH.search(k)]
        if flagged:
            for k in flagged[:10]:
                print(f"    [unit-suspect] {k} "
                      f"(non-empty on {d4['cols'][k]:,} rows, "
                      f"sample: {d4['samples'].get(k, 'all empty')})")
        else:
            print("    [unit-suspect] none")
    if len(by_file) < 2:
        gaps.append(
            f"Only {len(by_file)} rva.gov workbook(s) ingested - the "
            "Public Data Set is a 3-file set (parcels, land, building "
            "characteristics), and the building file carries the unit "
            "counts. The landing-page follower fix (2026-09-03) downloads "
            "all of them on the next stale re-pull; if this line still "
            "shows <2 files after that, read the arcgis-sales-latest "
            "per-file lines for what failed.")


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
                "coords": 0, "units_and_coords": 0, "shapes": set(),
                "sample": None, "apns": set()})
            d["n"] += 1
            # Coordinate coverage per source, because the geometry bridge
            # can only pair sources that BOTH carry one. Richmond merged
            # zero on 2026-09-01 while Atlanta merged 92, and nothing in
            # this report said which Richmond feed was coordinate-blind.
            has_xy = (m.get("lat") is not None and m.get("lng") is not None)
            if has_xy:
                d["coords"] += 1
                if m.get("units"):
                    d["units_and_coords"] += 1
            apn_norm = spine.normalize_apn(str(m.get("apn") or ""))
            if apn_norm:
                d["apn"] += 1
                d["apns"].add(apn_norm)
                if d["sample"] is None:
                    d["sample"] = str(m.get("apn"))
            if m.get("units"):
                d["units"] += 1
            if apn_norm:
                from core.geo_bridge import apn_shape
                d["shapes"].add(apn_shape(str(m.get("apn") or "")))
            if m.get("assessed_value"):
                d["val"] += 1
            if m.get("use_code"):
                d["use"] += 1
        for src, d in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {src[:60]}")
            print(f"    rows={d['n']:,}  apn={d['apn']:,} "
                  f"(sample: {d['sample'] or 'NONE'})  units={d['units']:,}  "
                  f"assessed_value={d['val']:,}  use_code={d['use']:,}")
            print(f"    coords={d['coords']:,}  "
                  f"units+coords={d['units_and_coords']:,}  "
                  f"apn shapes={sorted(d['shapes'])[:3] or 'NONE'}")
        # Two RAW records from the unit-bearing source, keys and values
        # verbatim. 2026-09-01: the fix hinged on what FRM_PRCL and
        # PARCEL_LOCATION actually contain, and no report showed a single
        # raw value - the field lists said what columns exist, never what
        # is in them. Guessing from field NAMES is how the apn alias
        # picked a numeric account id.
        unit_src2 = max(per.items(), key=lambda kv: kv[1]["units"],
                        default=(None, None))[0]
        if unit_src2 and per[unit_src2]["units"]:
            print(f"  -- 2 raw records from the unit-bearing source "
                  f"({unit_src2[:48]}) --")
            shown = 0
            for src3, rec3 in conn.execute(
                    "SELECT source_url, record FROM muni_records "
                    "WHERE market='Richmond' AND kind LIKE 'assessor%' "
                    "AND source_url = ?", (unit_src2,)):
                raw3 = phase0._decode_muni_record(rec3)
                if not raw3:
                    continue
                m3 = phase0.normalize_record("Richmond", "VA", raw3)
                if not m3.get("units"):
                    continue
                shown += 1
                for k in sorted(raw3):
                    v = str(raw3[k])
                    if v not in ("", "None", "null"):
                        print(f"      {k} = {v[:48]}")
                print("      " + "-" * 40)
                if shown >= 2:
                    break

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
                # WHICH raw attribute carries the workbook's parcel key?
                # (2026-08-11: workbook PINs look like C0010124002 while the
                # COR feed's mapped apn is 405010001 - a different id scheme.
                # Score every raw key on the API sources by how many of its
                # values land in the workbook apn set; the winning key is the
                # alias to add in core/phase0.py. Evidence, not format
                # guessing.)
                hits: dict[tuple[str, str], int] = {}
                scanned: dict[str, int] = {}
                for src2, rec2 in conn.execute(
                        "SELECT source_url, record FROM muni_records "
                        "WHERE market='Richmond' AND kind LIKE 'assessor%'"):
                    s2 = src2 or "?"
                    if s2 == files_src or scanned.get(s2, 0) >= 4000:
                        continue
                    raw2 = phase0._decode_muni_record(rec2)
                    if not raw2:
                        continue
                    scanned[s2] = scanned.get(s2, 0) + 1
                    for k, v in raw2.items():
                        if v is None or isinstance(v, (dict, list)):
                            continue
                        nv = spine.normalize_apn(str(v))
                        if nv and nv in fset:
                            hits[(s2, k)] = hits.get((s2, k), 0) + 1
                if hits:
                    print("  alias candidates (API attribute -> workbook apn "
                          "overlap, first 4,000 rows per source):")
                    for (s2, k), n in sorted(hits.items(),
                                             key=lambda kv: -kv[1])[:6]:
                        print(f"    {n:>6,}  {k}  @ {s2[:52]}")
                else:
                    print("  alias candidates: NONE - no API attribute value "
                          "appears in the workbook apn set (a crosswalk "
                          "feed is needed, not an alias)")
                gaps.append(
                    f"rva.gov workbook rows do NOT join the parcel "
                    f"backbone ({overlap:,}/{len(fset):,} apn matches) - "
                    "their units/values enrich nothing. The alias-candidate "
                    "lines above name the raw attribute to alias in "
                    "core/phase0.py.")
            # CROSSWALK EVIDENCE (2026-08-14). The COR layer holds every
            # Richmond unit count (2,365 rows) but keys on a NUMERIC parcel
            # id, while values + parcels now key on the letter PIN - so units
            # cannot merge, MF collapsed to 140 rows and section 4 stays
            # blocked. No COR attribute matches the workbook apn set (the
            # alias scan above proves it), so the join has to be built on
            # something else. Address is the candidate: measure the real
            # overlap before writing any merge, exactly as the alias-candidate
            # scan did before the PTM_ID fix.
            from core.calibration import _norm_addr
            addr_by_src: dict[str, dict] = {}
            for src2, rec2 in conn.execute(
                    "SELECT source_url, record FROM muni_records "
                    "WHERE market='Richmond' AND kind LIKE 'assessor%'"):
                raw2 = phase0._decode_muni_record(rec2)
                if not raw2:
                    continue
                m2 = phase0.normalize_record("Richmond", "VA", raw2)
                a = _norm_addr(m2.get("address"))
                if not a:
                    continue
                d2 = addr_by_src.setdefault(src2 or "?",
                                            {"addrs": set(), "units": set()})
                d2["addrs"].add(a)
                if m2.get("units"):
                    d2["units"].add(a)
            # Report address coverage per source ALWAYS. The first run of
            # this scan printed nothing at all, which reads as "no problem"
            # when it actually meant "the unit-bearing source has no usable
            # address either" - a bigger finding than the one being hunted.
            print("  address coverage per source (usable normalized "
                  "addresses / with units):")
            for s2, d2 in sorted(addr_by_src.items(),
                                 key=lambda kv: -len(kv[1]["addrs"])):
                print(f"    {len(d2['addrs']):>7,} addrs  "
                      f"{len(d2['units']):>7,} of them carry units   "
                      f"{s2[:44]}")
            if not addr_by_src:
                print("    NONE - no Richmond assessor source maps an "
                      "address at all")
            unit_src = max(addr_by_src.items(),
                           key=lambda kv: len(kv[1]["units"]),
                           default=(None, None))[0]
            if not unit_src or not addr_by_src.get(unit_src, {}).get("units"):
                gaps.append(
                    "Units and values sit on different parcel-id schemes AND "
                    "no unit-bearing source maps a usable address - so "
                    "neither an alias nor an address crosswalk can bridge "
                    "them. The geometry bridge now runs on every spine build "
                    "(core/geo_bridge.py): read 'units bridged by geometry' "
                    "and its rejection breakdown in phase0-latest.txt. If "
                    "everything there is 'rejected (too far)', the two feeds' "
                    "centroids do not land in the same place and a wider "
                    "radius is the wrong fix - check the projection.")
            if unit_src and addr_by_src[unit_src]["units"]:
                u_addrs = addr_by_src[unit_src]["units"]
                print("  address-crosswalk candidates (the unit-bearing "
                      f"source is {unit_src[:52]}, {len(u_addrs):,} parcels "
                      "with units and a usable address):")
                for s2, d2 in sorted(addr_by_src.items(),
                                     key=lambda kv: -len(kv[1]["addrs"])):
                    if s2 == unit_src:
                        continue
                    hit = len(u_addrs & d2["addrs"])
                    pct = (100.0 * hit / len(u_addrs)) if u_addrs else 0.0
                    print(f"    {hit:>6,} ({pct:4.1f}%) of unit-bearing "
                          f"addresses also appear in {s2[:46]}")
                gaps.append(
                    "Richmond units and values sit on DIFFERENT parcel-id "
                    "schemes (COR numeric vs letter PIN) and no attribute "
                    "bridges them - see the address-crosswalk candidate lines "
                    "above. A high overlap there is the go-ahead to merge COR "
                    "units onto the value-bearing parcels by normalized "
                    "address; a low one means the crosswalk needs geometry.")

            if per[files_src]["units"] == 0:
                gaps.append(
                    "The rva.gov Public Data Set maps NO unit counts - "
                    "check section 2b and the phase0 unmapped-keys list "
                    "for the workbook's unit column, or promote a units-"
                    "bearing candidate from discover-sales-latest.txt.")

        # ---- 2c. per-FILE column inventory for the rva.gov workbooks ----
        if files_src:
            _files_column_inventory(conn, files_src, gaps)

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
