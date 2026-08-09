"""QUARRIE — municipal open-data ETL for the Top-25 expansion markets.

Pulls free city/county assessor + permit + sales feeds and writes them to a
`muni_records` enrichment table in workbench.db, keyed so the workbench can
join them to property records by address/parcel later.

Two adapters cover ~every market (verified via live research 2026-05-30):
  • ArcGISPuller  — ArcGIS REST FeatureServer/MapServer `/query` (no key).
                    Paginates on resultOffset until exceededTransferLimit
                    clears. This is the dominant platform (Charlotte, Atlanta,
                    Nashville, Raleigh/Wake, DeKalb, Virginia Beach, Newport
                    News, Greenville SC, Memphis, …).
  • SocrataPuller — SODA API `/resource/{id}.json` with $limit/$offset
                    paging + optional X-App-Token. (Norfolk; Nashville mirror.)

The MUNI_FEEDS registry below is the irreplaceable asset: one FeedSpec per
confirmed endpoint, plus `status` for the gated/portal-only markets so the
map is complete. Wiring a new feed = adding one FeedSpec.

Run weekly:  python etl_munidata.py            (pulls all status="live" feeds)
             python etl_munidata.py --market Nashville
             python etl_munidata.py --list
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re as _re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import requests
except Exception:  # requests is in the app venv; guard for import-only use
    requests = None  # type: ignore

_DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = _DATA_DIR / "workbench.db"


# ---------------------------------------------------------------------------
# Feed spec + registry
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class FeedSpec:
    market: str                  # display market (matches record city)
    state: str
    county: str
    kind: str                    # "assessor" | "permits" | "sales" | "assessor+sales"
    platform: str                # "arcgis" | "socrata" | "file" | "portal"
    url: str                     # query base (ArcGIS layer URL or Socrata resource)
    status: str = "live"         # "live" | "gated" | "portal" | "file" | "paid"
    key_required: bool = False
    field_map: dict[str, str] = dataclasses.field(default_factory=dict)
    note: str = ""
    # Optional row filter. Statewide aggregates (VGIN's VA_Parcels covers
    # every locality in Virginia) are only usable per-city WITH this - an
    # unfiltered pull would ingest 4M rows under one market's FIPS.
    where: str = ""


# The verified Top-25 feed map. status="live" feeds are pull-ready, no key
# (except Socrata app-token, which is optional/free). Others are documented
# so Brian knows the path (Word doc covers the signups/gated ones).
MUNI_FEEDS: list[FeedSpec] = [
    # ---- North Carolina ----
    FeedSpec("Charlotte", "NC", "Mecklenburg", "assessor+sales", "arcgis",
             "https://services.arcgis.com/BWD3gDuaqc7SQmy7/arcgis/rest/services/"
             "TaxParcels_public_b4866785628d4f8d832dce3718f3997c/FeatureServer/0",
             status="verify",
             note="VERIFY: this endpoint currently returns count=0 (Mecklenburg "
                  "republished). Re-resolve the live TaxParcels item from the "
                  "maps.mecknc.gov/openmapping catalog. Permits=Accela (portal)."),
    FeedSpec("Raleigh", "NC", "Wake", "assessor+sales", "arcgis",
             "https://maps.wake.gov/arcgis/rest/services/Property/Parcels/MapServer/0",
             note="One layer = owner+assessed+yr built+TYPE_AND_USE+TOTSALPRICE+SALE_DATE. "
                  "Daily bulk also at services.wake.gov/realdata_extracts/"),
    FeedSpec("Raleigh", "NC", "Wake", "permits", "arcgis",
             "https://services.arcgis.com/v400IkDOw1ad7Yad/arcgis/rest/services/"
             "Building_Permits/FeatureServer/0"),
    FeedSpec("Greensboro", "NC", "Guilford", "assessor", "arcgis",
             "https://gcgis.guilfordcountync.gov/arcgis/rest/services/Tax/"
             "PublishingParcelsSpatialView_FeatureToPointWGS84/FeatureServer/0",
             note="Owner+assessed+yr built. No clean use code; text match. Permits=EPL portal."),
    FeedSpec("Durham", "NC", "Durham", "assessor", "arcgis",
             "https://services2.arcgis.com/G5vR3cOjh6g2Ed8E/arcgis/rest/services/"
             "Parcels_NEW/FeatureServer/0",
             note="Owner+assessed+LAND_CLASS. No yr built, no sales feed."),
    FeedSpec("Durham", "NC", "Durham", "permits", "arcgis",
             "https://services.arcgis.com/G5vR3cOjh6g2Ed8E/arcgis/rest/services/"
             "All_Building_Permits/FeatureServer/0",
             note="Confirm exact item URL from Hub 'View API Resources' before first run."),
    FeedSpec("Winston-Salem", "NC", "Forsyth", "assessor+sales", "arcgis",
             "https://maps.co.forsyth.nc.us/arcgis/rest/services/WS_CommDev/"
             "DQ_Parcels_and_City_Owned_Parcels/FeatureServer/2",
             note="Richest layer (64 fields), yr built (RESCOMYRBLT), LASTQUALIFIEDSALEPRICE. "
                  "maxRecordCount=20000."),
    # ---- Georgia ----
    FeedSpec("Atlanta", "GA", "Fulton", "assessor", "arcgis",
             "https://gis.atlantaga.gov/dpcd/rest/services/AdministrativeArea/"
             "TaxParcel/MapServer/0",
             note="Owner+assessed+appraised+CLASSCD. No yr built. Use internal MapServer "
                  "(open-data copy is stripped)."),
    FeedSpec("Atlanta", "GA", "Fulton", "assessor", "arcgis",
             "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/"
             "PropertyMapViewer/PropertyMapViewer/MapServer/11",
             note="Fulton full valuation (Tot/Land/Impr assessed + appraised)."),
    FeedSpec("Decatur", "GA", "DeKalb", "assessor", "arcgis",
             "https://dcgis.dekalbcountyga.gov/hosted/rest/services/PropertyAppraisal/"
             "Parcels_IASWorld/FeatureServer/0",
             note="Best GA layer: owner+CNTASSDVAL+LNDVALUE+RESYRBLT(yr built)+USECD."),
    FeedSpec("Savannah", "GA", "Chatham", "assessor", "arcgis",
             "https://pub.sagis.org/arcgis/rest/services/BOA/BoaTy_Parcels/MapServer/1",
             note="SAGIS Board of Assessors current parcels. Verify CAMA value fields via f=pjson."),
    FeedSpec("Athens", "GA", "Clarke", "assessor", "arcgis",
             "https://enigma.accgov.com/server/rest/services/ACC_Parcels/FeatureServer/0",
             note="Owner+use code only (no value/yr built). Value via qPublic scrape."),
    FeedSpec("Augusta", "GA", "Richmond", "assessor", "arcgis",
             "https://services1.arcgis.com/UKYQy2KtG5YhYPTp/arcgis/rest/services/"
             "OpenData_Parcels/FeatureServer/4",
             note="Geometry+PIN+address only. Owner/value via qPublic scrape."),
    FeedSpec("Macon", "GA", "Bibb", "assessor", "file", status="file",
             url="https://maconinsights.maconbibb.us/",
             note="WinGAP annual CAMA ZIP export (full schema). File-pull branch, not API."),
    FeedSpec("Atlanta", "GA", "—", "sales", "paid", status="paid",
             url="https://search.gsccca.org/RealEstate/",
             note="GA deeds/sales statewide = GSCCCA, $14.95-29.95/mo + $0.50/page. Paid."),
    # ---- Tennessee ----
    FeedSpec("Nashville", "TN", "Davidson", "assessor+sales", "arcgis",
             "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/"
             "Parcels_view/FeatureServer/0",
             note="GOLD: owner+LandAppr/ImprAppr/TotlAppr+LUCode+SalePrice+OwnDate in one layer."),
    FeedSpec("Nashville", "TN", "Davidson", "permits", "arcgis",
             "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/"
             "Building_Permits_Issued_2/FeatureServer/0"),
    FeedSpec("Memphis", "TN", "Shelby", "permits", "arcgis",
             "https://services2.arcgis.com/saWmpKJIUAjyyNVc/arcgis/rest/services/"
             "DPD_Building_Permits/FeatureServer/0",
             note="Permits live+free. Assessor data via Shelby Assessor request (not free API)."),
    FeedSpec("Knoxville", "TN", "Knox", "assessor", "portal", status="gated",
             url="https://www.kgis.org/gisserver/rest/services/",
             note="KGIS token-gated (499). Property site bans scraping. Needs licensed data agreement."),
    FeedSpec("Murfreesboro", "TN", "Rutherford", "assessor", "portal", status="portal",
             url="https://secured.rutherfordcountytn.gov/propertydata/",
             note="No open feed; WebPro 'Export results' tool only. Defer."),
    # ---- Virginia (home base + the rest) ----
    FeedSpec("Norfolk", "VA", "Norfolk", "assessor+sales", "socrata",
             "https://data.norfolk.gov/resource/g7sg-tivf.json",
             note="Reference model. Owner+land/imp/total value+yr built+use+consideration+transfer_date. "
                  "FY-stamped id — resolve current FY yearly."),
    FeedSpec("Norfolk", "VA", "Norfolk", "permits", "socrata",
             "https://data.norfolk.gov/resource/fahm-yuh4.json"),
    FeedSpec("Virginia Beach", "VA", "Virginia Beach", "assessor+sales", "arcgis",
             "https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/"
             "Property_Sales_view/FeatureServer/0",
             note="Sale date/price + land/imp/total value."),
    FeedSpec("Virginia Beach", "VA", "Virginia Beach", "permits", "arcgis",
             "https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/"
             "Building_Permits_Applications_view/FeatureServer/0"),
    FeedSpec("Newport News", "VA", "Newport News", "assessor+sales", "arcgis",
             "https://maps.nnva.gov/gis/rest/services/Operational/Parcel/MapServer/0",
             note="One layer: owner+use+yr built+LIVUNIT(units)+current/prior land+imp value+last sale."),
    FeedSpec("Richmond", "VA", "Richmond", "assessor+sales", "file", status="file",
             url="https://www.rva.gov/assessor-real-estate/data-request",
             note="Socrata API auth-gated (403). Use free monthly assessor Excel + Transfers/Sales report."),
    FeedSpec("Chesapeake", "VA", "Chesapeake", "assessor", "arcgis", status="live",
             url="https://gis.cityofchesapeake.net/mapping/rest/services/OpenData/"
                 "OpenData/MapServer/15",
             note="Parcel boundaries+class+transfer date; no owner/value/sale price (portal for those)."),
    FeedSpec("Arlington", "VA", "Arlington", "assessor", "arcgis", status="live",
             url="https://arlgis.arlingtonva.us/arcgis/rest/services/Open_Data/"
                 "od_REA_Property_Polygons/FeatureServer/0",
             note="Geometry only; assessment/sales/permits = CSV in data.arlingtonva.us directory."),
    FeedSpec("Alexandria", "VA", "Alexandria", "assessor", "arcgis", status="live",
             url="https://services2.arcgis.com/ChYV69FhfjwkvRmy/arcgis/rest/services/"
                 "Alexandria_Parcels/FeatureServer/0",
             note="Owner+boundaries; assessed value via realestate.alexandriava.gov portal."),
    FeedSpec("Fredericksburg", "VA", "Spotsylvania", "sales", "file", status="file",
             url="https://data-fredericksburg.opendata.arcgis.com/",
             note="Parcel_Sales.xls + dwelling/land tables (bulk download)."),
    # ---- South Carolina ----
    FeedSpec("Greenville", "SC", "Greenville", "assessor+sales", "arcgis",
             "https://www.gcgis.org/arcgiscw2/rest/services/GreenvilleJS/"
             "Map_Layers_JS/MapServer/5",
             note="Owner+use(PROPTYPE,LANDUSE)+SALEPRICE+SALEDATE. MF filter: PROPTYPE='MULTI-FAMILY'. "
                  "Note the /arcgiscw2/ path."),
    FeedSpec("Columbia", "SC", "Richland", "assessor", "portal", status="gated",
             url="https://beacon.schneidercorp.com/?AppID=1067",
             note="No public Esri feed; Beacon/qPublic (Schneider) scrape or negotiated bulk file."),
]


_HR_CITY_NAMES = ("virginia beach", "chesapeake", "hampton", "portsmouth",
                  "suffolk", "norfolk", "newport news", "richmond")


def named_for_other_city(text_parts: str, city: str) -> bool:
    """True when a feed's name/URL is titled after a DIFFERENT HR city.

    Whoever hosts it, a layer called ``Chesapeake_Norfolk_Streets_Parcels``
    holds Chesapeake/Norfolk data - VB's own AGOL org serves exactly that
    layer. Ingesting it under the wrong market assigns wrong-FIPS 8R ids,
    so the name is disqualifying on its own. Guarded here (not just in
    discovery) so a stale feeds_extra.json can never poison a pull.
    """
    text = _re.sub(r"[^a-z]+", " ", text_parts.lower())
    text = text.replace("hampton roads", " ")   # region name, not the city
    target = _re.sub(r"[^a-z]+", " ", city.lower()).strip()
    if target in text:
        return False
    return any(other in text for other in _HR_CITY_NAMES if other != target)


def _extra_feeds() -> list[FeedSpec]:
    """Feeds discovered on the host by scripts/discover_feeds.py.

    data/feeds_extra.json holds plain FeedSpec dicts; unknown keys are
    dropped so an older/newer discovery file never breaks the ETL.
    """
    path = _DATA_DIR / "feeds_extra.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    allowed = {f.name for f in dataclasses.fields(FeedSpec)}
    out: list[FeedSpec] = []
    for d in raw if isinstance(raw, list) else []:
        if not (isinstance(d, dict) and d.get("url")):
            continue
        market = str(d.get("market") or "")
        if market in ACTIVE_MARKETS and named_for_other_city(
                f"{d.get('url', '')} {d.get('note', '')}", market):
            print(f"  [skipped] {market}: {d['url']} - layer is named for "
                  "another city (re-run discover-feeds.bat)")
            continue
        out.append(FeedSpec(**{k: v for k, v in d.items() if k in allowed}))
    return out


def feeds(status: str | None = "live", market: str | None = None) -> list[FeedSpec]:
    out = MUNI_FEEDS + _extra_feeds()
    if status:
        out = [f for f in out if f.status == status]
    if market:
        out = [f for f in out if f.market.lower() == market.lower()]
    return out


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class ArcGISPuller:
    """Pull all features from an ArcGIS REST FeatureServer/MapServer layer.

    Paginates with resultOffset/resultRecordCount, following the layer's
    own maxRecordCount, until exceededTransferLimit clears. No key needed
    for public layers.
    """

    def __init__(self, layer_url: str, page: int = 1000, where: str = "1=1"):
        self.layer_url = layer_url.rstrip("/")
        self.page = page
        self.where = where

    def _get(self, params: dict[str, Any]) -> dict:
        """Query with retries: gov ArcGIS servers throw transient 502/503s
        mid-pagination (a VB pull died at offset 48,000 on one 502). 5xx,
        timeouts and connection drops retry with backoff; 4xx raise at once
        (the request itself is wrong - retrying can't fix it)."""
        if requests is None:
            raise RuntimeError("requests not available")
        last: Exception | None = None
        for delay in (0, 2, 5, 10):
            if delay:
                time.sleep(delay)
            try:
                r = requests.get(self.layer_url + "/query", params=params,
                                 timeout=60)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                status = getattr(getattr(e, "response", None),
                                 "status_code", None)
                if status is not None and status < 500:
                    raise
                last = e
        assert last is not None
        raise last

    def _meta(self) -> dict:
        if not hasattr(self, "_meta_cache"):
            if requests is None:
                raise RuntimeError("requests not available")
            r = requests.get(self.layer_url, params={"f": "json"}, timeout=30)
            r.raise_for_status()
            self._meta_cache = r.json()
        return self._meta_cache

    def max_record_count(self) -> int:
        try:
            return int(self._meta().get("maxRecordCount") or self.page)
        except Exception:
            return self.page

    def _oid_field(self) -> str | None:
        """Find the layer's true OID field name. Hosted layers vary
        (OBJECTID / FID / ObjectId / OID_). Ordering by a non-existent
        field makes some servers silently return 0 rows — so discover it."""
        try:
            meta = self._meta()
            if meta.get("objectIdField"):
                return meta["objectIdField"]
            for fld in meta.get("fields", []):
                if fld.get("type") == "esriFieldTypeOID":
                    return fld.get("name")
        except Exception:
            pass
        return None

    # Geometry fetch modes, tried in order on the first page. Without a
    # coordinate the spine can only match by address - Portsmouth went 0/45
    # in P0-2 because its layers stored no lat/lng at all.
    #   centroid: cheap (no polygon payload); hosted layers support it.
    #   geometry: full shapes for on-prem servers that ignore returnCentroid;
    #             polygon rings are averaged into a centroid downstream.
    #   none:     the legacy behavior, for servers that choke on either.
    _GEO_MODES = ("centroid", "geometry", "none")

    @staticmethod
    def _geo_params(mode: str) -> dict[str, Any]:
        if mode == "centroid":
            return {"returnGeometry": "false", "returnCentroid": "true",
                    "outSR": 4326}
        if mode == "geometry":
            return {"returnGeometry": "true", "outSR": 4326,
                    "geometryPrecision": 6}
        return {"returnGeometry": "false"}

    @staticmethod
    def _feature_xy(ft: dict) -> tuple[Any, Any]:
        geo = ft.get("centroid") or ft.get("geometry") or {}
        x, y = geo.get("x"), geo.get("y")
        if (x is None or y is None) and geo.get("rings"):
            ring = geo["rings"][0] or []
            pts = [p for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
            if pts:
                x = sum(p[0] for p in pts) / len(pts)
                y = sum(p[1] for p in pts) / len(pts)
        return x, y

    def iter_records(self) -> Iterable[dict]:
        offset = 0
        page = min(self.page, self.max_record_count())
        oid = self._oid_field()
        geo_mode: str | None = None
        while True:
            base = {
                "where": self.where,
                "outFields": "*",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": page,
            }
            if oid:  # only order when we know the real OID field
                base["orderByFields"] = oid
            if geo_mode is None:
                # First page: probe modes until one returns features (and,
                # for centroid mode, actually carries centroids).
                data, feats = {}, []
                for mode in self._GEO_MODES:
                    try:
                        data = self._get({**base, **self._geo_params(mode)})
                    except Exception:
                        continue
                    feats = data.get("features", [])
                    if data.get("error") or not feats:
                        continue
                    if mode == "centroid" and not feats[0].get("centroid"):
                        continue
                    geo_mode = mode
                    break
                if geo_mode is None:
                    geo_mode = "none"  # empty layer or all probes failed
            else:
                data = self._get({**base, **self._geo_params(geo_mode)})
                feats = data.get("features", [])
            for ft in feats:
                attrs = ft.get("attributes", {}) or {}
                x, y = self._feature_xy(ft)
                if x is not None and y is not None:
                    # Distinct keys - never clobber a real attribute column.
                    # phase0 maps geo_lat/geo_lng at top alias priority and
                    # converts stray Web Mercator meters to degrees.
                    attrs = {**attrs, "geo_lat": y, "geo_lng": x}
                yield attrs
            if not data.get("exceededTransferLimit") or not feats:
                break
            offset += page
            time.sleep(0.2)  # be polite to gov servers


class SocrataPuller:
    """Pull all rows from a Socrata SODA resource with $limit/$offset paging.
    Sends X-App-Token when provided (free; lifts rate limits)."""

    def __init__(self, resource_url: str, app_token: str | None = None,
                 page: int = 50000, where: str | None = None):
        self.resource_url = resource_url
        self.app_token = app_token
        self.page = page
        self.where = where

    def iter_records(self) -> Iterable[dict]:
        if requests is None:
            raise RuntimeError("requests not available")
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        offset = 0
        while True:
            params = {"$limit": self.page, "$offset": offset, "$order": ":id"}
            if self.where:
                params["$where"] = self.where
            r = requests.get(self.resource_url, params=params,
                             headers=headers, timeout=60)
            r.raise_for_status()
            rows = r.json()
            for row in rows:
                yield row
            if len(rows) < self.page:
                break
            offset += self.page
            time.sleep(0.2)


def puller_for(feed: FeedSpec, app_token: str | None = None):
    if feed.platform == "arcgis":
        return ArcGISPuller(feed.url, where=feed.where or "1=1")
    if feed.platform == "socrata":
        return SocrataPuller(feed.url, app_token=app_token,
                             where=feed.where or None)
    return None  # file/portal/paid handled out-of-band


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_MUNI_SCHEMA = """
CREATE TABLE IF NOT EXISTS muni_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    market        TEXT,
    state         TEXT,
    county        TEXT,
    kind          TEXT,
    source_url    TEXT,
    pulled_at     TEXT,
    record        TEXT      -- JSON of the raw attributes
);
CREATE INDEX IF NOT EXISTS ix_muni_market ON muni_records (market);
CREATE INDEX IF NOT EXISTS ix_muni_kind   ON muni_records (kind);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_MUNI_SCHEMA)


# Assessor rolls change on assessment cycles (annual) and permit feeds
# weekly at most. Re-downloading ~1M records every autopilot cycle was pure
# waste - it saturated the office server's CPU/disk (the app crawled and
# Streamlit reruns ghosted), hammered the city portals, and never yielded a
# new row. A feed re-pulls when its stamp ages out, when its URL/where
# changes (discovery found something better), or on ER_MUNI_FORCE=1.
MUNI_REFRESH_DAYS = 3


# Feeds are identified by (market, kind, source_url), NOT source_url alone.
# The VGIN statewide fallback (spec 15) serves Hampton, Suffolk, Richmond and
# Portsmouth from the SAME VA_Parcels URL, differing only by the locality
# `where`. Keying on url alone made those markets collide: one market's pull
# deleted the others' rows and one market's freshness skipped the rest, so
# Suffolk/Richmond came back empty while Hampton kept the shared rows. The
# whole triple is the identity.
def _feed_key(conn, feed: FeedSpec, cols: str):
    return conn.execute(
        f"SELECT {cols} FROM muni_records "
        " WHERE source_url = ? AND market = ? AND kind = ?",
        (feed.url, feed.market, feed.kind))


def _feed_fresh(conn: sqlite3.Connection, feed: FeedSpec) -> bool:
    import os
    if os.environ.get("ER_MUNI_FORCE", "").strip() in ("1", "true", "yes"):
        return False
    try:
        row = _feed_key(conn, feed, "MAX(pulled_at), COUNT(*)").fetchone()
    except sqlite3.Error:
        return False
    if not row or not row[0] or not row[1]:
        return False                    # never pulled (or pulled empty)
    try:
        age = dt.datetime.now() - dt.datetime.fromisoformat(row[0])
    except ValueError:
        return False
    return age <= dt.timedelta(days=MUNI_REFRESH_DAYS)


def run_feed(feed: FeedSpec, conn: sqlite3.Connection,
             app_token: str | None = None, limit: int | None = None) -> int:
    if _feed_fresh(conn, feed):
        n = _feed_key(conn, feed, "COUNT(*)").fetchone()[0]
        print(f"  [fresh] {feed.market}/{feed.kind}: pulled within "
              f"{MUNI_REFRESH_DAYS} days - keeping {n:,} rows "
              f"(ER_MUNI_FORCE=1 to re-pull)")
        return n
    puller = puller_for(feed, app_token=app_token)
    if puller is None:
        return 0
    pulled_at = dt.datetime.now().isoformat(timespec="seconds")
    n = 0
    rows = []
    for rec in puller.iter_records():
        rows.append((feed.market, feed.state, feed.county, feed.kind,
                     feed.url, pulled_at, json.dumps(rec, default=str)))
        n += 1
        if limit and n >= limit:
            break
    # Replace prior records for this (market, kind, url) - all three, so a
    # URL shared across markets (the VGIN statewide layer) does not wipe a
    # sibling market's rows.
    conn.execute("DELETE FROM muni_records WHERE source_url = ? "
                 " AND market = ? AND kind = ?",
                 (feed.url, feed.market, feed.kind))
    conn.executemany(
        "INSERT INTO muni_records (market,state,county,kind,source_url,pulled_at,record) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return n


HR_MARKETS = ("Norfolk", "Virginia Beach", "Chesapeake", "Hampton",
              "Newport News", "Portsmouth", "Suffolk")

# 50-metro rollout (spec 15): expansion markets whose feeds ride the same
# nightly --hr pull and stale-row sweep as Hampton Roads.
#   Wave 1 (2026-08-09, owner directive - free sources, prioritized waves):
#   major metros whose free ArcGIS/Socrata assessor feeds are ALREADY in the
#   MUNI_FEEDS registry, so activating them pulls existing feeds, not new
#   scraping. Their FIPS + metro labels are wired in core/market_data.py.
EXPANSION_MARKETS = ("Richmond", "Raleigh", "Charlotte", "Winston-Salem",
                     "Greensboro", "Durham", "Nashville", "Atlanta")
ACTIVE_MARKETS = HR_MARKETS + EXPANSION_MARKETS


def run_all(app_token: str | None = None, market: str | None = None,
            limit: int | None = None, hr_only: bool = False) -> dict[str, int]:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout = 60000")   # tolerate the app reading
    # WAL: readers never block the writer - the pull can run unattended
    # while the app/service is up (autopilot requirement). Persistent.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    _ensure_schema(conn)
    extras = _extra_feeds()
    print(f"  discovered feeds loaded from data/feeds_extra.json: {len(extras)}")
    for f in extras:
        print(f"    + {f.market}: {f.url}")
    results: dict[str, int] = {}
    todo = feeds(status="live", market=market)
    if hr_only:
        todo = [f for f in todo if f.market in ACTIVE_MARKETS]
    # Reconciliation sweep: rows from feeds that are NO LONGER in the
    # registry linger forever otherwise (run_feed only replaces feeds it
    # re-pulls). That left retired discovery layers in the DB - including
    # a "Chesapeake_Parcels_Within_Blast_Zone" layer filed under HAMPTON
    # from before the wrong-city guard existed. Swept per HR market so
    # national pulls are untouched.
    current_urls = {f.url for f in feeds(status=None)}
    swept = 0
    for m in ACTIVE_MARKETS:
        rows = conn.execute(
            "SELECT DISTINCT source_url FROM muni_records WHERE market = ?",
            (m,)).fetchall()
        for (url,) in rows:
            if url and url not in current_urls:
                cur = conn.execute(
                    "DELETE FROM muni_records WHERE market = ? "
                    " AND source_url = ?", (m, url))
                swept += cur.rowcount
                print(f"  [swept] {m}: {cur.rowcount:,} stale rows from "
                      f"retired feed {url}")
    if swept:
        conn.commit()
        print(f"  stale rows removed: {swept:,}")
    for feed in todo:
        key = f"{feed.market}/{feed.kind}"
        try:
            results[key] = run_feed(feed, conn, app_token=app_token, limit=limit)
            print(f"  [OK] {key:38} {results[key]:>7} records")
        except Exception as e:
            results[key] = -1
            print(f"  [ERR] {key:38} {e}")
    conn.close()
    return results


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="QUARRIE municipal-data ETL")
    ap.add_argument("--market", help="Only pull this market")
    ap.add_argument("--list", action="store_true", help="List the feed registry and exit")
    ap.add_argument("--limit", type=int, help="Cap records per feed (testing)")
    ap.add_argument("--app-token", help="Socrata app token (Norfolk/Nashville)")
    ap.add_argument("--hr", action="store_true",
                    help="Hampton Roads markets only (skip the Top-25 pulls)")
    args = ap.parse_args(argv)

    if args.list:
        by_status: dict[str, int] = {}
        print(f"{'Market':16} {'St':2} {'County':14} {'Kind':16} {'Platform':8} {'Status':7}")
        print("-" * 78)
        for f in MUNI_FEEDS:
            by_status[f.status] = by_status.get(f.status, 0) + 1
            print(f"{f.market[:16]:16} {f.state:2} {f.county[:14]:14} "
                  f"{f.kind:16} {f.platform:8} {f.status:7}")
        print(f"\n{len(MUNI_FEEDS)} feeds: " +
              ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
        live = len(feeds("live"))
        print(f"{live} are pull-ready now (status=live, no key beyond optional Socrata token).")
        return 0

    print(f"QUARRIE muni-data ETL — {dt.date.today().isoformat()}")
    run_all(app_token=args.app_token, market=args.market,
            limit=args.limit, hr_only=args.hr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
