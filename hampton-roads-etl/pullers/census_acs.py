"""Census ACS — population, income, household demographics by HR county.

For Eight Rock: tracks demographic shifts in target submarkets — population
growth, median household income, renter share, age distribution. Tells us
whether the tenant base is growing or shrinking under our properties.

Source: Census Bureau API (https://api.census.gov/data/...).
Requires `CENSUS_API_KEY` (free at https://api.census.gov/data/key_signup.html).
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import requests

from config import HAMPTON_ROADS

# Most recent ACS 5-year estimates available — typically lag by ~18 months
ACS_BASE = "https://api.census.gov/data/{year}/acs/acs5"

# Subset of ACS variables most relevant to multifamily underwriting
ACS_VARS = {
    "B01003_001E": "total_population",
    "B19013_001E": "median_household_income",
    "B25003_002E": "owner_occupied_hh",
    "B25003_003E": "renter_occupied_hh",
    "B25064_001E": "median_gross_rent",
    "B25001_001E": "total_housing_units",
    "B25002_003E": "vacant_housing_units",
}


def pull_census_acs() -> pd.DataFrame:
    """Pull most recent ACS 5-year estimates for HR counties. Returns DataFrame."""
    api_key = os.environ.get("CENSUS_API_KEY")
    if not api_key:
        print("  [census_acs] CENSUS_API_KEY not set — skipping. Get a free key at"
              " https://api.census.gov/data/key_signup.html")
        return pd.DataFrame()

    today = dt.date.today()
    counties = ",".join(p.fips_county for p in HAMPTON_ROADS)
    var_codes = ",".join(["NAME"] + list(ACS_VARS.keys()))

    # Try most-recent year first; fall back two more years (release schedule varies)
    last_err = None
    for year_offset in (2, 3, 4):
        year = today.year - year_offset
        url = ACS_BASE.format(year=year)
        params = {
            "get": var_codes,
            "for": f"county:{counties}",
            "in": "state:51",
            "key": api_key,
        }
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code != 200:
                last_err = f"{r.status_code} {r.reason}: {r.text[:200]}"
                continue
            # Census returns plain-text errors with HTTP 200 sometimes;
            # JSON parse will catch those.
            data = r.json()
            print(f"  [census_acs] using ACS 5-year for {year}")
            break
        except (requests.RequestException, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}; body={r.text[:200] if 'r' in locals() else 'no response'}"
            continue
    else:
        print(f"  [census_acs] all year offsets failed. Last error: {last_err}")
        return pd.DataFrame()

    if not data or not isinstance(data, list) or len(data) < 2:
        print(f"  [census_acs] unexpected response shape: {str(data)[:200]}")
        return pd.DataFrame()

    header = data[0]
    rows = data[1:]
    df = pd.DataFrame(rows, columns=header)

    # Rename ACS variable codes to human-readable names
    df = df.rename(columns={**ACS_VARS, "NAME": "place_name"})

    # Coerce numeric columns
    for col in ACS_VARS.values():
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["acs_year"] = year
    df["fips_state"] = "51"
    return df
