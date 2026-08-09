"""HUD LIHTC database — every Virginia LIHTC property since 1987.

For Eight Rock: deals coming off LIHTC compliance (15-yr initial, 30-yr
extended use) are some of the best off-market value-add opportunities in
the state, and they're scheduled years in advance.

Source: HUD's ArcGIS REST endpoint at egis.hud.gov serves the LIHTC dataset
in JSON, which is more reliable than the legacy ZIP download (which has
moved several times). Verified working as of 2026-05-07.

  https://egis.hud.gov/arcgis/rest/services/affht/AffhtMapService/MapServer/30/query

Schema additions (derived):
  - compliance_period_start = year_placed_in_service
  - initial_compliance_end  = PIS year + 15
  - extended_use_end        = PIS year + 30 (default; varies by LURA)
"""

from __future__ import annotations

import pandas as pd
import requests

# HUD's ArcGIS LIHTC layer (ID 30 of the AffhtMapService).
LIHTC_ARCGIS = (
    "https://egis.hud.gov/arcgis/rest/services/affht/"
    "AffhtMapService/MapServer/30/query"
)


def _fetch_va_lihtc() -> pd.DataFrame:
    """Pull all Virginia LIHTC properties via HUD's ArcGIS REST endpoint."""
    # ArcGIS pages results — we ask for the max + offset until empty
    rows: list[dict] = []
    offset = 0
    page_size = 2000
    while True:
        try:
            r = requests.get(
                LIHTC_ARCGIS,
                params={
                    "where": "PROJ_ST='VA'",
                    "outFields": "*",
                    "outSR": "4326",            # WGS84 lat/lng
                    "f": "json",
                    "resultOffset": offset,
                    "resultRecordCount": page_size,
                },
                timeout=60,
            )
            r.raise_for_status()
            body = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  [lihtc] ArcGIS query failed: {e}")
            return pd.DataFrame()

        features = body.get("features") or []
        if not features:
            break

        for feat in features:
            attrs = feat.get("attributes") or {}
            geom = feat.get("geometry") or {}
            attrs["_lat"] = geom.get("y")
            attrs["_lng"] = geom.get("x")
            rows.append(attrs)

        if len(features) < page_size:
            break
        offset += page_size

    return pd.DataFrame(rows)


def pull_lihtc() -> pd.DataFrame:
    """Pull all Virginia LIHTC projects, derive compliance dates."""
    raw = _fetch_va_lihtc()
    if raw.empty:
        print("  [lihtc] ArcGIS query returned no rows for VA — endpoint may have shifted")
        return pd.DataFrame()

    # Normalize HUD's all-uppercase column names → our schema
    cols_lower = {c: c.lower() for c in raw.columns}
    df = raw.rename(columns=cols_lower)

    out = pd.DataFrame()
    out["lihtc_id"] = df.get("hud_id", df.get("lihtc_id", "")).astype(str)
    out["project_name"] = df.get("project", "").astype(str)
    out["address"] = df.get("proj_add", "").astype(str)
    out["city"] = df.get("proj_cty", "").astype(str)
    out["state"] = "VA"
    out["zip_code"] = df.get("proj_zip", "").astype(str).str.zfill(5)
    out["fips_state"] = "51"
    fips = df.get("fips2010")
    if fips is not None:
        out["fips_county"] = fips.astype(str).str[2:5]
    else:
        out["fips_county"] = None
    out["latitude"] = pd.to_numeric(
        df.get("latitude", df.get("_lat")), errors="coerce"
    )
    out["longitude"] = pd.to_numeric(
        df.get("longitude", df.get("_lng")), errors="coerce"
    )
    out["n_units"] = pd.to_numeric(df.get("n_units"), errors="coerce")
    out["n_lihtc_units"] = pd.to_numeric(df.get("li_units"), errors="coerce")
    out["year_placed_in_service"] = pd.to_numeric(df.get("yr_pis"), errors="coerce")
    out["year_allocated"] = pd.to_numeric(df.get("yr_alloc"), errors="coerce")
    out["credit_type"] = df.get("type", "").astype(str)
    out["nonprofit_sponsor"] = pd.to_numeric(
        df.get("non_prof"), errors="coerce"
    ).fillna(0).astype(int)

    # Derived compliance dates — initial 15-yr period + default 30-yr extended use
    out["compliance_period_start"] = out["year_placed_in_service"]
    out["initial_compliance_end"] = out["year_placed_in_service"] + 15
    out["extended_use_end"] = out["year_placed_in_service"] + 30

    return out
