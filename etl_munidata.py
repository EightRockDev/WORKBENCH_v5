"""QUARRY — municipal open-data ETL for the Top-25 expansion markets.

Pulls free city/county assessor + permit + sales feeds and writes them to a
`muni_records` enrichment table in workbench.db, keyed so the workbench can
join them to ALN properties by address/parcel later.

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
    market: str                  # display market (matches ALN city)
    state: str
    county: str
    kind: str                    # "assessor" | "permits" | "sales" | "assessor+sales"
    platform: str                # "arcgis" | "socrata" | "file" | "portal"
    url: str                     # query base (ArcGIS layer URL or Socrata resource)
    status: str = "live"         # "live" | "gated" | "portal" | "file" | "paid"
    key_required: bool = False
    field_map: dict[str, str] = dataclasses.field(default_factory=dict)
    note: str = ""


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


def feeds(status: str | None = "live", market: str | None = None) -> list[FeedSpec]:
    out = MUNI_FEEDS
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
        if requests is None:
            raise RuntimeError("requests not available")
        r = requests.get(self.layer_url + "/query", params=params, timeout=60)
        r.raise_for_status()
        return r.json()

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

    def iter_records(self) -> Iterable[dict]:
        offset = 0
        page = min(self.page, self.max_record_count())
        oid = self._oid_field()
        while True:
            params = {
                "where": self.where,
                "outFields": "*",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": page,
            }
            if oid:  # only order when we know the real OID field
                params["orderByFields"] = oid
            data = self._get(params)
            feats = data.get("features", [])
            for ft in feats:
                yield ft.get("attributes", {})
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
        return ArcGISPuller(feed.url)
    if feed.platform == "socrata":
        return SocrataPuller(feed.url, app_token=app_token)
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


def run_feed(feed: FeedSpec, conn: sqlite3.Connection,
             app_token: str | None = None, limit: int | None = None) -> int:
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
    # Replace prior records for this (market, kind, url)
    conn.execute("DELETE FROM muni_records WHERE source_url = ?", (feed.url,))
    conn.executemany(
        "INSERT INTO muni_records (market,state,county,kind,source_url,pulled_at,record) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return n


def run_all(app_token: str | None = None, market: str | None = None,
            limit: int | None = None) -> dict[str, int]:
    conn = sqlite3.connect(DB_PATH)
    _ensure_schema(conn)
    results: dict[str, int] = {}
    for feed in feeds(status="live", market=market):
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
    ap = argparse.ArgumentParser(description="QUARRY municipal-data ETL")
    ap.add_argument("--market", help="Only pull this market")
    ap.add_argument("--list", action="store_true", help="List the feed registry and exit")
    ap.add_argument("--limit", type=int, help="Cap records per feed (testing)")
    ap.add_argument("--app-token", help="Socrata app token (Norfolk/Nashville)")
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

    print(f"QUARRY muni-data ETL — {dt.date.today().isoformat()}")
    run_all(app_token=args.app_token, market=args.market, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
