"""FRED — macro time series relevant to multifamily.

For Eight Rock: 10-year Treasury, 30-year mortgage rates, CPI shelter
component, and HR-MSA HPI all feed our exit-cap and rent-growth assumptions.

Source: FRED API (https://api.stlouisfed.org/fred/series/observations).
Requires `FRED_API_KEY` (free at https://fred.stlouisfed.org/docs/api/api_key.html).
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import requests

FRED_API = "https://api.stlouisfed.org/fred/series/observations"

# Series IDs to pull. Daily series get a 5-year window; lower-frequency get 10.
FRED_SERIES = {
    "DGS10":     ("10yr_treasury_yield", 5),     # daily — last 5 years
    "MORTGAGE30US": ("30yr_mortgage_rate", 5),   # weekly
    "CPIAUCSL":  ("cpi_all_urban", 10),          # monthly
    "CUUR0000SAH1": ("cpi_shelter", 10),         # monthly
    "ATNHPIUS47260Q": ("hpi_hampton_roads_msa", 15),  # quarterly
}


def pull_fred() -> pd.DataFrame:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("  [fred] FRED_API_KEY not set — skipping")
        return pd.DataFrame()

    today = dt.date.today()
    rows: list[dict] = []
    for series_id, (friendly, years_back) in FRED_SERIES.items():
        start_date = (today.replace(year=today.year - years_back)).isoformat()
        try:
            r = requests.get(
                FRED_API,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    # observation_start avoids 500 errors on long daily series
                    # (FRED returns 500 when the result set gets too large).
                    "observation_start": start_date,
                },
                timeout=60,
            )
            r.raise_for_status()
            obs = r.json().get("observations", [])
            for o in obs:
                if o["value"] in (".", "", None):
                    continue
                try:
                    rows.append({
                        "series_id": series_id,
                        "series_name": friendly,
                        "date": o["date"],
                        "value": float(o["value"]),
                    })
                except (TypeError, ValueError):
                    continue
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"  [fred] {series_id} failed: {e}")
            continue

    return pd.DataFrame(rows)
