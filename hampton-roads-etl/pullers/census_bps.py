"""Census Building Permits Survey (BPS) — multifamily permits by city.

Pulls last 5 years of monthly Place-level permit data for Virginia, filters
to the 7 Hampton Roads cities, writes to `census_bps` table.

Headline number for Eight Rock: `units_5punit` (5+ unit multifamily permits).
This is the supply pipeline 18-24 months out — multifamily permits are a
leading indicator of rent growth deceleration.

Source: https://www2.census.gov/econ/bps/Place/South%20Region/
Format: monthly comma-delimited CSV, 3 header rows, files named
`so{YY}{MM}c.txt` (current month) or `so{YY}{MM}y.txt` (year-to-date).
We use the `c` (monthly) variant. Verified working as of 2026-05-07
through file so2603c.txt.

Columns (0-indexed, 29 total in modern format):
   0  Survey Date (YYYYMM)
   1  State FIPS                          ← key
   2  6-digit county ID
   3  County FIPS                         ← key
   4  Census Place Code
   5  FIPS Place                          ← key (filter on this)
   6  FIPS MCD
   7  Population
   8  CSA Code
   9  CBSA Code
  10  Footnote
  11  Central City flag
  12  ZIP Code
  13  Region Code
  14  Division Code
  15  Source Code
  16  Place Name                          ← key
  17  1-unit Bldgs       18 Units    19 Value
  20  2-unit Bldgs       21 Units    22 Value
  23  3-4 unit Bldgs     24 Units    25 Value
  26  5+ unit Bldgs      27 Units    28 Value     ← Eight Rock cares about these
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

import pandas as pd
import requests

from config import HAMPTON_ROADS

# 5-digit place FIPS for the seven HR cities → name lookup
HR_PLACE_FIPS = {p.fips_place: p.name for p in HAMPTON_ROADS}

# BPS regional grouping — Virginia is in the "South Region" (with a space).
# Note: the URL has a literal space, URL-encoded as %20. The directory was
# renamed from `South_Region/` to `South Region/` at some point — old code
# 404s without this fix.
BPS_BASE = "https://www2.census.gov/econ/bps/Place/South%20Region"

# Census blocks default Python User-Agent with timeouts. A browser-like UA
# returns immediately. Used for every request from this module.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "hampton-roads-etl/1.0 (+research)"
    ),
}

# Column indexes per BPS Place file (verified 2024+ format, 29 cols).
# Column positions are stable across modern years — only file naming has changed.
BPS_COLS = {
    1: "fips_state",
    3: "fips_county",
    5: "fips_place",
    16: "place_name",
    17: "bldgs_1unit",
    18: "units_1unit",
    20: "bldgs_2unit",
    21: "units_2unit",
    23: "bldgs_3to4unit",
    24: "units_3to4unit",
    26: "bldgs_5punit",
    27: "units_5punit",
    28: "valuation_5punit",
}


def _try_fetch_month(year: int, month: int) -> pd.DataFrame | None:
    """Fetch one monthly Place-level file. Returns None on 404 / parse error."""
    yy = f"{year % 100:02d}"
    mm = f"{month:02d}"
    url = f"{BPS_BASE}/so{yy}{mm}c.txt"

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        return None

    if r.status_code != 200 or len(r.content) < 500:
        return None

    try:
        df = pd.read_csv(
            io.StringIO(r.text),
            skiprows=3,            # 2 column-name rows + 1 blank
            header=None,
            low_memory=False,
            on_bad_lines="skip",
            dtype=str,             # parse numerics ourselves; preserves leading zeros
        )
    except (pd.errors.ParserError, ValueError):
        return None

    df.attrs["source_url"] = url
    df.attrs["year"] = year
    df.attrs["month"] = month
    return df


def pull_census_bps() -> pd.DataFrame:
    """Pull last 5 years of monthly BPS data for Hampton Roads. Returns
    a DataFrame ready for `db.write()`."""
    today = dt.date.today()
    rows: list[dict[str, Any]] = []
    found_count = 0
    miss_count = 0
    consecutive_misses = 0

    # Last 5 years × 12 months. Skip the current month (often not yet posted).
    # If we hit 6 consecutive misses, bail early — Census has likely changed
    # the URL pattern again.
    for year in range(today.year - 4, today.year + 1):
        for month in range(1, 13):
            if year == today.year and month >= today.month:
                break
            try:
                df = _try_fetch_month(year, month)
                if df is None:
                    miss_count += 1
                    consecutive_misses += 1
                    if consecutive_misses == 6 and found_count == 0:
                        print(
                            "  [census_bps] 6 consecutive misses with no hits — "
                            "Census URL pattern likely shifted. Bailing out early. "
                            "Verify at https://www2.census.gov/econ/bps/Place/"
                        )
                        return pd.DataFrame()
                    continue
                consecutive_misses = 0
                found_count += 1
                if found_count <= 3 or found_count % 12 == 0:
                    print(f"  [census_bps] fetched {year}-{month:02d}")

                # Make sure all expected columns exist (older files may have
                # fewer columns); fill missing positions with None.
                for idx in BPS_COLS:
                    if idx >= df.shape[1]:
                        df[idx] = None

                df = df.rename(columns=BPS_COLS)

                # Filter to HR. fips_place + fips_state both come in with
                # padded whitespace (file is fixed-width-ish); strip + zfill.
                # IMPORTANT: must filter on BOTH state AND place — many
                # FIPS Place codes are reused across states (e.g. 57000 is
                # both Norfolk VA and Norfolk MA-style cities elsewhere).
                df["fips_place"] = (
                    df["fips_place"].fillna("").astype(str).str.strip().str.zfill(5)
                )
                df["fips_state"] = (
                    df["fips_state"].fillna("").astype(str).str.strip().str.zfill(2)
                )
                hr_mask = (df["fips_state"] == "51") & df["fips_place"].isin(HR_PLACE_FIPS.keys())
                hr = df[hr_mask].copy()
                if hr.empty:
                    continue

                # Coerce numeric columns
                for col in ("bldgs_1unit", "units_1unit", "bldgs_2unit",
                            "units_2unit", "bldgs_3to4unit", "units_3to4unit",
                            "bldgs_5punit", "units_5punit", "valuation_5punit"):
                    hr[col] = (
                        pd.to_numeric(hr[col], errors="coerce")
                        .fillna(0)
                        .astype(int)
                    )

                # Strip whitespace from string columns
                for col in ("fips_state", "fips_county", "place_name"):
                    hr[col] = hr[col].fillna("").astype(str).str.strip()

                hr["year"] = year
                hr["month"] = month
                keep = ["year", "month", "fips_state", "fips_county", "fips_place",
                        "place_name",
                        "bldgs_1unit", "units_1unit", "bldgs_2unit", "units_2unit",
                        "bldgs_3to4unit", "units_3to4unit", "bldgs_5punit",
                        "units_5punit", "valuation_5punit"]
                rows.extend(hr[keep].to_dict(orient="records"))
            except Exception as e:
                print(f"  [census_bps] {year}-{month:02d} failed: {e}")
                continue

    print(f"  [census_bps] fetched {found_count} months, missed {miss_count}")
    if not rows:
        print(
            "  [census_bps] no rows pulled — Census BPS URL pattern may have shifted. "
            "Verify at https://www2.census.gov/econ/bps/Place/"
        )
        return pd.DataFrame()

    return pd.DataFrame(rows)
