"""Hampton Roads city-assessor open-data puller.

Pulls multifamily parcel inventory + FY-by-FY assessed value history from
each HR city's open-data portal, writes to two tables:

  - `va_multifamily_inventory`: one row per (city, parcel_id) — latest
    snapshot with address, owner, GPIN, year built, current total/land/
    improvement values, sqft/acreage, latest sale, geocoded lat/lng.
  - `va_assessment_history`: one row per (city, parcel_id, fiscal_year) —
    historical FY values for time-series analysis.

These are the "free comprehensive" answer to Brian's question about catching
new properties + name changes (Phase 3 of 2026-05-08 strategic memo). The
Streamlit workbench's `core.market_data` reads these tables to surface the
data in the Subject tab.

City status (2026-05-08 build):
  ✓ Norfolk         — Socrata, FY19-FY26 datasets published per FY
  ☐ Virginia Beach  — TODO (vbgov.com, ESRI map service)
  ☐ Chesapeake      — TODO (cityofchesapeake.net)
  ☐ Portsmouth      — TODO (likely needs scraping; no open API)
  ☐ Hampton         — TODO (hampton.gov)
  ☐ Newport News    — TODO (nngov.com)
  ☐ Suffolk         — TODO (suffolkva.us)

For each new city, implement `pull_<city>()` returning two DataFrames
(inventory, history) and add to `CITY_DISPATCH`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import requests

# Browser-like UA so city portals don't reject default Python requests
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "hampton-roads-etl/1.0 (+research)"
    ),
}


# ---------------------------------------------------------------------------
# Norfolk (Socrata-based open data portal)
# ---------------------------------------------------------------------------

NORFOLK_BASE = "https://data.norfolk.gov/resource"

# Per-FY Socrata resource IDs — separate dataset for each fiscal year.
# When a new FY drops (typically July 1), Norfolk publishes a new dataset
# and freezes the prior one. Append the new year to this dict.
NORFOLK_FY_DATASETS: dict[int, str] = {
    2019: "th3n-jr9u",
    2020: "pdf2-gh9c",
    2021: "8bfx-a5g8",
    2022: "7tu9-2ytx",
    2023: "yvpm-8aid",
    2024: "9gmp-9x4c",
    2025: "g7sg-tivf",
    2026: "m5ya-5grb",
}

# Norfolk multifamily class codes for Eight Rock's universe — 5+ unit residential.
# Class codes are stored as the 3-digit prefix of `property_class_description`
# (e.g., "405 Apartment 49+ Low Rise"). Critically, some FYs have trailing
# whitespace ("405 Apartment 49+ Low Rise   ") so we filter by PREFIX with
# `starts_with` rather than exact-match `IN (...)`.
# Excludes 450 Com Condo and 418 Retail/Apt (mixed-use edge cases).
NORFOLK_MULTIFAMILY_CLASS_CODES = ("401", "402", "403", "404", "405", "406", "407")


def _norfolk_fetch_fy(
    year: int,
    resource_id: str,
    class_codes: tuple[str, ...],
    page_size: int = 1000,
) -> pd.DataFrame:
    """Fetch all multifamily parcels from a single Norfolk FY dataset.

    Socrata supports `$limit` + `$offset` for pagination. We filter by
    class-code PREFIX so the query tolerates trailing-whitespace variants
    that appear in FY2021/FY2022 datasets.
    """
    url = f"{NORFOLK_BASE}/{resource_id}.json"
    # Build SoQL: starts_with(prop_class_desc, '401') OR ... OR starts_with(..., '407')
    where = " OR ".join(
        f"starts_with(property_class_description, '{code}')"
        for code in class_codes
    )

    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "$where": where,
            "$limit": page_size,
            "$offset": offset,
        }
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=60)
            if r.status_code != 200:
                print(f"    [norfolk] FY{year} HTTP {r.status_code} on offset {offset}")
                break
            page = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"    [norfolk] FY{year} fetch failed: {e}")
            break

        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["fiscal_year"] = year
    df["city"] = "Norfolk"
    return df


def pull_norfolk(
    class_codes: tuple[str, ...] = NORFOLK_MULTIFAMILY_CLASS_CODES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull Norfolk multifamily inventory + assessment history across all
    published fiscal years.

    Returns (inventory_df, history_df).

    Inventory has one row per parcel_id with the LATEST FY values.
    History has one row per (parcel_id, fiscal_year).
    """
    print(f"  [va_assessors] pulling Norfolk multifamily across {len(NORFOLK_FY_DATASETS)} fiscal years…")
    fy_frames: list[pd.DataFrame] = []
    for year in sorted(NORFOLK_FY_DATASETS):
        rid = NORFOLK_FY_DATASETS[year]
        df = _norfolk_fetch_fy(year, rid, class_codes)
        if df.empty:
            print(f"    [norfolk] FY{year}: empty")
            continue
        print(f"    [norfolk] FY{year}: {len(df):,} parcels")
        fy_frames.append(df)

    if not fy_frames:
        return pd.DataFrame(), pd.DataFrame()

    combined = pd.concat(fy_frames, ignore_index=True, sort=False)

    # Strip whitespace from class descriptions (FY21/FY22 datasets pad with
    # trailing spaces — clean here so downstream queries don't need LIKE).
    if "property_class_description" in combined.columns:
        combined["property_class_description"] = (
            combined["property_class_description"].astype(str).str.strip()
        )

    # Norfolk's dataset has multi-building parcels split into MULTIPLE rows
    # per FY (same parcel_id, gpin, total values, but different sub-records
    # for each building / structure). Ashley Trace appears 6× per FY.
    # Deduplicate on (parcel_id, fiscal_year) — keep the first occurrence.
    combined = combined.drop_duplicates(
        subset=["parcel_id", "fiscal_year"], keep="first"
    )

    # ---- Build assessment history (long-form) ----
    history_cols = [
        "city", "parcel_id", "gpin", "fiscal_year",
        "current_total_value", "current_land_value", "current_improvement_value",
    ]
    history = combined[[c for c in history_cols if c in combined.columns]].copy()
    # Numeric coercion (Socrata returns strings)
    for col in ("current_total_value", "current_land_value", "current_improvement_value"):
        if col in history.columns:
            history[col] = pd.to_numeric(history[col], errors="coerce")
    history = history.rename(columns={
        "current_total_value": "assessed_value",
        "current_land_value": "land_value",
        "current_improvement_value": "improvement_value",
    })
    history = history.dropna(subset=["parcel_id", "fiscal_year"])

    # ---- Build inventory (latest-year snapshot per parcel) ----
    latest_year = max(NORFOLK_FY_DATASETS)
    latest = combined[combined["fiscal_year"] == latest_year].copy()
    if latest.empty:
        # If newest year had no data, fall back to most-recent non-empty
        for y in sorted(NORFOLK_FY_DATASETS, reverse=True):
            cand = combined[combined["fiscal_year"] == y]
            if not cand.empty:
                latest = cand.copy()
                break

    # Synthesize address column
    addr_parts = ("property_street_number", "property_street_name", "property_street_type")
    if all(c in latest.columns for c in addr_parts):
        latest["address"] = (
            latest["property_street_number"].fillna("").astype(str).str.strip() + " "
            + latest["property_street_name"].fillna("").astype(str).str.strip() + " "
            + latest["property_street_type"].fillna("").astype(str).str.strip()
        ).str.strip().str.replace(r"\s+", " ", regex=True)

    inventory_cols = [
        "city", "parcel_id", "gpin", "address",
        "property_class_description", "property_use",
        "owner", "improvement_year_built",
        "acreage", "land_square_footage", "commercial_building_area",
        "current_total_value", "current_land_value", "current_improvement_value",
        "transfer_date", "consideration", "grantee",
        "property_zip",
    ]
    inv = latest[[c for c in inventory_cols if c in latest.columns]].copy()
    # Numeric coercion
    for col in (
        "improvement_year_built", "acreage", "land_square_footage",
        "commercial_building_area", "current_total_value",
        "current_land_value", "current_improvement_value", "consideration",
    ):
        if col in inv.columns:
            inv[col] = pd.to_numeric(inv[col], errors="coerce")
    inv = inv.rename(columns={
        "property_class_description": "class_description",
        "improvement_year_built": "year_built",
        "current_total_value": "assessed_value",
        "current_land_value": "land_value",
        "current_improvement_value": "improvement_value",
        "transfer_date": "last_sale_date",
        "consideration": "last_sale_price",
        "grantee": "last_sale_buyer",
    })
    # latest_fy column for reference
    inv["latest_fiscal_year"] = latest_year

    return inv, history


# ---------------------------------------------------------------------------
# Generic ArcGIS REST paginator — used by Chesapeake / Newport News / Hampton
# ---------------------------------------------------------------------------

def _arcgis_query(
    base_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    page_size: int = 1000,
    order_by: str | None = None,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Page an ArcGIS REST FeatureService/MapServer layer until empty.

    Returns the list of attribute dicts (geometry stripped). Handles
    `exceededTransferLimit` continuation. Some servers require an
    `orderByFields` clause when paginating — pass via `order_by`.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "returnGeometry": "false",
        }
        if order_by:
            params["orderByFields"] = order_by
        try:
            r = requests.get(f"{base_url}/query", headers=HEADERS,
                             params=params, timeout=timeout)
            r.raise_for_status()
            body = r.json()
        except (requests.RequestException, ValueError):
            break
        feats = body.get("features", [])
        if not feats:
            break
        rows.extend(f.get("attributes", {}) for f in feats)
        if not body.get("exceededTransferLimit") and len(feats) < page_size:
            break
        offset += page_size
    return rows


# ---------------------------------------------------------------------------
# Chesapeake (★ ArcGIS REST — ProvalCommon_Layers/Parcels)
# ---------------------------------------------------------------------------

CHESAPEAKE_PARCELS_URL = (
    "https://gis.cityofchesapeake.net/mapping/rest/services/"
    "Common_Layers/Parcels/MapServer/0"
)

# Chesapeake property class codes (4-digit) for multifamily-flavored properties.
# 3010-3019 = condo / single condo unit
# 3346, 3352, 3380, 3382 = apartment communities (5+ units, Eight Rock target)
# 3812 = single-family residential (NOT MF, but in 3xxx range — exclude)
# We capture the 3xxx range broadly and let downstream UI filter further.
CHESAPEAKE_MULTIFAMILY_PREFIXES = ("3346", "3352", "3380", "3382", "3010", "3015")


def pull_chesapeake() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull Chesapeake multifamily parcels via ArcGIS REST.

    Returns (inventory_df, history_df). History is single-FY only — Chesapeake's
    REST endpoint exposes current values only. The TRANSFER + CONSIDERATION
    fields give last-sale data.
    """
    print(f"  [va_assessors] pulling Chesapeake multifamily…")
    where_clauses = " OR ".join(
        f"PROPCLASS LIKE '{p}%'" for p in CHESAPEAKE_MULTIFAMILY_PREFIXES
    )
    rows = _arcgis_query(CHESAPEAKE_PARCELS_URL, where=f"({where_clauses})")
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(rows)
    df["city"] = "Chesapeake"
    print(f"    [chesapeake] pulled {len(df):,} multifamily parcels")

    # Build inventory
    inv = pd.DataFrame({
        "city": "Chesapeake",
        "parcel_id": df.get("MAP_PARCEL", pd.Series(dtype=str)).astype(str).str.strip(),
        "gpin": df.get("CNTRL_NO", pd.Series(dtype=str)).astype(str).str.strip(),
        "address": df.get("ADDRESS", pd.Series(dtype=str)).astype(str).str.strip(),
        "class_description": df.get("PROPCLASS", pd.Series(dtype=str)).astype(str).str.strip(),
        "property_use": df.get("PROPCLASS", pd.Series(dtype=str)).astype(str).str.strip(),
        "owner": df.get("OWNER", pd.Series(dtype=str)).astype(str).str.strip(),
        "year_built": pd.NA,  # not exposed in Chesapeake REST
        "acreage": pd.to_numeric(df.get("ACRES"), errors="coerce"),
        "land_square_footage": pd.to_numeric(df.get("LANDSF"), errors="coerce"),
        "commercial_building_area": pd.NA,
        "assessed_value": pd.to_numeric(df.get("ASMT_TOTAL"), errors="coerce"),
        "land_value": pd.to_numeric(df.get("ASMT_LD"), errors="coerce"),
        "improvement_value": pd.to_numeric(df.get("ASMT_BL"), errors="coerce"),
        "last_sale_date": df.get("TRANSFER"),
        "last_sale_price": pd.to_numeric(df.get("CONSIDERATION"), errors="coerce"),
        "last_sale_buyer": pd.NA,
        "property_zip": df.get("ZIP", pd.Series(dtype=str)).astype(str).str.strip(),
        "latest_fiscal_year": dt.date.today().year + 1,  # FY runs ahead
    })
    inv = inv[inv["parcel_id"].astype(str).str.len() > 0]

    # Build single-year history
    today_fy = dt.date.today().year + 1  # VA fiscal year flips July 1
    history = pd.DataFrame({
        "city": "Chesapeake",
        "parcel_id": inv["parcel_id"],
        "gpin": inv["gpin"],
        "fiscal_year": today_fy,
        "assessed_value": inv["assessed_value"],
        "land_value": inv["land_value"],
        "improvement_value": inv["improvement_value"],
    }).dropna(subset=["assessed_value"])

    return inv, history


# ---------------------------------------------------------------------------
# Newport News (★ ArcGIS REST — has 2yr history + sale data)
# ---------------------------------------------------------------------------

NEWPORT_NEWS_PARCELS_URL = (
    "https://maps.nnva.gov/gis/rest/services/Operational/Parcel/MapServer/0"
)

# CLASSDSCRP values that match Eight Rock multifamily target.
# "Apartment (over 4 dwellings)" — sweet spot
# "Multi Family (2-4 dwellings)" — duplex/triplex/quad
# Skip "Condominium" (4671 parcels — too many, mostly individual condo units)
NEWPORT_NEWS_MF_CLASSES = (
    "Apartment (over 4 dwellings)",
    "Multi Family (2-4 dwellings)",
)


def pull_newport_news() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull Newport News multifamily parcels — 2-year history + sale data."""
    print(f"  [va_assessors] pulling Newport News multifamily…")
    quoted = ",".join(f"'{c}'" for c in NEWPORT_NEWS_MF_CLASSES)
    where = f"CLASSDSCRP IN ({quoted})"
    rows = _arcgis_query(NEWPORT_NEWS_PARCELS_URL, where=where)
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(rows)
    df["city"] = "Newport News"
    print(f"    [newport-news] pulled {len(df):,} multifamily parcels")

    # Compose address
    addr_parts = ["SITEADDRESS"]
    if "SITEADDRESS" in df.columns:
        df["address"] = df["SITEADDRESS"].astype(str).str.strip()
    else:
        df["address"] = ""

    # Numeric coercion
    cnt_total = pd.to_numeric(df.get("CNTLNDVAL"), errors="coerce").fillna(0) + \
                pd.to_numeric(df.get("CNTIMPVAL"), errors="coerce").fillna(0)
    prv_total = pd.to_numeric(df.get("PRVLNDVAL"), errors="coerce").fillna(0) + \
                pd.to_numeric(df.get("PRVIMPVAL"), errors="coerce").fillna(0)

    inv = pd.DataFrame({
        "city": "Newport News",
        "parcel_id": df.get("PARCELID", pd.Series(dtype=str)).astype(str).str.strip(),
        "gpin": df.get("PARCELID", pd.Series(dtype=str)).astype(str).str.strip(),  # NN uses PARCELID as the GPIN equivalent
        "address": df["address"],
        "class_description": df.get("CLASSDSCRP", pd.Series(dtype=str)).astype(str).str.strip(),
        "property_use": df.get("USEDSCRP", pd.Series(dtype=str)).astype(str).str.strip(),
        "owner": df.get("OWNERNME1", pd.Series(dtype=str)).astype(str).str.strip(),
        "year_built": pd.to_numeric(df.get("RESYRBLT"), errors="coerce"),
        "acreage": pd.to_numeric(df.get("DEEDACRES"), errors="coerce"),
        "land_square_footage": pd.NA,
        "commercial_building_area": pd.to_numeric(df.get("RESFLRAREA"), errors="coerce"),
        "assessed_value": cnt_total.replace(0, pd.NA),
        "land_value": pd.to_numeric(df.get("CNTLNDVAL"), errors="coerce"),
        "improvement_value": pd.to_numeric(df.get("CNTIMPVAL"), errors="coerce"),
        "last_sale_date": df.get("LASTSALEDATE"),
        "last_sale_price": pd.to_numeric(df.get("LASTSALEPRICE"), errors="coerce"),
        "last_sale_buyer": pd.NA,
        "property_zip": df.get("PROPZIP", pd.Series(dtype=str)).astype(str).str.strip(),
        "latest_fiscal_year": dt.date.today().year + 1,
    })
    inv = inv[inv["parcel_id"].astype(str).str.len() > 0]

    # Build 2-FY history (current + previous)
    today_fy = dt.date.today().year + 1
    cur_hist = pd.DataFrame({
        "city": "Newport News",
        "parcel_id": inv["parcel_id"],
        "gpin": inv["gpin"],
        "fiscal_year": today_fy,
        "assessed_value": inv["assessed_value"],
        "land_value": inv["land_value"],
        "improvement_value": inv["improvement_value"],
    }).dropna(subset=["assessed_value"])
    prev_hist = pd.DataFrame({
        "city": "Newport News",
        "parcel_id": inv["parcel_id"],
        "gpin": inv["gpin"],
        "fiscal_year": today_fy - 1,
        "assessed_value": prv_total.replace(0, pd.NA),
        "land_value": pd.to_numeric(df.get("PRVLNDVAL"), errors="coerce"),
        "improvement_value": pd.to_numeric(df.get("PRVIMPVAL"), errors="coerce"),
    }).dropna(subset=["assessed_value"])

    history = pd.concat([prev_hist, cur_hist], ignore_index=True)
    return inv, history


# ---------------------------------------------------------------------------
# Hampton (★ ArcGIS REST — RealMaster has 5yr assessment history)
# ---------------------------------------------------------------------------

HAMPTON_REALMASTER_URL = (
    "https://webgis2.hampton.gov/server/rest/services/BasicGov/MapServer/5"
)
HAMPTON_PARCELS_URL = (
    "https://webgis2.hampton.gov/server/rest/services/BasicGov/MapServer/0"
)

# Hampton uses descriptive PCDesc strings like "Multi-Family            "
# (trailing spaces). Filter with LIKE prefix.
HAMPTON_MF_PCDESC_PREFIXES = ("Multi-Family",)


def pull_hampton() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull Hampton multifamily parcels — 5-year history via RealMaster table.

    Joins RealMaster (assessment) with Parcels layer (geometry/SITUS address)
    on GPIN. RealMaster has no sale data — we leave that null.
    """
    print(f"  [va_assessors] pulling Hampton multifamily (RealMaster)…")
    where_clauses = " OR ".join(
        f"PCDesc LIKE '{p}%'" for p in HAMPTON_MF_PCDESC_PREFIXES
    )
    out_fields = (
        "GPIN,PIN,Owner1,Owner2,PCDesc,LocCity,"
        "LandVal1,LandVal2,LandVal3,LandVal4,LandVal5,"
        "DwlgVal1,DwlgVal2,DwlgVal3,DwlgVal4,DwlgVal5,"
        "OthVal1,OthVal2,OthVal3,OthVal4,OthVal5,"
        "TotVal1,TotVal2,TotVal3,TotVal4,TotVal5"
    )
    rows = _arcgis_query(
        HAMPTON_REALMASTER_URL,
        where=where_clauses,
        out_fields=out_fields,
        order_by="GPIN",  # required — table has no OID for pagination
    )
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    rm = pd.DataFrame(rows)
    print(f"    [hampton] pulled {len(rm):,} multifamily RealMaster rows")

    # Pull Parcels layer for SITUS addresses + acreage (joined on GPIN)
    parcel_rows = _arcgis_query(
        HAMPTON_PARCELS_URL,
        out_fields="GPIN,SITUS,SQFT,ACREAGE",
        order_by="OBJECTID",
    )
    if parcel_rows:
        parcels = pd.DataFrame(parcel_rows)
        # Merge with RealMaster on GPIN
        rm = rm.merge(
            parcels[["GPIN", "SITUS", "SQFT", "ACREAGE"]].drop_duplicates("GPIN"),
            on="GPIN", how="left",
        )

    # Strip trailing whitespace from string fields
    for col in ("PCDesc", "Owner1", "Owner2", "SITUS"):
        if col in rm.columns:
            rm[col] = rm[col].astype(str).str.strip()

    today_fy = dt.date.today().year + 1

    inv = pd.DataFrame({
        "city": "Hampton",
        "parcel_id": rm.get("PIN", pd.Series(dtype=str)).astype(str).str.strip(),
        "gpin": rm.get("GPIN", pd.Series(dtype=str)).astype(str).str.strip(),
        "address": rm.get("SITUS", pd.Series(dtype=str)),
        "class_description": rm.get("PCDesc", pd.Series(dtype=str)),
        "property_use": rm.get("PCDesc", pd.Series(dtype=str)),
        "owner": rm.get("Owner1", pd.Series(dtype=str)),
        "year_built": pd.NA,
        "acreage": pd.to_numeric(rm.get("ACREAGE"), errors="coerce"),
        "land_square_footage": pd.to_numeric(rm.get("SQFT"), errors="coerce"),
        "commercial_building_area": pd.NA,
        "assessed_value": pd.to_numeric(rm.get("TotVal1"), errors="coerce"),
        "land_value": pd.to_numeric(rm.get("LandVal1"), errors="coerce"),
        "improvement_value": pd.to_numeric(rm.get("DwlgVal1"), errors="coerce"),
        "last_sale_date": pd.NA,
        "last_sale_price": pd.NA,
        "last_sale_buyer": pd.NA,
        "property_zip": pd.NA,
        "latest_fiscal_year": today_fy,
    })
    inv = inv[inv["parcel_id"].astype(str).str.len() > 0]

    # Build 5-FY history. Hampton's TotVal1..5 are most-recent..oldest.
    history_frames = []
    for offset, suffix in enumerate(("1", "2", "3", "4", "5")):
        fy = today_fy - offset
        h = pd.DataFrame({
            "city": "Hampton",
            "parcel_id": rm.get("PIN", pd.Series(dtype=str)).astype(str).str.strip(),
            "gpin": rm.get("GPIN", pd.Series(dtype=str)).astype(str).str.strip(),
            "fiscal_year": fy,
            "assessed_value": pd.to_numeric(rm.get(f"TotVal{suffix}"), errors="coerce"),
            "land_value": pd.to_numeric(rm.get(f"LandVal{suffix}"), errors="coerce"),
            "improvement_value": pd.to_numeric(rm.get(f"DwlgVal{suffix}"), errors="coerce"),
        }).dropna(subset=["assessed_value"])
        h = h[h["assessed_value"] > 0]
        history_frames.append(h)

    history = pd.concat(history_frames, ignore_index=True) if history_frames else pd.DataFrame()
    return inv, history


# ---------------------------------------------------------------------------
# Virginia Beach (★★ — VB's own MapServer for parcels + HRGEO for values)
# ---------------------------------------------------------------------------
#
# VB's multifamily inventory needs a 2-source merge:
#   1. VB's own MapServer (geo.vbgov.com) — parcel list, addresses, GPINs,
#      LAND_USE classifications. No auth needed. ~445 multifamily parcels.
#   2. HRGEO regional layer — owner + assessed value + year built. VB shares
#      these via HRGEO. We join on PAR_GPIN ↔ HRGEO PARCELID (both 14-digit).
#
# Why not just HRGEO? VB's zoning codes don't follow common HR conventions
# (R20, B2, AG1, etc. — none flag multifamily reliably). The MapServer's
# LAND_USE = 'Multi Family' is the authoritative classification.
#
# Why not just the MapServer? It has zero financial fields (no owner, no
# assessed value, no year built). Hosted FeatureServer with that data
# requires AGOL auth (returns 499 Token Required from the discovery).

VB_PARCELS_URL = (
    "https://geo.vbgov.com/mapservices/rest/services/Basemaps/"
    "Property_Information/MapServer/12"
)


def pull_virginia_beach() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull Virginia Beach multifamily parcels.

    Step 1: VB's own MapServer for parcel list (LAND_USE='Multi Family').
    Step 2: HRGEO regional layer for owner + assessed value + year built.
    Step 3: merge on GPIN.

    Returns (inventory_df, history_df). History is single-FY only.
    """
    print(f"  [va_assessors] pulling Virginia Beach multifamily…")

    # ---- Step 1: parcels from VB's own MapServer ----
    rows = _arcgis_query(
        VB_PARCELS_URL,
        where="LAND_USE = 'Multi Family'",
        out_fields=(
            "OBJECTID,PAR_GPIN,PROP_ADDRESS,LAND_USE,PROP_CLASS,ZIP,"
            "ZONING,LATITUDE,LONGITUDE,SUBDIVISION"
        ),
        order_by="OBJECTID",
    )
    if not rows:
        return pd.DataFrame(), pd.DataFrame()
    vb = pd.DataFrame(rows)
    print(f"    [vb] pulled {len(vb):,} multifamily parcels from VB MapServer")

    # ---- Step 2: HRGEO enrichment (owner + values + year built) ----
    hrgeo_url = (
        "https://geo.hrsd.com/hrgeo/rest/services/regionalgis/"
        "HRGeo_Parcels_Public/MapServer/0"
    )
    hrgeo_rows = _arcgis_query(
        hrgeo_url,
        where="SRCAGENCY='Virginia Beach'",
        out_fields=(
            "PARCELID,OWNERNME1,PSTLADDRESS,TOTVALUE,LNDVALUE,IMPVALUE,"
            "RESYRBLT,STATEDAREA,CALCAREA,PSTLZIP5"
        ),
    )
    print(f"    [vb] pulled {len(hrgeo_rows):,} HRGEO VB rows for enrichment")
    hrgeo = pd.DataFrame(hrgeo_rows) if hrgeo_rows else pd.DataFrame()

    # ---- Step 3: merge on GPIN ↔ PARCELID ----
    if not hrgeo.empty and "PARCELID" in hrgeo.columns:
        # HRGEO's PARCELID is a string of 14 digits; VB's PAR_GPIN same.
        hrgeo["_join_key"] = hrgeo["PARCELID"].astype(str).str.strip()
        vb["_join_key"] = vb["PAR_GPIN"].astype(str).str.strip()
        # De-dupe HRGEO on PARCELID (prevents merge explosion if HRGEO has
        # multi-row parcels)
        hrgeo_dedup = hrgeo.drop_duplicates("_join_key", keep="first")
        vb = vb.merge(
            hrgeo_dedup[[
                "_join_key", "OWNERNME1", "TOTVALUE", "LNDVALUE",
                "IMPVALUE", "RESYRBLT", "CALCAREA", "PSTLZIP5",
            ]],
            on="_join_key",
            how="left",
        )

    today_fy = dt.date.today().year + 1

    inv = pd.DataFrame({
        "city": "Virginia Beach",
        "parcel_id": vb.get("PAR_GPIN", pd.Series(dtype=str)).astype(str).str.strip(),
        "gpin": vb.get("PAR_GPIN", pd.Series(dtype=str)).astype(str).str.strip(),
        "address": vb.get("PROP_ADDRESS", pd.Series(dtype=str)).astype(str).str.strip(),
        "class_description": vb.get("LAND_USE", pd.Series(dtype=str)).astype(str).str.strip(),
        "property_use": vb.get("LAND_USE", pd.Series(dtype=str)).astype(str).str.strip(),
        "owner": vb.get("OWNERNME1", pd.Series(dtype=str)).astype(str).str.strip()
                  if "OWNERNME1" in vb.columns else pd.NA,
        "year_built": pd.to_numeric(vb.get("RESYRBLT"), errors="coerce")
                      if "RESYRBLT" in vb.columns else pd.NA,
        "acreage": pd.to_numeric(vb.get("CALCAREA"), errors="coerce")
                   if "CALCAREA" in vb.columns else pd.NA,
        "land_square_footage": pd.NA,
        "commercial_building_area": pd.NA,
        "assessed_value": pd.to_numeric(vb.get("TOTVALUE"), errors="coerce")
                          if "TOTVALUE" in vb.columns else pd.NA,
        "land_value": pd.to_numeric(vb.get("LNDVALUE"), errors="coerce")
                      if "LNDVALUE" in vb.columns else pd.NA,
        "improvement_value": pd.to_numeric(vb.get("IMPVALUE"), errors="coerce")
                             if "IMPVALUE" in vb.columns else pd.NA,
        "last_sale_date": pd.NA,
        "last_sale_price": pd.NA,
        "last_sale_buyer": pd.NA,
        "property_zip": (vb.get("PSTLZIP5") if "PSTLZIP5" in vb.columns else vb.get("ZIP", pd.Series(dtype=str))).astype(str).str.strip(),
        "latest_fiscal_year": today_fy,
    })
    inv = inv[inv["parcel_id"].astype(str).str.len() > 0]

    history = pd.DataFrame({
        "city": "Virginia Beach",
        "parcel_id": inv["parcel_id"],
        "gpin": inv["gpin"],
        "fiscal_year": today_fy,
        "assessed_value": inv["assessed_value"],
        "land_value": inv["land_value"],
        "improvement_value": inv["improvement_value"],
    }).dropna(subset=["assessed_value"])

    return inv, history


# ---------------------------------------------------------------------------
# HRGEO regional baseline — covers all 6 cities for basic fields
# ---------------------------------------------------------------------------

HRGEO_PARCELS_URL = (
    "https://geo.hrsd.com/hrgeo/rest/services/regionalgis/"
    "HRGeo_Parcels_Public/MapServer/0"
)
# Filter to multifamily-flavored zoning. ZONING values vary across cities;
# this is a permissive net.
HRGEO_MULTIFAMILY_ZONING = (
    "R-MF", "RMF", "RM", "MF", "R-MF1", "R-MF2",
    "MULTI", "APARTMENT", "RESIDENTIAL MULTI",
)


def pull_hrgeo_baseline(
    cities: tuple[str, ...] = (
        "Virginia Beach", "Suffolk", "Portsmouth",
    ),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """HRGEO regional layer — used for the 3 cities we don't have native
    pullers for yet (VB, Suffolk, Portsmouth). Returns whatever the
    regional layer exposes for those cities, filtered to multifamily zoning.

    Skipped cities (Norfolk, Chesapeake, Newport News, Hampton) have native
    pullers above — HRGEO would just duplicate.
    """
    print(f"  [va_assessors] pulling HRGEO regional baseline for {', '.join(cities)}…")
    # SRCAGENCY values match the city names per the discovery report.
    cities_quoted = ",".join(f"'{c}'" for c in cities)
    # Permissive zoning filter — HRGEO ZONING is free-text per source agency
    zoning_clause = " OR ".join(
        f"UPPER(ZONING) LIKE '%{z}%'" for z in HRGEO_MULTIFAMILY_ZONING
    )
    where = f"SRCAGENCY IN ({cities_quoted}) AND ({zoning_clause})"
    rows = _arcgis_query(HRGEO_PARCELS_URL, where=where)
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(rows)
    print(f"    [hrgeo] pulled {len(df):,} multifamily parcels (baseline)")

    today_fy = dt.date.today().year + 1
    # Compose address
    addr = df.get("PSTLADDRESS", pd.Series(dtype=str)).astype(str).str.strip()
    inv = pd.DataFrame({
        "city": df.get("SRCAGENCY", pd.Series(dtype=str)).astype(str).str.strip(),
        "parcel_id": df.get("PARCELID", pd.Series(dtype=str)).astype(str).str.strip(),
        "gpin": pd.NA,  # HRGEO does not include GPIN
        "address": addr,
        "class_description": df.get("ZONING", pd.Series(dtype=str)).astype(str).str.strip(),
        "property_use": df.get("ZONING", pd.Series(dtype=str)).astype(str).str.strip(),
        "owner": df.get("OWNERNME1", pd.Series(dtype=str)).astype(str).str.strip(),
        "year_built": pd.to_numeric(df.get("RESYRBLT"), errors="coerce"),
        "acreage": pd.to_numeric(df.get("CALCAREA"), errors="coerce"),
        "land_square_footage": pd.NA,
        "commercial_building_area": pd.NA,
        "assessed_value": pd.to_numeric(df.get("TOTVALUE"), errors="coerce"),
        "land_value": pd.to_numeric(df.get("LNDVALUE"), errors="coerce"),
        "improvement_value": pd.to_numeric(df.get("IMPVALUE"), errors="coerce"),
        "last_sale_date": pd.NA,
        "last_sale_price": pd.NA,
        "last_sale_buyer": pd.NA,
        "property_zip": df.get("PSTLZIP5", pd.Series(dtype=str)).astype(str).str.strip(),
        "latest_fiscal_year": today_fy,
    })
    inv = inv[inv["parcel_id"].astype(str).str.len() > 0]

    history = pd.DataFrame({
        "city": inv["city"],
        "parcel_id": inv["parcel_id"],
        "gpin": pd.NA,
        "fiscal_year": today_fy,
        "assessed_value": inv["assessed_value"],
        "land_value": inv["land_value"],
        "improvement_value": inv["improvement_value"],
    }).dropna(subset=["assessed_value"])

    return inv, history


# ---------------------------------------------------------------------------
# Per-city dispatch + top-level entrypoint
# ---------------------------------------------------------------------------

# When we add a new city, register its puller function here. Each function
# returns (inventory_df, history_df) in the same shape as `pull_norfolk`.
# Until implemented, the city falls through with a warning.
CITY_DISPATCH: dict[str, Any] = {
    "Norfolk": pull_norfolk,
    "Chesapeake": pull_chesapeake,
    "Newport News": pull_newport_news,
    "Hampton": pull_hampton,
    "Virginia Beach": pull_virginia_beach,
    # HRGEO baseline is implemented but DISABLED — its zoning-code filter
    # catches Suffolk's "RM" zoning (6,811 parcels including non-apartment
    # multifamily zoning) but Suffolk doesn't share TOTVALUE via HRGEO, and
    # VB/Portsmouth use zoning codes that don't match common MF prefixes.
    # Better to leave these 3 cities pending until we build proper native
    # pullers (VB Hub API, Suffolk Spatialest scrape, Portsmouth assessor
    # email request).
    # "HRGEO Baseline": pull_hrgeo_baseline,
}

# Cities still on the to-do list — referenced for status output.
PLANNED_CITIES = (
    "Portsmouth",       # ASPX scrape OR email assessor for CSV — defer
    "Suffolk",          # Native ArcGIS REST + Spatialest scrape — ~6 hrs build
)


def pull_va_assessors() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull all HR cities with assessor pullers implemented.

    Returns (inventory_df, history_df) with one row per (city, parcel_id)
    in inventory and (city, parcel_id, fiscal_year) in history. Cities still
    on the TODO list are logged but skipped.
    """
    inv_frames: list[pd.DataFrame] = []
    hist_frames: list[pd.DataFrame] = []

    for city, puller in CITY_DISPATCH.items():
        try:
            inv, hist = puller()
            if not inv.empty:
                inv_frames.append(inv)
            if not hist.empty:
                hist_frames.append(hist)
            print(f"  [va_assessors] {city}: {len(inv):,} parcels / {len(hist):,} FY records")
        except Exception as e:
            print(f"  [va_assessors] {city} FAILED: {type(e).__name__}: {e}")

    for planned in PLANNED_CITIES:
        if planned not in CITY_DISPATCH:
            print(f"  [va_assessors] {planned}: not yet implemented (TODO — see module docstring)")

    if not inv_frames:
        return pd.DataFrame(), pd.DataFrame()

    inventory = pd.concat(inv_frames, ignore_index=True, sort=False)
    history = (
        pd.concat(hist_frames, ignore_index=True, sort=False)
        if hist_frames else pd.DataFrame()
    )

    # ---- GLOBAL DEDUP SAFETY NET ----
    # Defense-in-depth: even if a city's puller missed a duplicate (Newport
    # News API returned parcel 154000401 twice in a single fetch on 2026-05-08)
    # this strips the dupes before they hit the DB. Primary key for the
    # workbench is (city, parcel_id) for inventory and (city, parcel_id,
    # fiscal_year) for history.
    inv_before = len(inventory)
    inventory = inventory.drop_duplicates(
        subset=["city", "parcel_id"], keep="first",
    )
    if len(inventory) < inv_before:
        print(f"  [va_assessors] global dedup removed "
              f"{inv_before - len(inventory)} duplicate inventory rows")

    if not history.empty:
        hist_before = len(history)
        history = history.drop_duplicates(
            subset=["city", "parcel_id", "fiscal_year"], keep="first",
        )
        if len(history) < hist_before:
            print(f"  [va_assessors] global dedup removed "
                  f"{hist_before - len(history)} duplicate history rows")

    # Stamp pull_date so the workbench can show "last refreshed" per row
    today = dt.date.today().isoformat()
    inventory["pull_date"] = today
    if not history.empty:
        history["pull_date"] = today

    return inventory, history
