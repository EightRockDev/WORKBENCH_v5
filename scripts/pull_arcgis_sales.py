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
import re
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
# Browser-like UA on purpose: data.richmondgov.com 403'd the first host run
# (2026-08-11) while data.norfolk.gov served the same code fine - Tyler/
# Socrata domains can sit behind bot filtering, and a real-browser UA is the
# same lever that cleared Spatialest. ER_SOCRATA_APP_TOKEN (free at
# evergreen.data.socrata.com) rides along as X-App-Token when set - the other
# way these domains gate API reads.
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) "
                   "Gecko/20100101 Firefox/153.0"),
    "Accept": "application/json, text/plain, */*",
}


def _headers() -> dict:
    h = dict(UA)
    tok = os.environ.get("ER_SOCRATA_APP_TOKEN", "").strip()
    if tok:
        h["X-App-Token"] = tok
    return h


# Last failure detail per _get_json call - so a skip line can say WHY
# (HTTP 403 vs 404 vs timeout), instead of a blind "count query failed".
_LAST_ERR = ""

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
    # Richmond Socrata (uxre-by3i/k9h9-y482) RETIRED 2026-08-11: the domain
    # 403s API reads, and the rva.gov files path below already delivers the
    # same transfers (84k sale rows through 2026-05, verified in
    # richmond-review). Owner directive: research easier methods instead of
    # making a difficult source work - so no token chase; if Socrata is ever
    # wanted again, discover_sales_feeds.py scores it from the central
    # catalog without touching the gated domain.
    # Richmond PATH 2 (owner 2026-08-11 "Not review. I want it done."): the
    # Assessor's OWN monthly files on rva.gov - a different domain than the
    # 403-risk data.richmondgov.com, so the two paths fail independently.
    # The page hosts a 3-file Public Data Set (parcel/land+building/ownership
    # + assessment history) and a market-transfers workbook, refreshed ~15th
    # monthly with changing URLs - so the adapter scrapes the page for
    # spreadsheet links each run instead of hardcoding any.
    # Richmond PATH 3 (owner 2026-08-11 "you should [use an API]"): the
    # "Richmond-parcels" REMOVED 2026-09-02. The COR_Parcel_Ownership_WFL1
    # layer on AGOL org il6vO1TutlF580Ku is the City of Richmond,
    # CALIFORNIA - proven by its own raw records (FHSZ, a Cal-Fire fire
    # hazard field; Contra Costa 9-digit APNs; N_CTY_ST mailing cities in
    # Illinois). It was added 2026-08-11 as "the Richmond GeoHub" on the
    # strength of its NAME, ingested 32,907 California parcels under
    # market='Richmond' VA, and its 2,365 apartment unit counts were
    # chased for three weeks as the missing Richmond VA unit source.
    # QUARANTINED_SOURCES below purges its rows every run.
    "Richmond-files": {
        "type": "html_files",
        "page": "https://www.rva.gov/assessor-real-estate/data-request",
        "market": "Richmond",
        "state": "VA", "county": "Richmond",
        "source_tag": "files:rva.gov/assessor-real-estate",
        # 5 (not 7) so the 2026-09-03 landing-page fix re-pulls on the
        # NEXT cycle instead of waiting out the last stamp; the files
        # update monthly (~the 15th), so 5d vs 7d costs nothing.
        "refresh_d": 5,
    },
    # Richmond VA unit counts via the city's master address table - one
    # row per address INCLUDING one per apartment unit, PIN-stamped in
    # the assessor's own letter-PIN format. See _pull_arcgis_unit_rollup
    # for the verification trail and the in-code geography gate.
    "Richmond-units": {
        "type": "arcgis_unit_rollup",
        "url": ("https://services1.arcgis.com/k3vhq11XkBNeeOfM/arcgis/"
                "rest/services/Addresses/FeatureServer/0"),
        "market": "Richmond", "kind": "assessor",
        "state": "VA", "county": "Richmond",
        "where": "UnitValue IS NOT NULL",
        "pin_field": "PIN", "subaddress_field": "SubaddressID",
        "unit_type_field": "UnitType", "unit_value_field": "UnitValue",
        "lat_field": "Latitude", "lng_field": "Longitude",
        # The live Addresses layer carries NO parcel id (the 2018
        # snapshot's PIN column is gone) - unit points are assigned to
        # parcels by point-in-polygon against the city's own Parcels
        # layer (same org, PIN + polygons).
        "parcel_layer_url": ("https://services1.arcgis.com/"
                             "k3vhq11XkBNeeOfM/arcgis/rest/services/"
                             "Parcels/FeatureServer/0"),
        "parcel_pin_field": "PIN",
        # Richmond VIRGINIA city bbox - parcels averaging outside it
        # mean the wrong city and the pull refuses to write.
        "bbox": (37.40, 37.65, -77.65, -77.30),
        "refresh_d": 7,
    },
    # Hottest-50 Wave 1 begins (owner "do all of them" 2026-08-11). Chicago =
    # Cook County Assessor "Parcel Sales" (wvhk-k5uv) - TRANSACTION-level full
    # sale history per PIN, the dataset the Assessor's own models publish.
    # County-wide it's ~1.5M rows, so the SoQL filter mirrors VB's
    # arm's-length-since-2021 stance to fit the per-market safety cap.
    "Chicago": {
        "type": "socrata_stack",
        "base": "https://datacatalog.cookcountyil.gov/resource/",
        "resources": (("sales", "wvhk-k5uv"),),
        "soql_where": ("sale_price > 0 AND "
                       f"sale_date >= '{SINCE_YEAR}-01-01T00:00:00'"),
        "state": "IL", "county": "Cook",
        "source_tag": "socrata:datacatalog.cookcountyil.gov/wvhk-k5uv",
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
    global _LAST_ERR
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_headers(), timeout=60)
            if r.status_code == 200:
                _LAST_ERR = ""
                try:
                    return r.json()
                except ValueError:
                    _LAST_ERR = "HTTP 200 but non-JSON body"
                    return None
            _LAST_ERR = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            _LAST_ERR = repr(exc)
        time.sleep(1.5 * (attempt + 1))
    return None


def _query(url: str, params: dict) -> dict | None:
    """Esri layer query (kept as a seam for tests)."""
    return _get_json(url + "/query", params)


def _source_fresh(conn, source_url: str, cutoff_iso: str) -> bool:
    # No kind filter: assessor-kind feeds (the Richmond COR roll) stamp
    # their rows kind='assessor', and a kind='sales' lookup never saw them -
    # the roll re-downloaded all its rows every cycle (2026-08-11).
    row = conn.execute(
        "SELECT max(pulled_at) FROM muni_records WHERE source_url=?",
        (source_url,)).fetchone()
    return bool(row and row[0] and row[0] >= cutoff_iso)


def _refresh_cutoff(cfg: dict) -> str:
    days = int(os.environ.get("ER_ARCGIS_SALES_REFRESH_D",
                              str(cfg.get("refresh_d", 7))))
    return (dt.datetime.now() - dt.timedelta(days=days)).isoformat()


def _replace_rows(conn, source_tag: str, rows: list[tuple],
                  kind: str = "sales") -> None:
    # kind must match what the rows carry: deleting kind='sales' before
    # inserting kind='assessor' rows removes nothing, so every pull APPENDED
    # a full copy of the feed (COR roll: 32,907 -> 65,814 in one day).
    with conn:
        conn.execute("DELETE FROM muni_records WHERE kind=? AND "
                     "source_url=?", (kind, source_tag))
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

def count_sales(url: str, where: str = "") -> int | None:
    """Esri: size the pull BEFORE paginating (owner directive)."""
    js = _query(url, {"where": where or WHERE, "returnCountOnly": "true",
                      "f": "json"})
    if js is None or "count" not in js:
        return None
    return int(js["count"])


def iter_features(url: str, expected: int, where: str = ""):
    offset = 0
    while offset < min(expected, MAX_RECORDS):
        js = _query(url, {
            "where": where or WHERE, "outFields": "*",
            "returnGeometry": "false",
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
    market = cfg.get("market", market)   # dict key may be a variant label
    kind = cfg.get("kind", "sales")      # assessor layers reuse this adapter
    where = cfg.get("where", "")         # per-source filter; default = WHERE
    total = count_sales(url, where)
    if total is None:
        print(f"[sales:{market}] count query FAILED "
              f"({_LAST_ERR or 'endpoint/where?'}) - skip, no rows touched")
        return 0
    print(f"[sales:{market}] {total} {kind} rows to pull "
          f"(where: {where or WHERE})")
    if total == 0:
        return 0
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    rows = [(market, cfg["state"], cfg["county"], kind, url, now_iso,
             json.dumps(attrs)) for attrs in iter_features(url, total, where)]
    if not rows:
        print(f"[sales:{market}] expected {total} but paginated 0 - NOT "
              f"deleting existing rows (transient?)")
        return 0
    _replace_rows(conn, url, rows, kind)
    print(f"[sales:{market}] wrote {len(rows)} rows (expected {total})")
    return len(rows)


# ------------------------------------------- unit rollup (address table)
#
# Richmond VA's master address table (city AGOL org k3vhq11XkBNeeOfM -
# verified VIRGINIA: OpenAddresses us/va/city_of_richmond.json geoid
# 51760 harvests this exact layer; NOT the quarantined California org)
# carries one row per ADDRESS, including one per apartment unit, each row
# stamped with the parcel PIN in the assessor's own letter-PIN format.
# The assessor's bulk files publish no unit counts, so counting distinct
# unit-designated addresses per PIN is the city's own data answering the
# question sideways: 2018 snapshot spot-checks give PIN N0170390020 ->
# 1,051 units, W0000356014 -> 992.

# Unit-type designators that are NOT dwellings - suites, offices, docks.
# Rows with a blank type still count (residential units often carry only
# a bare unit number); these explicit commercial types do not.
_NON_DWELLING_UNIT_TYPES = {
    "ste", "suite", "rm", "room", "fl", "floor", "ofc", "office", "box",
    "trlr", "lot", "slip", "dock", "pier", "hangar", "bldg", "spc",
}


def _rollup_pin(pin: str) -> str:
    """The address table duplicates every subaddress under the base PIN
    and a 'T' variant (N0170390020 and N0170390020T) - fold the variant
    onto the base so counts don't double."""
    p = (pin or "").strip().upper()
    if len(p) > 11 and p.endswith("T") and p[:-1][-1].isdigit():
        return p[:-1]
    return p


# The configured field name is a starting guess - the live layer's real
# spelling wins. First host run (2026-09-03 22:30): the where clause
# matched 55,435 rows, so UnitValue was right, but every row was skipped
# because the PIN attribute is spelled differently than the 2018 snapshot
# said - and the log couldn't say what the real name was. Resolution is
# case/underscore-insensitive with per-role fallbacks, and a failed
# resolve prints the layer's ACTUAL attribute names.
_ROLLUP_FALLBACKS = {
    "pin_field": ("pin", "parcelpin", "parcelid", "parcel_id", "pinnumber",
                  "parcelnumber", "gpin", "pin1", "parceladdresspin"),
    "subaddress_field": ("subaddressid", "subaddress_id", "subaddrid",
                         "addresssubid", "siteaddid",
                         # live 2026 layer: no SubaddressID; the unit
                         # label works because identity is anchored to
                         # the point below. NEVER fall back to a raw row
                         # id (AddressId/OBJECTID) - exact duplicate
                         # rows carry different row ids and would count
                         # as two units.
                         "addresslabelwithunit", "extensionwithunit"),
    "unit_type_field": ("unittype", "unit_type", "subaddresstype",
                        "unitdesignator"),
    "unit_value_field": ("unitvalue", "unit_value", "unitnumber", "unitnum",
                         "subaddressvalue", "unitid"),
    "lat_field": ("latitude", "lat", "pointy", "point_y", "ycoord", "y"),
    "lng_field": ("longitude", "lng", "lon", "long", "pointx", "point_x",
                  "xcoord", "x"),
}


def _resolve_rollup_fields(cfg: dict, attrs: dict) -> dict:
    """Map each cfg role to the attribute name the layer really uses."""
    keymap = {k.replace("_", "").lower(): k for k in attrs}
    out = {}
    for role, fallbacks in _ROLLUP_FALLBACKS.items():
        for cand in (cfg.get(role, ""), *fallbacks):
            hit = keymap.get(str(cand).replace("_", "").lower())
            if hit:
                out[role] = hit
                break
        else:
            out[role] = None
    return out


def _iter_unit_features(url: str, expected: int, where: str,
                        out_fields: str = "*"):
    """Like iter_features but WITH geometry, forced to lat/lng (outSR
    4326) - hosted layers default to Web Mercator meters, and some carry
    no Latitude/Longitude attributes at all. Yields (attrs, geometry).

    Keeps paging until a page comes back EMPTY: a server whose
    maxRecordCount is smaller than PAGE returns short pages long before
    the data runs out, so 'short page = done' silently truncates."""
    offset = 0
    while offset < min(expected, MAX_RECORDS):
        js = _query(url, {
            "where": where, "outFields": out_fields,
            "returnGeometry": "true", "outSR": 4326,
            "resultOffset": offset, "resultRecordCount": PAGE, "f": "json"})
        feats = (js or {}).get("features") or []
        if not feats:
            break
        for f in feats:
            attrs = f.get("attributes")
            if isinstance(attrs, dict):
                yield attrs, f.get("geometry")
        offset += len(feats)
        time.sleep(0.3)


# ------------------------------ spatial join: points -> parcel polygons

_GRID_CELL = 0.001            # ~100m at Richmond's latitude


def _point_in_rings(lng: float, lat: float, rings) -> bool:
    """Even-odd ray cast over every ring, so holes subtract."""
    inside = False
    for ring in rings or ():
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > lat) != (yj > lat) and \
                    lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def _assign_pins_by_polygon(points: list, parcel_url: str,
                            pin_field: str, market: str) -> dict:
    """points: [(lng, lat, point_index), ...]. Streams the parcel layer's
    polygons ONE PAGE AT A TIME (a whole-city polygon set held at once is
    hundreds of MB) and ray-casts the grid-bucketed candidate points, so
    memory stays at one page of rings plus a small point index.
    Returns {point_index: pin}."""
    grid: dict[tuple, list] = {}
    for lng, lat, idx in points:
        grid.setdefault((int(lng / _GRID_CELL), int(lat / _GRID_CELL)),
                        []).append((lng, lat, idx))
    total = count_sales(parcel_url, "1=1")
    if total is None:
        print(f"[units:{market}] parcel layer count FAILED "
              f"({_LAST_ERR or 'endpoint?'}) - cannot spatially join")
        return {}
    print(f"[units:{market}] spatial join against {total} parcel "
          f"polygons ({parcel_url.rsplit('/services/', 1)[-1]})")
    assigned: dict[int, str] = {}
    polys = 0
    for attrs, geom in _iter_unit_features(parcel_url, total, "1=1",
                                           out_fields=pin_field):
        polys += 1
        pin = str((attrs or {}).get(pin_field) or "").strip().upper()
        rings = (geom or {}).get("rings")
        if not pin or not rings:
            continue
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        x0, x1 = int(min(xs) / _GRID_CELL), int(max(xs) / _GRID_CELL)
        y0, y1 = int(min(ys) / _GRID_CELL), int(max(ys) / _GRID_CELL)
        for cx in range(x0 - 1, x1 + 2):
            for cy in range(y0 - 1, y1 + 2):
                for lng, lat, idx in grid.get((cx, cy), ()):
                    if idx not in assigned and \
                            _point_in_rings(lng, lat, rings):
                        assigned[idx] = pin
    print(f"[units:{market}] spatial join: {len(assigned)}/{len(points)} "
          f"unit addresses landed in one of {polys} polygons")
    return assigned


def _pull_arcgis_unit_rollup(conn, market: str, cfg: dict) -> int:
    """Aggregate an address-point layer into one record per parcel PIN
    with the count of distinct dwelling-unit addresses on it. Writes
    kind='assessor' rows shaped for the normal phase0 aliases (PIN ->
    apn, UnitCount -> units, mean point -> Latitude/Longitude), so the
    spine joins them like any other assessor feed.

    California lesson (V5.66.1.0.0) enforced in code: after the pull,
    the mean coordinate of every parcel must sit inside the market's
    bbox or NOTHING is written."""
    url = cfg["url"]
    market = cfg.get("market", market)
    kind = cfg.get("kind", "assessor")
    where = cfg["where"]
    lat_lo, lat_hi, lng_lo, lng_hi = cfg["bbox"]

    total = count_sales(url, where)
    if total is None:
        print(f"[units:{market}] count query FAILED "
              f"({_LAST_ERR or 'endpoint/where?'}) - skip, no rows touched")
        return 0
    print(f"[units:{market}] {total} unit-address rows to roll up "
          f"(where: {where})")
    if total == 0:
        return 0

    kept: list[tuple] = []   # (pin_or_None, sub_identity, lat, lng)
    skipped_type = 0
    fetched = 0
    fields: dict | None = None
    first_keys: list[str] = []
    spatial = False
    for attrs, geom in _iter_unit_features(url, total, where):
        fetched += 1
        if fields is None:
            fields = _resolve_rollup_fields(cfg, attrs)
            first_keys = sorted(attrs)
            print(f"[units:{market}] resolved fields: "
                  + ", ".join(f"{r.split('_')[0]}={fields[r]}"
                              for r in sorted(fields)))
            spatial = fields["pin_field"] is None
            if spatial and not cfg.get("parcel_layer_url"):
                print(f"[units:{market}] NO PIN-like attribute and no "
                      f"parcel_layer_url to join against - REFUSING. "
                      f"Actual attributes: " + ", ".join(first_keys))
                return 0
            if spatial:
                # The city dropped PIN from the live layer (2026-09-04
                # host run proved it) - points must land in parcel
                # polygons instead.
                print(f"[units:{market}] no PIN attribute - will assign "
                      f"parcels by point-in-polygon")
        val = str(attrs.get(fields["unit_value_field"]) or "").strip() \
            if fields["unit_value_field"] else ""
        if not val:
            continue
        utype = ""
        if fields["unit_type_field"]:
            utype = str(attrs.get(fields["unit_type_field"]) or "") \
                .strip().lower().rstrip(".")
        if utype in _NON_DWELLING_UNIT_TYPES:
            skipped_type += 1
            continue
        pin = None
        if not spatial:
            pin = _rollup_pin(str(attrs.get(fields["pin_field"]) or ""))
            if not pin:
                continue
        la = ln = None
        if fields["lat_field"] and fields["lng_field"]:
            la, ln = attrs.get(fields["lat_field"]), \
                attrs.get(fields["lng_field"])
        if (la in (None, "") or ln in (None, "")) and isinstance(geom, dict):
            la, ln = geom.get("y"), geom.get("x")
        try:
            la, ln = float(la), float(ln)
        except (TypeError, ValueError):
            la = ln = None
            if spatial:
                continue          # no coordinate = nothing to join on
        sub = attrs.get(fields["subaddress_field"]) \
            if fields["subaddress_field"] else None
        if sub in (None, ""):
            sub = f"{utype}|{val}"
        # Identity is (designator, point): a bare "Apt 101" repeats in
        # every building of a complex (anchoring to the point keeps them
        # distinct), while an exact duplicate row shares its point (so
        # it still collapses to one unit).
        if la is not None:
            sub = f"{sub}|{la:.6f},{ln:.6f}"
        kept.append((pin, str(sub), la, ln))

    pinmap: dict[int, str] = {}
    unmatched = 0
    if spatial and kept:
        pts = [(ln, la, i) for i, (_pin, _s, la, ln) in enumerate(kept)
               if la is not None]
        pinmap = _assign_pins_by_polygon(
            pts, cfg["parcel_layer_url"],
            cfg.get("parcel_pin_field", "PIN"), market)
        if not pinmap:
            print(f"[units:{market}] spatial join assigned 0 parcels - "
                  f"NOT deleting existing rows")
            return 0

    per_pin: dict[str, dict] = {}
    for i, (pin, sub, la, ln) in enumerate(kept):
        pin = pin or pinmap.get(i)
        if not pin:
            unmatched += 1
            continue
        pin = _rollup_pin(pin)
        d = per_pin.setdefault(pin, {"subs": set(), "lat": 0.0,
                                     "lng": 0.0, "pts": 0})
        d["subs"].add(sub)
        if la is not None:
            d["lat"] += la
            d["lng"] += ln
            d["pts"] += 1
    if unmatched:
        print(f"[units:{market}] {unmatched} unit addresses matched no "
              f"parcel polygon (kept out of the counts)")
    if not per_pin:
        print(f"[units:{market}] rolled up 0 parcels from {fetched} "
              f"fetched rows (expected {total}) - NOT deleting existing "
              f"rows. "
              + (f"Last page error: {_LAST_ERR}" if not fetched else
                 "Layer attributes: " + ", ".join(first_keys)))
        return 0

    # Geography gate BEFORE any write. A wrong-city feed passed a name
    # check once; a coordinate check would have caught it on day one.
    located = [d for d in per_pin.values() if d["pts"]]
    if not located:
        print(f"[units:{market}] no row carried a coordinate - REFUSING "
              f"to write (cannot prove the layer's geography)")
        return 0
    inside = sum(1 for d in located
                 if lat_lo <= d["lat"] / d["pts"] <= lat_hi
                 and lng_lo <= d["lng"] / d["pts"] <= lng_hi)
    share = inside / len(located)
    if share < 0.95:
        print(f"[units:{market}] GEOGRAPHY CHECK FAILED: only "
              f"{share:.0%} of parcels fall inside the {market} bbox - "
              f"REFUSING to write (wrong city? projection?)")
        return 0

    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    rows = []
    for pin, d in per_pin.items():
        rec = {"PIN": pin, "UnitCount": len(d["subs"]),
               "_derived": "distinct dwelling-unit addresses in the city "
                           "master address table"}
        if d["pts"]:
            rec["Latitude"] = round(d["lat"] / d["pts"], 6)
            rec["Longitude"] = round(d["lng"] / d["pts"], 6)
        rows.append((market, cfg["state"], cfg["county"], kind, url,
                     now_iso, json.dumps(rec)))
    _replace_rows(conn, url, rows, kind)
    mf = sum(1 for pin, d in per_pin.items() if len(d["subs"]) >= 10)
    print(f"[units:{market}] wrote {len(rows)} parcels "
          f"({mf} with >=10 units; {skipped_type} non-dwelling rows "
          f"skipped; geography {share:.0%} inside bbox)")
    return len(rows)


def _socrata_count(base: str, rid: str, where: str = "") -> int | None:
    params = {"$select": "count(*)"}
    if where:
        params["$where"] = where
    js = _get_json(f"{base}{rid}.json", params)
    if isinstance(js, list) and js:
        for v in js[0].values():
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def _socrata_rows(base: str, rid: str, where: str = ""):
    offset = 0
    while offset < MAX_RECORDS:
        params = {"$limit": SOCRATA_PAGE, "$offset": offset}
        if where:
            params["$where"] = where
        js = _get_json(f"{base}{rid}.json", params)
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
    soql_where = cfg.get("soql_where", "")
    # SIZE FIRST across the whole stack.
    sized = []
    for fy, rid in cfg["resources"]:
        n = _socrata_count(base, rid, soql_where)
        if n is None:
            print(f"[sales:{market}] {fy} ({rid}) count failed: "
                  f"{_LAST_ERR or 'no response'}")
        sized.append((fy, rid, n))
    known = [(fy, n) for fy, _, n in sized if n is not None]
    if not known:
        print(f"[sales:{market}] every count query failed (see per-resource "
              f"detail above; HTTP 403 = domain wants an app token - set "
              f"ER_SOCRATA_APP_TOKEN, free at evergreen.data.socrata.com) - "
              f"skip, no rows touched")
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
        for raw in _socrata_rows(base, rid, soql_where):
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


# Anchor tags with their inner text. rva.gov (Drupal) links files as
# extension-less /media/<id> URLs - first contact 2026-08-11 midnight cycle:
# HTTP 200 but 0 links matched the old .xlsx-only href regex. So capture
# EVERY anchor, filter by href shape (spreadsheet extension, /media/<id>, or
# /sites/default/files/), and keep the link text for kind classification.
_ANCHOR = re.compile(
    r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_FILEISH = re.compile(
    r"(\.(?:xlsx|xls|csv)(?:$|\?))|(/media/\d+)|(/sites/default/files/)",
    re.IGNORECASE)


def _list_file_links(html: str, page_url: str) -> list[tuple[str, str]]:
    """(url, link_text) pairs for file-ish links, absolutized, deduped."""
    from urllib.parse import urljoin
    out, seen = [], set()
    for m in _ANCHOR.finditer(html or ""):
        href = m.group(1)
        if not _FILEISH.search(href):
            continue
        u = urljoin(page_url, href)
        if u in seen:
            continue
        seen.add(u)
        label = _TAGS.sub("", m.group(2)).strip()
        out.append((u, label))
    return out


def _file_kind(url: str, label: str = "") -> str:
    """transfers/sales workbooks -> kind='sales'; everything else on an
    assessor page is parcel data -> kind='assessor'. /media/<id> URLs carry
    no filename, so the anchor TEXT is the classification signal there."""
    hay = f"{url.rsplit('/', 1)[-1]} {label}".lower()
    return "sales" if ("transfer" in hay or "sale" in hay) else "assessor"


def _read_workbook(content: bytes):
    """All tabs of a spreadsheet as ONE DataFrame (rva.gov puts each year on
    its own tab), or None. Format sniffed from magic bytes, NOT the URL -
    /media/<id> links have no extension (PK.. = xlsx, D0 CF = legacy xls,
    else try CSV)."""
    import pandas as pd
    if content[:4] == b"PK\x03\x04":
        engines = ("openpyxl",)
    elif content[:4] == b"\xd0\xcf\x11\xe0":
        engines = ("xlrd",)
    else:
        try:
            return pd.read_csv(io.BytesIO(content))
        except Exception:
            engines = ("openpyxl", "xlrd")   # last resort: header lied
    for engine in engines:
        try:
            sheets = pd.read_excel(io.BytesIO(content), engine=engine,
                                   sheet_name=None)
            frames = [df for df in sheets.values() if len(df)]
            if not frames:
                return None
            return pd.concat(frames, ignore_index=True) if len(frames) > 1 \
                else frames[0]
        except Exception:
            continue
    return None


def _download_tables(url: str, _depth: int = 0) -> list[tuple]:
    """[(DataFrame, final_url), ...] for a remote spreadsheet URL. Drupal
    /media/<id> URLs (2 AM ET first contact) return an HTML LANDING PAGE,
    not the file - when the body is HTML, follow EVERY spreadsheet-ish
    link one level down. The first version returned only the FIRST file
    that parsed, which is how Richmond's 3-file Public Data Set silently
    lost its building-characteristics file - and with it every Richmond
    unit count (found 2026-09-03). final_url is the real file, whose name
    carries the transfer/assessor classification signal the /media/ URL
    lacks."""
    import pandas as pd  # noqa: F401  (re-exported for the helpers)
    try:
        r = requests.get(url, headers=_headers(), timeout=180,
                         allow_redirects=True)
        if r.status_code != 200 or not r.content:
            return []
    except requests.RequestException:
        return []
    content = r.content
    head = content[:512].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html")) or b"<html" in head:
        if _depth >= 1:
            return []
        sub = _list_file_links(content.decode("utf-8", "replace"), str(r.url))
        if len(sub) > 12:
            print(f"    [files] landing page lists {len(sub)} files - "
                  f"loading the first 12, IGNORING {len(sub) - 12}")
        out: list[tuple] = []
        for u, _label in sub[:12]:
            if u.rstrip("/") == url.rstrip("/"):
                continue                       # self-link, avoid a loop
            out.extend(_download_tables(u, _depth + 1))
        return out
    df = _read_workbook(content)
    return [(df, str(r.url))] if df is not None else []


def _pull_html_files(conn, market_key: str, cfg: dict) -> int:
    """Scrape an assessor downloads page for spreadsheet links and load each:
    transfers -> kind='sales', parcel data -> kind='assessor'. Links change
    monthly, so nothing is hardcoded; the sized per-file log is the proof."""
    market = cfg.get("market", market_key)
    tag = cfg["source_tag"]
    try:
        r = requests.get(cfg["page"], headers=_headers(), timeout=60)
        html = r.text if r.status_code == 200 else ""
        status = r.status_code
    except requests.RequestException as exc:
        html, status = "", repr(exc)
    links = _list_file_links(html, cfg["page"])
    print(f"[sales:{market}] {cfg['page']} -> HTTP {status}, "
          f"{len(links)} spreadsheet link(s)")
    if not links:
        print(f"[sales:{market}] no file links found - skip, no rows touched")
        return 0

    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []
    per_kind = {"sales": 0, "assessor": 0}
    seen_files: set[str] = set()     # two anchors can land on one file
    if len(links) > 12:
        print(f"[sales:{market}] page lists {len(links)} links - loading "
              f"the first 12, IGNORING {len(links) - 12}")
    for url, label in links[:12]:        # sanity cap on a scraped page
        tables = _download_tables(url)
        name = label or url.rsplit("/", 1)[-1]
        if not tables:
            print(f"[sales:{market}]   {name[:60]}: download/parse FAILED "
                  f"({url})")
            continue
        for df, final_url in tables:
            if final_url in seen_files:
                continue
            seen_files.add(final_url)
            fname = final_url.rsplit("/", 1)[-1] or name
            # Classify on the RESOLVED filename + anchor text - the
            # /media/<id> link itself says nothing, the file it lands on
            # does.
            kind = _file_kind(final_url, label)
            kept = 0
            for rec in df.to_dict(orient="records"):
                clean = {}
                for k, v in rec.items():
                    cv = _clean_value(v)
                    if cv not in (None, ""):
                        clean[str(k).strip()] = cv
                if not clean:
                    continue
                clean["_file"] = final_url
                rows.append((market, cfg["state"], cfg["county"], kind, tag,
                             now_iso, json.dumps(clean, default=str)))
                kept += 1
                if len(rows) >= MAX_RECORDS:
                    break
            per_kind[kind] = per_kind.get(kind, 0) + kept
            print(f"[sales:{market}]   {name[:40]} -> {fname[:50]}: "
                  f"{kept} rows -> kind={kind}")
    if not rows:
        print(f"[sales:{market}] every file failed to parse - NOT deleting "
              f"existing rows (transient?)")
        return 0
    with conn:
        conn.execute("DELETE FROM muni_records WHERE source_url=?", (tag,))
        conn.executemany(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "pulled_at,record) VALUES (?,?,?,?,?,?,?)", rows)
    print(f"[sales:{market}] wrote {len(rows)} rows "
          f"(sales={per_kind.get('sales', 0)}, "
          f"assessor={per_kind.get('assessor', 0)})")
    return len(rows)


def _tabular_frames(files, market: str):
    """Download (label, url) files and parse each to a DataFrame - CSV or
    Excel decided by content, not extension (state-portal mirror URLs are
    often extension-less resource ids)."""
    import pandas as pd
    for label, url in files:
        try:
            r = requests.get(url, headers=UA, timeout=120)
            if r.status_code != 200 or not r.content:
                print(f"[sales:{market}] {label} download failed "
                      f"(HTTP {r.status_code})")
                continue
        except requests.RequestException as exc:
            print(f"[sales:{market}] {label} download failed ({exc!r})")
            continue
        df = None
        if r.content[:2] == b"PK" or r.content[:4] == b"\xd0\xcf\x11\xe0":
            for engine in ("openpyxl", "xlrd"):
                try:
                    df = pd.read_excel(io.BytesIO(r.content), engine=engine)
                    break
                except Exception:
                    continue
        else:
            try:
                df = pd.read_csv(io.BytesIO(r.content), dtype=str,
                                 encoding_errors="replace", low_memory=False)
            except Exception as exc:
                print(f"[sales:{market}] {label}: CSV parse failed ({exc!r})")
        if df is None:
            print(f"[sales:{market}] {label}: could not parse file")
            continue
        yield label, df


def _pull_csv_download(conn, market: str, cfg: dict) -> int:
    """Generic tabular-file source - the adapter for discover_sales_feeds'
    'csv_download' candidates (e.g. data.virginia.gov CKAN mirrors, which
    serve locality files from the state domain with no bot filtering).

    cfg:
      files       ((label, url), ...) oldest first - on a dedupe collision
                  the later file's row wins (Norfolk FY-stack semantics)
      kind        'sales' (default) or 'assessor'
      dedupe      True to dedupe on the probed (parcel-id, date) keys
    Same safety rails as every adapter here: size logged per file before
    write, transient empty never deletes existing rows.
    """
    tag = cfg["source_tag"]
    kind = cfg.get("kind", "sales")
    mkt = cfg.get("market", market)
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    cleaned: list[dict] = []
    for label, df in _tabular_frames(cfg["files"], mkt):
        print(f"[sales:{mkt}] {label}: {len(df)} rows parsed")
        for rec in df.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                cv = _clean_value(v)
                if cv not in (None, ""):
                    clean[str(k).strip()] = cv
            if clean:
                clean["_file"] = label
                cleaned.append(clean)
    if cfg.get("dedupe") and cleaned:
        id_key = next((k for k in _STACK_ID_KEYS
                       if any(k in {c.lower() for c in r} for r in cleaned[:50])),
                      None)
        date_key = next((k for k in _STACK_DATE_KEYS
                         if any(k in {c.lower() for c in r} for r in cleaned[:50])),
                        None)
        if id_key and date_key:
            def _kv(r, want):
                for c, v in r.items():
                    if c.lower() == want:
                        return str(v)
                return ""
            by_key = {}
            for r in cleaned:            # later files overwrite earlier
                by_key[(_kv(r, id_key), _kv(r, date_key))] = r
            print(f"[sales:{mkt}] dedupe on ({id_key}, {date_key}): "
                  f"{len(cleaned)} -> {len(by_key)}")
            cleaned = list(by_key.values())
        else:
            print(f"[sales:{mkt}] dedupe requested but no id/date columns "
                  "probed - keeping all rows")
    if not cleaned:
        print(f"[sales:{mkt}] files yielded 0 rows - NOT deleting existing "
              "rows (transient?)")
        return 0
    rows = [(mkt, cfg["state"], cfg["county"], kind, tag, now_iso,
             json.dumps(r)) for r in cleaned]
    _replace_rows(conn, tag, rows, kind)
    print(f"[sales:{mkt}] wrote {len(rows)} rows from "
          f"{len(cfg['files'])} file(s)")
    return len(rows)


_ADAPTERS = {
    "arcgis": _pull_arcgis,
    "arcgis_unit_rollup": _pull_arcgis_unit_rollup,
    "socrata_stack": _pull_socrata_stack,
    "landbook_xlsx": _pull_landbook,
    "html_files": _pull_html_files,
    "csv_download": _pull_csv_download,
}


def _sweep_stale_generations(conn, tag: str) -> None:
    """Drop rows older than a source's newest write, per kind.

    One `_replace_rows` write is one generation (a single pulled_at stamp).
    The kind='sales' delete bug left older generations behind for
    assessor-kind feeds (the COR roll doubled to 65,814 rows); this runs
    even on fresh-skip cycles so those copies drain without waiting out
    the refresh window.
    """
    # Two steps on purpose. The first version was one DELETE with a
    # correlated max(pulled_at) subquery - on the host's unindexed
    # million-row muni_records that re-scanned the whole table PER
    # CANDIDATE ROW (65k x 1M+), and the 06:00 2026-08-11 cycle hung in it
    # for over an hour, blocking every later step AND the next cycles
    # (schtasks won't start a new instance while one runs).
    latest_by_kind = conn.execute(
        "SELECT kind, max(pulled_at) FROM muni_records "
        "WHERE source_url=? GROUP BY kind", (tag,)).fetchall()
    with conn:
        for kind, latest in latest_by_kind:
            if latest:
                conn.execute(
                    "DELETE FROM muni_records WHERE source_url=? AND "
                    "kind=? AND pulled_at<?", (tag, kind, latest))


def pull_market(conn: sqlite3.Connection, market: str, cfg: dict) -> int:
    tag = cfg.get("source_tag") or cfg.get("url")
    _sweep_stale_generations(conn, tag)
    if _source_fresh(conn, tag, _refresh_cutoff(cfg)):
        print(f"[sales:{market}] fresh (<{cfg.get('refresh_d', 7)}d) - skip")
        return 0
    return _ADAPTERS[cfg["type"]](conn, market, cfg)


# Sources ingested by mistake and evicted. Every run deletes their rows,
# so a stale database heals no matter when it last pulled. Never reuse a
# tag here for a live feed.
QUARANTINED_SOURCES = (
    "%il6vO1TutlF580Ku%",     # City of Richmond CALIFORNIA, not Virginia
)


def purge_quarantined(conn) -> int:
    n = 0
    for pat in QUARANTINED_SOURCES:
        cur = conn.execute(
            "DELETE FROM muni_records WHERE source_url LIKE ?", (pat,))
        n += cur.rowcount
    if n:
        conn.commit()
        print(f"[quarantine] purged {n:,} rows from evicted sources")
    return n


def main(argv: list[str] | None = None) -> int:
    only = {m.strip() for m in
            os.environ.get("ER_ARCGIS_SALES_MARKETS", "").split(",") if m.strip()}
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("[sales] no workbench.db - skipping")
        return 0
    conn = sqlite3.connect(db)
    try:
        purge_quarantined(conn)
        # muni_records ships with no index at all; every freshness check,
        # sweep and generation delete was a full-table scan. One-time cost
        # is a few seconds on the host db, then all of those are instant.
        conn.execute("CREATE INDEX IF NOT EXISTS ix_muni_src_kind_pulled "
                     "ON muni_records(source_url, kind, pulled_at)")
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
