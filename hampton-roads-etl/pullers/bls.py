"""BLS LAUS — local-area unemployment by HR county, monthly.

For Eight Rock: rising unemployment is an early signal of rent collection
risk and vacancy creep, especially in Class C tenant bases.

Source: BLS API (https://api.bls.gov/publicAPI/v2/timeseries/data/).
Series IDs follow pattern LAUCN51{county}0000000003 (unemployment rate).
Requires `BLS_API_KEY` (free at https://data.bls.gov/registrationEngine/).
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import requests

from config import HAMPTON_ROADS

BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def pull_bls_laus() -> pd.DataFrame:
    """Pull last 36 months of unemployment rate for each HR county."""
    api_key = os.environ.get("BLS_API_KEY")
    if not api_key:
        print("  [bls] BLS_API_KEY not set — skipping")
        return pd.DataFrame()

    today = dt.date.today()
    series_ids = [f"LAUCN51{p.fips_county}0000000003" for p in HAMPTON_ROADS]
    series_to_county = {sid: p.name for sid, p in zip(series_ids, HAMPTON_ROADS)}

    payload = {
        "seriesid": series_ids,
        "startyear": str(today.year - 3),
        "endyear": str(today.year),
        "registrationkey": api_key,
    }

    try:
        r = requests.post(BLS_API, json=payload, timeout=60)
        r.raise_for_status()
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  [bls] API failed: {e}")
        return pd.DataFrame()

    if body.get("status") != "REQUEST_SUCCEEDED":
        print(f"  [bls] BLS returned non-success: {body.get('message')}")
        return pd.DataFrame()

    rows = []
    for series in body["Results"]["series"]:
        sid = series["seriesID"]
        county = series_to_county.get(sid, sid)
        for d in series["data"]:
            # BLS returns '-' (or sometimes '' or 'N/A') for missing values
            raw = (d.get("value") or "").strip()
            try:
                rate = float(raw) if raw and raw != "-" else None
            except ValueError:
                rate = None
            rows.append({
                "series_id": sid,
                "county": county,
                "year": int(d["year"]),
                "period": d["period"],          # M01, M02, ..., M12
                "month": int(d["period"][1:]) if d["period"].startswith("M") else None,
                "unemployment_rate_pct": rate,
            })
    return pd.DataFrame(rows)
