"""Pull municipal property SALES into muni_records (kind='sales') from each
jurisdiction's real source - three adapter types (owner-verified 2026-08-11).

The "same ArcGIS pattern everywhere" assumption broke on contact: only VB
publishes true per-parcel transaction history from one Esri endpoint. The
verified integration map (owner ran the live probes in a parallel session):

  * esri_history (Virginia Beach): Property_Sales_ FeatureServer - full
    transfer-event table (~594k rows, ~2wk lag). Standard Esri /query,
    2,000/page via resultOffset.
  * socrata_snapshot_stack (Norfolk): data.norfolk.gov "Property Assessment
    and Sales" - ONE row per parcel, LATEST transfer only, re-published per
    fiscal year. Stacking the FY19..FY27 files and deduping on
    (gpin, transfer_date) recovers ~the last 3 sales per parcel. FY27
    (qva7-tzrf) is live with a ~5-day lag. Plain JSON GET, $limit/$offset
    paging, no auth.
  * esri_date + xlsx_join (Chesapeake): the parcels layer carries
    TRANSFER/DEEDBK/DEEDPG but NO price (it already flows as kind='assessor';
    core.sale_history reads its TRANSFER date). Prices come from the
    assessor's annual LandBook XLS exports (portal item downloads), one row
    per parcel with CONSIDERATION/TRANSFER DATE/CURRENTOWNER - joined to
    parcels on MAP_PARCEL, which phase0 already aliases to apn.

Why rows are stored VERBATIM: core.sale_history.extract_sale_records already
recognizes every field spelling here (Sale_Price/Sales_Date, consideration/
transfer_date, CONSIDERATION/"TRANSFER DATE"/CURRENTOWNER/DEEDBK/DEEDPG), and
phase0.normalize_record aliases GPIN/gpin/MAP_PARCEL/PARNO to apn. So each
source row lands as-is and core.sale_index picks it up next cycle.

Design rules (host-only; the build env is firewalled from all of these):
  * SIZE FIRST: every adapter logs the expected row count before writing, so
    a silent 0 or truncated pull can never look "done" ([OK] ... 0 records
    from a once-full feed is a BREAK - the VB Property_Sales_view lesson).
  * A transient empty pull NEVER deletes existing rows for that source.
  * One source failing never sinks the others.

Env:
  ER_ARCGIS_SALES_SINCE_YEAR   VB: earliest sale year (default 2021; set low
                               for full deed-chain tenure)
  ER_ARCGIS_SALES_REFRESH_D    global re-pull gate override in days
                               (default: per-source refresh_d)
  ER_ARCGIS_SALES_MARKETS      comma list to restrict (default = all)
  ER_ARCGIS_SALES_MAX          safety cap on records per market (400000)
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from core import phase0  # noqa: E402

PAGE = 2000                              # Esri Hub default max per page
SOCRATA_PAGE = 50000                     # Socrata $limit ceiling
SINCE_YEAR = int(os.environ.get("ER_ARCGIS_SALES_SINCE_YEAR", "2021"))
MAX_RECORDS = int(os.environ.get("ER_ARCGIS_SALES_MAX", "400000"))
UA = {"User-Agent": "EightRockWorkbench/1.0 (contact bmccune@gmail.com)",
      "Accept": "application/json"}

# Verified sources ONLY (never pull blind). Each entry's type picks the
# adapter; source_tag is the muni_records.source_url key that scopes the
# delete+insert refresh and the freshness stamp.
SALES_SOURCES: dict[str, dict] = {
    "Virginia Beach": {
        "type": "arcgis",
        "url": ("https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/"
                "services/Property_Sales_/FeatureServer/0"),
        "state": "VA", "county": "Virginia Beach",
        "refresh_d": 7,                  # dataset lags ~2 weeks
    },
    "Norfolk": {
        "type": "socrata_stack",
        "base": "https://data.norfolk.gov/resource/",
        # Oldest FIRST - on a (gpin, transfer_date) collision the later file
        # (fresher owner/value snapshot) wins.
        "resources": (("FY19", "th3n-jr9u"), ("FY20", "pdf2-gh9c"),
                      ("FY21", "8bfx-a5g8"), ("FY22", "7tu9-2ytx"),
                      ("FY23", "yvpm-8aid"), ("FY24", "9gmp-9x4c"),
                      ("FY25", "g7sg-tivf"), ("FY26", "m5ya-5grb"),
                      ("FY27", "qva7-tzrf")),
        "state": "VA", "county": "Norfolk",
        "source_tag": "socrata-stack:data.norfolk.gov/property-assessment-and-sales",
        "refresh_d": 3,                  # FY27 file lags ~5 days
    },
    "Chesapeake": {
        "type": "landbook_xlsx",
        "items": (
            ("commercial",
             "https://gis.cityofchesapeake.net/portal/sharing/rest/content/"
             "items/28f709bf912740ea99eba88c597a0c12/data"),
            ("residential",
             "https://gis.cityofchesapeake.net/portal/sharing/rest/content/"
             "items/714668f436894569a65db695022fb3db/data"),
        ),
        "state": "VA", "county": "Chesapeake",
        "source_tag": "landbook:gis.cityofchesapeake.net FY26-27",
        "refresh_d": 30,                 # annual export
    },
    # Richmond (owner directive 2026-08-11 "don't stop until data on server"):
    # data.richmondgov.com Socrata. "Property Transfer History" (uxre-by3i,
    # quarterly, full history) + "Property Transfers" (k9h9-y482, recent) -
    # same stack+dedupe treatment as Norfolk; the "current" file lists later
    # so its fresher rows win collisions. Field spellings self-verify on the
    # first sized host run (the adapter probes id/date key candidates).
    "Richmond": {
        "type": "socrata_stack",
        "base": "https://data.richmondgov.com/resource/",
        "resources": (("history", "uxre-by3i"), ("current", "k9h9-y482")),
        "state": "VA", "county": "Richmond",
        "source_tag": "socrata-stack:data.richmondgov.com/property-transfers",
        "refresh_d": 7,
    },
}

# Socrata column names vary per city (Norfolk: gpin/transfer_date; Richmond:
# pin or parcel_id / transfer_date...). Probe candidates in order; the sized
# per-FY log makes a spelling miss loud (0 dated transfers = break, not done).
_STACK_ID_KEYS = ("gpin", "parcel_id", "pin", "parcelid", "parcel", "parid")
_STACK_DATE_KEYS = ("transfer_date", "transferdate", "sale_date", "saledate",
                    "date_of_transfer", "transfer_dt", "deed_date")

# Arm's-length, recent (VB only - the event table is huge; Sale_Price>0 drops
# $0 deed transfers, the date bound reproduces the owner's verified 47,631).
WHERE = f"Sale_Price > 0 AND Sales_Date >= DATE '{SINCE_YEAR}-01-01'"


# ---------------------------------------------------------------- plumbing

def _get_json(url: str, params: dict | None = None):
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def _query(url: str, params: dict) -> dict | None:
    """Esri layer query (kept as a seam for tests)."""
    return _get_json(url + "/query", params)


def _source_fresh(conn, source_url: str, cutoff_iso: str) -> bool:
    row = conn.execute(
        "SELECT max(pulled_at) FROM muni_records "
        "WHERE kind='sales' AND source_url=?", (source_url,)).fetchone()
    return bool(row and row[0] and row[0] >= cutoff_iso)


def _refresh_cutoff(cfg: dict) -> str:
    days = int(os.environ.get("ER_ARCGIS_SALES_REFRESH_D",
                              str(cfg.get("refresh_d", 7))))
    return (dt.datetime.now() - dt.timedelta(days=days)).isoformat()


def _replace_rows(conn, source_tag: str, rows: list[tuple]) -> None:
    with conn:
        conn.execute("DELETE FROM muni_records WHERE kind='sales' AND "
                     "source_url=?", (source_tag,))
        conn.executemany(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "pulled_at,record) VALUES (?,?,?,?,?,?,?)", rows)


def _clean_value(v):
    """JSON-safe scalar: datetimes -> ISO date, NaN/NaT -> None."""
    if v is None or isinstance(v, (str, int, bool)):
        return v
    try:
        if v != v:                              # NaN AND pandas NaT
            return None
    except Exception:
        pass
    if isinstance(v, float):
        return None if v != v else v            # NaN check
    if isinstance(v, (dt.date, dt.datetime)):
        return v.date().isoformat() if isinstance(v, dt.datetime) \
            else v.isoformat()
    iso = getattr(v, "isoformat", None)          # pandas Timestamp
    if callable(iso):
        try:
            return str(iso())[:10]
        except Exception:
            return str(v)
    return str(v)


# ---------------------------------------------------------------- adapters

def count_sales(url: str) -> int | None:
    """VB/Esri: size the pull BEFORE paginating (owner directive)."""
    js = _query(url, {"where": WHERE, "returnCountOnly": "true", "f": "json"})
    if js is None or "count" not in js:
        return None
    return int(js["count"])


def iter_features(url: str, expected: int):
    offset = 0
    while offset < min(expected, MAX_RECORDS):
        js = _query(url, {
            "where": WHERE, "outFields": "*", "returnGeometry": "false",
            "resultOffset": offset, "resultRecordCount": PAGE, "f": "json"})
        feats = (js or {}).get("features") or []
        if not feats:
            break
        for f in feats:
            attrs = f.get("attributes")
            if isinstance(attrs, dict):
                yield attrs
        offset += len(feats)
        if len(feats) < PAGE:
            break
        time.sleep(0.3)


def _pull_arcgis(conn, market: str, cfg: dict) -> int:
    url = cfg["url"]
    total = count_sales(url)
    if total is None:
        print(f"[sales:{market}] count query FAILED (endpoint/where?) - "
              f"skip, no rows touched")
        return 0
    print(f"[sales:{market}] {total} arm's-length sales since {SINCE_YEAR} "
          f"to pull")
    if total == 0:
        return 0
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    rows = [(market, cfg["state"], cfg["county"], "sales", url, now_iso,
             json.dumps(attrs)) for attrs in iter_features(url, total)]
    if not rows:
        print(f"[sales:{market}] expected {total} but paginated 0 - NOT "
              f"deleting existing rows (transient?)")
        return 0
    _replace_rows(conn, url, rows)
    print(f"[sales:{market}] wrote {len(rows)} rows (expected {total})")
    return len(rows)


def _socrata_count(base: str, rid: str) -> int | None:
    js = _get_json(f"{base}{rid}.json", {"$select": "count(*)"})
    if isinstance(js, list) and js:
        for v in js[0].values():
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def _socrata_rows(base: str, rid: str):
    offset = 0
    while offset < MAX_RECORDS:
        js = _get_json(f"{base}{rid}.json",
                       {"$limit": SOCRATA_PAGE, "$offset": offset})
        if not isinstance(js, list) or not js:
            break
        yield from js
        offset += len(js)
        if len(js) < SOCRATA_PAGE:
            break
        time.sleep(0.5)


def _pull_socrata_stack(conn, market: str, cfg: dict) -> int:
    """Norfolk: one row per parcel per FY snapshot, latest transfer only.
    Stack every FY file, dedupe on (gpin, transfer_date) - later FY wins -
    to recover multi-sale history (~last 3 sales per parcel)."""
    base = cfg["base"]
    # SIZE FIRST across the whole stack.
    sized = []
    for fy, rid in cfg["resources"]:
        n = _socrata_count(base, rid)
        sized.append((fy, rid, n))
    known = [(fy, n) for fy, _, n in sized if n is not None]
    if not known:
        print(f"[sales:{market}] every FY count query failed - skip, "
              f"no rows touched")
        return 0
    print(f"[sales:{market}] FY stack sizes: "
          + ", ".join(f"{fy}={n}" for fy, n in known)
          + f" (total {sum(n for _, n in known)})")

    dedup: dict[tuple, tuple[int, str]] = {}
    for idx, (fy, rid, n) in enumerate(sized):
        if n is None:
            print(f"[sales:{market}] {fy} ({rid}) unreachable - skipping "
                  f"that year")
            continue
        got = 0
        for raw in _socrata_rows(base, rid):
            if not isinstance(raw, dict):
                continue
            # Strip Socrata meta keys and geometry dicts - not sale data.
            rec = {k: v for k, v in raw.items()
                   if not k.startswith(":") and not isinstance(v, (dict, list))}
            tdate = next((str(rec[k]).strip() for k in _STACK_DATE_KEYS
                          if rec.get(k)), "")
            if not tdate:
                continue                 # never sold / no dated transfer
            gpin = next((str(rec[k]).strip() for k in _STACK_ID_KEYS
                         if rec.get(k)), "")
            if not gpin:
                continue                 # unmatchable either way
            rec["_fy_resource"] = f"{fy}:{rid}"
            key = (gpin, tdate[:10])
            prev = dedup.get(key)
            if prev is None or idx >= prev[0]:
                dedup[key] = (idx, json.dumps(rec))
            got += 1
        print(f"[sales:{market}] {fy}: {got} dated transfers "
              f"({len(dedup)} unique so far)")
    if not dedup:
        print(f"[sales:{market}] stack yielded 0 rows despite non-zero "
              f"counts - key spellings may not match _STACK_ID_KEYS/"
              f"_STACK_DATE_KEYS (BREAK, not done). NOT deleting existing "
              f"rows.")
        return 0
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    tag = cfg["source_tag"]
    rows = [(market, cfg["state"], cfg["county"], "sales", tag, now_iso, js)
            for _, js in dedup.values()]
    _replace_rows(conn, tag, rows)
    print(f"[sales:{market}] wrote {len(rows)} unique (gpin,date) sale rows")
    return len(rows)


def _landbook_frames(cfg: dict):
    import pandas as pd
    for label, url in cfg["items"]:
        try:
            r = requests.get(url, headers=UA, timeout=120)
            if r.status_code != 200 or not r.content:
                print(f"[sales:Chesapeake] {label} download failed "
                      f"(HTTP {r.status_code})")
                continue
        except requests.RequestException as exc:
            print(f"[sales:Chesapeake] {label} download failed ({exc!r})")
            continue
        df = None
        for engine in ("openpyxl", "xlrd"):
            try:
                df = pd.read_excel(io.BytesIO(r.content), engine=engine)
                break
            except Exception:
                continue
        if df is None:
            print(f"[sales:Chesapeake] {label}: could not parse workbook")
            continue
        yield label, df


def _pull_landbook(conn, market: str, cfg: dict) -> int:
    """Chesapeake: assessor LandBook XLS (annual) - one row per parcel with
    CONSIDERATION/TRANSFER DATE/CURRENTOWNER; MAP_PARCEL joins to parcels
    (phase0 aliases it to apn). Date-bearing rows only."""
    all_rows: list[tuple] = []
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    tag = cfg["source_tag"]
    for label, df in _landbook_frames(cfg):
        # SIZE FIRST (post-parse: the download can't be sized ahead).
        print(f"[sales:{market}] {label} LandBook: {len(df)} rows parsed")
        kept = 0
        date_cols = [c for c in df.columns
                     if "transfer" in str(c).lower() and "date" in str(c).lower()] \
            or [c for c in df.columns if str(c).strip().lower() == "transfer"]
        for rec in df.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                cv = _clean_value(v)
                if cv not in (None, ""):
                    clean[str(k).strip()] = cv
            if not clean:
                continue
            has_date = any(clean.get(str(c).strip()) for c in date_cols)
            if not has_date:
                continue                 # never sold - nothing to index
            clean["_landbook"] = label
            all_rows.append((market, cfg["state"], cfg["county"], "sales",
                             tag, now_iso, json.dumps(clean)))
            kept += 1
        print(f"[sales:{market}] {label}: kept {kept} date-bearing rows")
    if not all_rows:
        print(f"[sales:{market}] LandBook yielded 0 rows - NOT deleting "
              f"existing rows (transient?)")
        return 0
    _replace_rows(conn, tag, all_rows)
    print(f"[sales:{market}] wrote {len(all_rows)} LandBook sale rows")
    return len(all_rows)


_ADAPTERS = {
    "arcgis": _pull_arcgis,
    "socrata_stack": _pull_socrata_stack,
    "landbook_xlsx": _pull_landbook,
}


def pull_market(conn: sqlite3.Connection, market: str, cfg: dict) -> int:
    tag = cfg.get("source_tag") or cfg.get("url")
    if _source_fresh(conn, tag, _refresh_cutoff(cfg)):
        print(f"[sales:{market}] fresh (<{cfg.get('refresh_d', 7)}d) - skip")
        return 0
    return _ADAPTERS[cfg["type"]](conn, market, cfg)


def main(argv: list[str] | None = None) -> int:
    only = {m.strip() for m in
            os.environ.get("ER_ARCGIS_SALES_MARKETS", "").split(",") if m.strip()}
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("[sales] no workbench.db - skipping")
        return 0
    conn = sqlite3.connect(db)
    try:
        wrote = 0
        for market, cfg in SALES_SOURCES.items():
            if only and market not in only:
                continue
            try:
                wrote += pull_market(conn, market, cfg)
            except Exception as exc:          # one market never sinks the rest
                print(f"[sales:{market}] ERROR {exc!r}")
        print(f"[sales] done: {wrote} rows across {len(SALES_SOURCES)} "
              f"source(s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
