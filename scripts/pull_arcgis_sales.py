"""Pull municipal Property Sales from ArcGIS Hub FeatureServers into
muni_records as kind='sales' (owner-confirmed source, 2026-08-10).

Virginia Beach's sale/deed history is NOT on Spatialest (that vendor 404s every
sales route - see reports/vb-sales-probe.txt). The VB Real Estate Assessor
publishes the full Property Sales dataset - every recorded sale/transfer - on
the city's ArcGIS Hub open-data portal, on the SAME AGOL org
(services2.arcgis.com/CyVvlIiUfRBmMQuu) we already query for VB parcels. It is a
standard Esri FeatureServer: no key, no auth, 2,000 records/page via
resultOffset. Norfolk and Chesapeake run similar ArcGIS Hub portals - add their
verified layer URLs to ARCGIS_SALES_FEEDS as they're confirmed (never pull blind
against an unverified layer).

Schema (VB Property_Sales_): GPIN, Street_Address, City, Zip_Code, Neighborhood,
Land_Value, Improvement_Value, Total_Value, Sale_Price, Document_Number,
Deed_Book, Deed_Page, Sales_Date (epoch ms).

Why no field mapping: core.sale_history.extract_sale_records already recognizes
these spellings (Sale_Price -> saleprice, Sales_Date -> salesdate with built-in
ArcGIS epoch-ms handling, Deed_Book/Deed_Page), and phase0.normalize_record maps
GPIN -> apn. So each feature's attributes are stored VERBATIM as the record JSON
and core.sale_index picks them up next cycle - zero further wiring.

Design (host-only; the build env is firewalled from ArcGIS):
  * SIZE THE PULL FIRST (returnCountOnly) so the run logs the expected count
    before paginating - a silent 0 or a truncated pull can never look "done".
  * Arm's-length only: WHERE Sale_Price > 0 AND Sales_Date >= DATE 'SINCE-01-01'
    ($0 deed transfers are common and non-arm's-length).
  * Full refresh per source: delete this source's rows, then bulk insert. The
    dataset lags ~2 weeks and changes slowly, so a periodic full refresh (gated
    to ER_ARCGIS_SALES_REFRESH_D days) is simplest and always consistent.

Env:
  ER_ARCGIS_SALES_SINCE_YEAR   earliest sale year to pull (default 2021)
  ER_ARCGIS_SALES_REFRESH_D    re-pull a source only if older than N days (7)
  ER_ARCGIS_SALES_MARKETS      comma list to restrict (default = all in registry)
  ER_ARCGIS_SALES_MAX          safety cap on records per market (default 400000)
"""

from __future__ import annotations

import datetime as dt
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

# Confirmed ArcGIS Hub Property-Sales FeatureServers. Add a market ONLY once its
# layer URL is verified (owner F12 / ArcGIS Hub) - an unverified URL would pull
# blind or silently write nothing.
ARCGIS_SALES_FEEDS: dict[str, dict[str, str]] = {
    "Virginia Beach": {
        "url": ("https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/"
                "services/Property_Sales_/FeatureServer/0"),
        "state": "VA",
        "county": "Virginia Beach",
    },
    # Norfolk / Chesapeake: same ArcGIS Hub pattern - add verified layer URLs.
}

PAGE = 2000                              # ArcGIS Hub default max per page
SINCE_YEAR = int(os.environ.get("ER_ARCGIS_SALES_SINCE_YEAR", "2021"))
REFRESH_D = int(os.environ.get("ER_ARCGIS_SALES_REFRESH_D", "7"))
MAX_RECORDS = int(os.environ.get("ER_ARCGIS_SALES_MAX", "400000"))
UA = {"User-Agent": "EightRockWorkbench/1.0 (contact bmccune@gmail.com)",
      "Accept": "application/json"}
# Arm's-length, recent. Sale_Price>0 drops $0 deed transfers; the date bound
# reproduces the owner's verified count (VB: 47,631 sales since 2021).
WHERE = f"Sale_Price > 0 AND Sales_Date >= DATE '{SINCE_YEAR}-01-01'"


def _query(url: str, params: dict) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(url + "/query", params=params, headers=UA, timeout=40)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def count_sales(url: str) -> int | None:
    """Size the pull BEFORE paginating (owner directive 2026-08-10)."""
    js = _query(url, {"where": WHERE, "returnCountOnly": "true", "f": "json"})
    if js is None or "count" not in js:
        return None
    return int(js["count"])


def iter_features(url: str, expected: int):
    """Yield attribute dicts, paginating by resultOffset. Stops when a page
    returns fewer than PAGE rows (or the safety cap is hit)."""
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
        time.sleep(0.3)                  # politeness between pages


def _source_fresh(conn, source_url: str, cutoff_iso: str) -> bool:
    row = conn.execute(
        "SELECT max(pulled_at) FROM muni_records "
        "WHERE kind='sales' AND source_url=?", (source_url,)).fetchone()
    return bool(row and row[0] and row[0] >= cutoff_iso)


def pull_market(conn: sqlite3.Connection, market: str, cfg: dict) -> int:
    url = cfg["url"]
    state = cfg.get("state", "VA")
    county = cfg.get("county", market)
    cutoff = (dt.datetime.now() - dt.timedelta(days=REFRESH_D)).isoformat()
    if _source_fresh(conn, url, cutoff):
        print(f"[arcgis-sales] {market}: fresh (<{REFRESH_D}d) - skip")
        return 0

    total = count_sales(url)
    if total is None:
        print(f"[arcgis-sales] {market}: count query FAILED (endpoint/where?) "
              f"- skip, no rows touched")
        return 0
    print(f"[arcgis-sales] {market}: {total} arm's-length sales since "
          f"{SINCE_YEAR} to pull")
    if total == 0:
        return 0

    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    rows = []
    for attrs in iter_features(url, total):
        rows.append((market, state, county, "sales", url, now_iso,
                     json.dumps(attrs)))
    if not rows:
        print(f"[arcgis-sales] {market}: expected {total} but paginated 0 - "
              f"NOT deleting existing rows (transient?)")
        return 0

    with conn:
        conn.execute("DELETE FROM muni_records WHERE kind='sales' AND "
                     "source_url=?", (url,))
        conn.executemany(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "pulled_at,record) VALUES (?,?,?,?,?,?,?)", rows)
    print(f"[arcgis-sales] {market}: wrote {len(rows)} sale rows "
          f"(expected {total})")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    only = {m.strip() for m in
            os.environ.get("ER_ARCGIS_SALES_MARKETS", "").split(",") if m.strip()}
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("[arcgis-sales] no workbench.db - skipping")
        return 0
    conn = sqlite3.connect(db)
    try:
        wrote = 0
        for market, cfg in ARCGIS_SALES_FEEDS.items():
            if only and market not in only:
                continue
            try:
                wrote += pull_market(conn, market, cfg)
            except Exception as exc:          # one market never sinks the rest
                print(f"[arcgis-sales] {market}: ERROR {exc!r}")
        print(f"[arcgis-sales] done: {wrote} rows across "
              f"{len(ARCGIS_SALES_FEEDS)} source(s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
