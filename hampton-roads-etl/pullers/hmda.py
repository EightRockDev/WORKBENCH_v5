"""FFIEC HMDA — multifamily loan originations by lender, by tract.

Two tables:
  - `hmda_originations`: every multifamily origination in HR, last 3 years
  - `hmda_lender_summary`: derived rollup — top lenders by city + year

Source: FFIEC Data Browser API (no auth required for public modified LAR).
  https://ffiec.cfpb.gov/v2/data-browser-api/view/csv

For Eight Rock, this is lender competitive intelligence — who's actually
closing multifamily loans in Norfolk Class C right now, at what spreads.
"""

from __future__ import annotations

import datetime as dt
import io
import os

import pandas as pd
import requests

from config import HAMPTON_ROADS, hr_county_fips_5

HMDA_API = "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"

# GLEIF lookup is the slowest part of this puller (one HTTP call per unique
# LEI, ~3s each). Set HMDA_RESOLVE_LENDER_NAMES=0 in .env to skip it.
# Read at function-call time (not module-import) so .env changes between runs
# are picked up. Strip+lowercase so trailing whitespace / case variation
# can't accidentally re-enable the slow path.
def _should_resolve_lenders() -> bool:
    val = os.environ.get("HMDA_RESOLVE_LENDER_NAMES", "1")
    val = val.strip().strip('"').strip("'").lower() if val else ""
    return val not in ("0", "false", "no", "off", "")


def _fetch_year(year: int, county_fips_5: tuple[str, ...]) -> pd.DataFrame:
    """Fetch one year of multifamily originations for the given counties."""
    params = {
        "states": "VA",
        "counties": ",".join(county_fips_5),
        "years": str(year),
        "actions_taken": "1",  # 1 = originated
        # Multifamily filter — parameter name has changed across versions;
        # try the modern one and fall back if rejected.
        "dwelling_categories": "Multifamily:Site-Built",
    }
    try:
        r = requests.get(HMDA_API, params=params, timeout=120)
        if r.status_code == 400:
            # Older parameter; total_units >= 5 is the legacy multifamily marker
            params.pop("dwelling_categories", None)
            params["total_units"] = "5-1000"
            r = requests.get(HMDA_API, params=params, timeout=120)
        r.raise_for_status()
        return pd.read_csv(io.StringIO(r.text), low_memory=False)
    except (requests.RequestException, pd.errors.ParserError) as e:
        print(f"  [hmda] {year} fetch failed: {e}")
        return pd.DataFrame()


def _resolve_lei(lei: str) -> str | None:
    """Resolve an LEI to a legal name via GLEIF. Best-effort, no auth."""
    if not lei or pd.isna(lei):
        return None
    try:
        r = requests.get(
            f"https://api.gleif.org/api/v1/lei-records/{lei}",
            timeout=15,
            headers={"Accept": "application/vnd.api+json"},
        )
        if r.status_code == 200:
            return r.json().get("data", {}).get("attributes", {}).get("entity", {}).get("legalName", {}).get("name")
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def pull_hmda() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull last 3 years of HR multifamily originations.
    Returns (originations_df, lender_summary_df)."""
    today = dt.date.today()
    counties = hr_county_fips_5()

    all_years: list[pd.DataFrame] = []
    for year in range(today.year - 3, today.year):
        df = _fetch_year(year, counties)
        if df.empty:
            continue
        df["year"] = year
        all_years.append(df)

    if not all_years:
        print("  [hmda] no data pulled across any year")
        return pd.DataFrame(), pd.DataFrame()

    raw = pd.concat(all_years, ignore_index=True)

    # Normalize column names HMDA uses; fall back gracefully if missing.
    col_map = {
        "lei": "lei",
        "legal_entity_identifier_lei_": "lei",
        "state_code": "state_code",
        "county_code": "county_code",
        "census_tract": "census_tract",
        "loan_amount": "loan_amount",
        "loan_purpose": "loan_purpose",
        "dwelling_category": "dwelling_category",
        "action_taken": "action_taken",
        "rate_spread": "rate_spread",
        "loan_to_value_ratio": "loan_to_value",
    }
    keep = [c for c in raw.columns if c in col_map]
    if not keep:
        print("  [hmda] columns don't match expected schema — endpoint may have shifted")
        return pd.DataFrame(), pd.DataFrame()
    norm = raw[keep + ["year"]].rename(columns=col_map)

    # Coerce numerics — HMDA's CSV occasionally has 'NA', 'Exempt', or empty
    # strings that pandas reads as `object` dtype. The downstream median/sum
    # aggregations require numeric columns.
    for ncol in ("loan_amount", "rate_spread", "loan_to_value"):
        if ncol in norm.columns:
            norm[ncol] = pd.to_numeric(norm[ncol], errors="coerce")

    # Resolve LEI → lender name. Cache so we don't re-hit GLEIF per row.
    if _should_resolve_lenders():
        unique_leis = [str(x) for x in norm["lei"].dropna().unique() if x]
        print(f"  [hmda] resolving {len(unique_leis)} unique LEIs via GLEIF…")
        lei_cache: dict[str, str | None] = {}
        for i, lei in enumerate(unique_leis, 1):
            lei_cache[lei] = _resolve_lei(lei)
            if i % 25 == 0:
                print(f"  [hmda]   resolved {i}/{len(unique_leis)} LEIs")
        norm["lender_name"] = norm["lei"].apply(lambda x: lei_cache.get(str(x)))
    else:
        print("  [hmda] HMDA_RESOLVE_LENDER_NAMES=0 — skipping GLEIF name resolution")
        norm["lender_name"] = None

    # Originations table — keep raw rows
    orig = norm.copy()

    # Lender summary — derived rollup per (year, lei, county). Use numeric_only
    # to skip non-numeric columns just in case any other column is object dtype.
    summary = (
        orig.groupby(["year", "lei", "lender_name", "county_code"], dropna=False)
        .agg(
            n_originations=("loan_amount", "count"),
            total_loan_amount=("loan_amount", lambda s: s.dropna().sum()),
            median_loan_amount=("loan_amount", lambda s: s.dropna().median()),
            median_rate_spread=("rate_spread", lambda s: s.dropna().median()),
        )
        .reset_index()
    )

    return orig, summary
