"""Surface helpers for the Hampton Roads ETL database (`hampton_roads.db`).

The ETL pipeline lives at `<workbench-root>/hampton-roads-etl/` and writes
to its own SQLite file. This module is the *only* place in the Streamlit
workbench that touches that DB — all UI panels go through these helpers
so the read paths and error fallbacks live in one spot.

If the ETL DB doesn't exist (fresh checkout, ETL never run) every helper
returns `None` or an empty DataFrame, and the UI silently hides the panel.

Mapping of ETL tables → workbench panels:
  bah_rates + bah_zip_mha   → Property Detail "Military Floor"
  census_bps                → Property Detail "Supply Pipeline"
  hud_fmr                   → Property Detail "HUD Fair Market Rent"
  hmda_lender_summary       → Property Detail "Active Lenders"
  fred_series               → Underwriting "Macro Context"
  bls_laus                  → Property Detail "Local Unemployment"
  hud_lihtc                 → Comps "Nearby LIHTC Compliance" (future)
  census_acs                → not yet wired (puller blocked on API key)
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path

import pandas as pd

from core import etl_db

# Location is resolved by `core/etl_db.py` ($ER_ETL_DB, then `data/`, then
# `hampton-roads-etl/`, then the legacy v2.4.1 sibling folder). Kept as a module
# constant for callers that report the path; the helpers below re-resolve on
# every call, so dropping the file in and restarting the app is enough.
ETL_DB_PATH = etl_db.resolve_etl_db() or etl_db.preferred_location()

# Independent-city VA county FIPS for HR cities. Mirrors hampton-roads-etl/config.py
# but copied here so the workbench doesn't import from the ETL package.
HR_CITY_TO_COUNTY_FIPS_5 = {
    "Norfolk":        "51710",
    "Virginia Beach": "51810",
    "Chesapeake":     "51550",
    "Portsmouth":     "51740",
    "Suffolk":        "51800",
    "Hampton":        "51650",
    "Newport News":   "51700",
}


def is_etl_available() -> bool:
    """True if the ETL database exists and has at least one expected table."""
    path = etl_db.resolve_etl_db()
    if path is None:
        return False
    try:
        with sqlite3.connect(path) as db:
            cur = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('bah_rates','census_bps','hud_fmr',"
                "'hmda_lender_summary','fred_series','bls_laus','hud_lihtc')"
            )
            return len(cur.fetchall()) > 0
    except sqlite3.Error:
        return False


def _connect() -> sqlite3.Connection | None:
    """Open the ETL DB read-only, or return None if it doesn't exist."""
    path = etl_db.resolve_etl_db()
    if path is None:
        return None
    try:
        # Read-only via URI so the workbench never accidentally writes here.
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# BAH — military housing floor by ZIP
# ---------------------------------------------------------------------------

# E-5 with-deps is the Norfolk Class C anchor — a married petty officer with
# kids is the median military tenant. E-6/E-7 widen the band.
BAH_FLOOR_PAYGRADES = ("E5", "E6", "E7")


def get_bah_floor_for_zip(
    zip_code: str | None,
    paygrades: tuple[str, ...] = BAH_FLOOR_PAYGRADES,
    with_dependents: int = 1,
) -> pd.DataFrame:
    """Return the with-dependents BAH rates for a property's ZIP.

    Columns: paygrade, monthly_rate, mha_code, mha_name, effective_year.
    Latest year only. Returns empty DataFrame if ZIP isn't in the crosswalk.
    """
    if not zip_code:
        return pd.DataFrame()
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        zip5 = str(zip_code).strip().zfill(5)
        placeholders = ",".join("?" * len(paygrades))
        df = pd.read_sql(
            f"""
            SELECT br.paygrade,
                   br.monthly_rate,
                   br.mha_code,
                   br.mha_name,
                   br.effective_year
            FROM bah_rates br
            JOIN bah_zip_mha bzm
                 ON br.mha_code = bzm.mha_code
                AND br.effective_year = bzm.effective_year
            WHERE bzm.zip_code = ?
              AND br.paygrade IN ({placeholders})
              AND br.with_dependents = ?
              AND br.effective_year = (
                  SELECT MAX(effective_year) FROM bah_rates
              )
            ORDER BY br.paygrade
            """,
            db,
            params=(zip5, *paygrades, with_dependents),
        )
        return df
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# BPS — multifamily supply pipeline
# ---------------------------------------------------------------------------

def get_supply_pipeline(
    city: str | None,
    months_back: int = 12,
) -> dict | None:
    """Return TTM 5+ unit permit summary for a city.

    Shape:
        {
            "city": "Norfolk",
            "ttm_units": 9,
            "ttm_bldgs": 1,
            "ttm_value": 1_862_196,
            "months_with_permits": 1,
            "latest_permit_month": "2025-08",
            "monthly": [{"year":..., "month":..., "units_5punit":..., ...}, ...],
        }
    """
    if not city:
        return None
    db = _connect()
    if db is None:
        return None
    try:
        cur = db.execute("SELECT MAX(year * 100 + month) FROM census_bps")
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        latest_ym = int(row[0])
        cutoff_ym = latest_ym - months_back + 1
        df = pd.read_sql(
            """
            SELECT year, month, units_5punit, bldgs_5punit, valuation_5punit
            FROM census_bps
            WHERE place_name = ?
              AND fips_state = '51'
              AND (year * 100 + month) >= ?
            ORDER BY year DESC, month DESC
            """,
            db,
            params=(city, cutoff_ym),
        )
        if df.empty:
            return None

        ttm_units = int(df["units_5punit"].sum())
        ttm_bldgs = int(df["bldgs_5punit"].sum())
        ttm_value = int(df["valuation_5punit"].sum())
        months_with = int((df["units_5punit"] > 0).sum())

        with_permits = df[df["units_5punit"] > 0]
        latest = (
            f"{int(with_permits.iloc[0]['year'])}-{int(with_permits.iloc[0]['month']):02d}"
            if not with_permits.empty else None
        )

        return {
            "city": city,
            "ttm_units": ttm_units,
            "ttm_bldgs": ttm_bldgs,
            "ttm_value": ttm_value,
            "months_with_permits": months_with,
            "latest_permit_month": latest,
            "monthly": df.to_dict(orient="records"),
        }
    except (sqlite3.Error, pd.errors.DatabaseError):
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# HUD FMR — Fair Market Rent
# ---------------------------------------------------------------------------

def get_fmr_for_county(city: str | None) -> dict | None:
    """Return latest-year FMR rates (efficiency–4BR) for the county containing
    the city, as a dict keyed by bedroom count."""
    if not city:
        return None
    county_fips = HR_CITY_TO_COUNTY_FIPS_5.get(city)
    if not county_fips:
        return None
    db = _connect()
    if db is None:
        return None
    try:
        cur = db.execute(
            """
            SELECT year, fmr_efficiency, fmr_one_bedroom, fmr_two_bedroom,
                   fmr_three_bedroom, fmr_four_bedroom
            FROM hud_fmr
            WHERE fips_county_5 = ?
            ORDER BY year DESC
            LIMIT 1
            """,
            (county_fips,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "year": int(row[0]),
            "efficiency": int(row[1]) if row[1] is not None else None,
            "one_bedroom": int(row[2]) if row[2] is not None else None,
            "two_bedroom": int(row[3]) if row[3] is not None else None,
            "three_bedroom": int(row[4]) if row[4] is not None else None,
            "four_bedroom": int(row[5]) if row[5] is not None else None,
        }
    except sqlite3.Error:
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# HMDA — top multifamily lenders in the county
# ---------------------------------------------------------------------------

def get_top_multifamily_lenders(city: str | None, top_n: int = 5) -> pd.DataFrame:
    """Top-N multifamily lenders by # of originations in the city's county,
    aggregated across all years in the HMDA dataset.
    """
    if not city:
        return pd.DataFrame()
    county_fips = HR_CITY_TO_COUNTY_FIPS_5.get(city)
    if not county_fips:
        return pd.DataFrame()
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        # county_code in the HMDA table is INTEGER (51710 not '51710')
        df = pd.read_sql(
            """
            SELECT lender_name,
                   lei,
                   SUM(n_originations) AS originations,
                   SUM(total_loan_amount) AS total_volume,
                   AVG(median_loan_amount) AS avg_median_loan
            FROM hmda_lender_summary
            WHERE county_code = ?
            GROUP BY lei, lender_name
            ORDER BY originations DESC
            LIMIT ?
            """,
            db,
            params=(int(county_fips), top_n),
        )
        return df
    except (sqlite3.Error, pd.errors.DatabaseError, ValueError):
        return pd.DataFrame()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FRED — macro context
# ---------------------------------------------------------------------------

# Series → display label
FRED_HEADLINE_SERIES = {
    "DGS10":         "10-Year Treasury",
    "MORTGAGE30US":  "30-Year Mortgage",
    "ATNHPIUS47260Q": "Hampton Roads MSA Home Price Index",
}


@lru_cache(maxsize=1)
def get_macro_indicators() -> pd.DataFrame:
    """Latest observation for each headline FRED series.

    Returns columns: series_id, label, date, value.
    Cached for the session — these don't change between reruns.
    Caller can call `get_macro_indicators.cache_clear()` to force a refresh.
    """
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        placeholders = ",".join("?" * len(FRED_HEADLINE_SERIES))
        df = pd.read_sql(
            f"""
            SELECT f.series_id, f.date, f.value
            FROM fred_series f
            JOIN (
                SELECT series_id, MAX(date) AS max_date
                FROM fred_series
                WHERE series_id IN ({placeholders})
                  AND value IS NOT NULL
                GROUP BY series_id
            ) m
              ON f.series_id = m.series_id AND f.date = m.max_date
            ORDER BY f.series_id
            """,
            db,
            params=tuple(FRED_HEADLINE_SERIES.keys()),
        )
        if df.empty:
            return df
        df["label"] = df["series_id"].map(FRED_HEADLINE_SERIES)
        return df
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# BLS LAUS — local unemployment
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LIHTC — nearby tax-credit properties (off-market opportunities when
# compliance is ending)
# ---------------------------------------------------------------------------

def get_nearby_lihtc(
    subject_lat: float | None,
    subject_lng: float | None,
    max_miles: float = 5.0,
    limit: int = 25,
) -> pd.DataFrame:
    """Return LIHTC properties within `max_miles` of the subject, sorted by
    distance, with derived years-until-compliance-end columns.

    Eight Rock relevance: LIHTC properties exiting their 15-yr initial
    compliance period are off-market value-add opportunities. Filter the
    returned df to `years_to_initial_end <= 5` for the actionable window.
    """
    if subject_lat is None or subject_lng is None:
        return pd.DataFrame()
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        # Pull all VA LIHTC with coords; filter by distance in pandas (1k rows).
        df = pd.read_sql(
            """
            SELECT lihtc_id, project_name, address, city, zip_code,
                   n_units, n_lihtc_units, year_placed_in_service,
                   credit_type, nonprofit_sponsor,
                   compliance_period_start,
                   initial_compliance_end, extended_use_end,
                   latitude, longitude
            FROM hud_lihtc
            WHERE state = 'VA'
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            """,
            db,
        )
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()

    if df.empty:
        return df

    # Haversine distance in miles
    import math
    lat0 = math.radians(subject_lat)
    lng0 = math.radians(subject_lng)

    def _miles(row) -> float:
        try:
            lat1 = math.radians(float(row["latitude"]))
            lng1 = math.radians(float(row["longitude"]))
            dlat = lat1 - lat0
            dlng = lng1 - lng0
            a = math.sin(dlat / 2) ** 2 + math.cos(lat0) * math.cos(lat1) * math.sin(dlng / 2) ** 2
            return 7917.5 * math.asin(math.sqrt(a))  # 2 * earth radius (mi) * asin(...)
        except (TypeError, ValueError):
            return float("inf")

    df["distance_miles"] = df.apply(_miles, axis=1)
    df = df[df["distance_miles"] <= max_miles].copy()
    if df.empty:
        return df

    # Years-to-compliance-end columns (relative to today)
    import datetime as dt
    today_year = dt.date.today().year
    df["years_to_initial_end"] = df["initial_compliance_end"] - today_year
    df["years_to_extended_end"] = df["extended_use_end"] - today_year

    df = df.sort_values("distance_miles").head(limit).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Permits trend — for sparkline charts
# ---------------------------------------------------------------------------

def get_permits_trend_for_city(
    city: str | None,
    months_back: int = 36,
) -> pd.DataFrame:
    """Monthly 5+ unit permit history for a city. Returns columns:
    year_month (YYYY-MM string), units_5p, bldgs_5p."""
    if not city:
        return pd.DataFrame()
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        cur = db.execute("SELECT MAX(year * 100 + month) FROM census_bps")
        row = cur.fetchone()
        if not row or row[0] is None:
            return pd.DataFrame()
        latest_ym = int(row[0])
        cutoff_ym = latest_ym - months_back + 1
        df = pd.read_sql(
            """
            SELECT year, month, units_5punit, bldgs_5punit
            FROM census_bps
            WHERE place_name = ?
              AND fips_state = '51'
              AND (year * 100 + month) >= ?
            ORDER BY year, month
            """,
            db,
            params=(city, cutoff_ym),
        )
        if df.empty:
            return df
        df["year_month"] = df.apply(
            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1,
        )
        df = df.rename(columns={"units_5punit": "units_5p", "bldgs_5punit": "bldgs_5p"})
        return df[["year_month", "units_5p", "bldgs_5p"]]
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


def get_hr_aggregate_permits_trend(months_back: int = 36) -> pd.DataFrame:
    """All Hampton Roads cities aggregated, monthly. For the sidebar sparkline."""
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        cur = db.execute("SELECT MAX(year * 100 + month) FROM census_bps")
        row = cur.fetchone()
        if not row or row[0] is None:
            return pd.DataFrame()
        latest_ym = int(row[0])
        cutoff_ym = latest_ym - months_back + 1
        df = pd.read_sql(
            """
            SELECT year, month,
                   SUM(units_5punit) AS units_5p,
                   SUM(bldgs_5punit) AS bldgs_5p
            FROM census_bps
            WHERE fips_state = '51'
              AND (year * 100 + month) >= ?
            GROUP BY year, month
            ORDER BY year, month
            """,
            db,
            params=(cutoff_ym,),
        )
        if df.empty:
            return df
        df["year_month"] = df.apply(
            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1,
        )
        return df[["year_month", "units_5p", "bldgs_5p"]]
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


def get_acs_for_city(city: str | None) -> dict | None:
    """Latest Census ACS 5-year demographics for a Hampton Roads city.

    Returns a dict with population, median household income, owner/renter
    occupied counts (computed renter %), median gross rent, total / vacant
    housing units, and the ACS year. None if no row matches.
    """
    if not city:
        return None
    db = _connect()
    if db is None:
        return None
    try:
        # ACS rows have place_name like "Norfolk city, Virginia"
        cur = db.execute(
            """
            SELECT total_population, median_household_income,
                   owner_occupied_hh, renter_occupied_hh,
                   median_gross_rent, total_housing_units,
                   vacant_housing_units, acs_year
            FROM census_acs
            WHERE place_name LIKE ?
            ORDER BY acs_year DESC
            LIMIT 1
            """,
            (f"{city}%",),
        )
        row = cur.fetchone()
        if not row:
            return None
        pop, mhi, own, ren, med_rent, total_hh, vacant_hh, year = row
        try:
            renter_pct = (
                float(ren) / (float(own) + float(ren))
                if own and ren else None
            )
        except (TypeError, ZeroDivisionError):
            renter_pct = None
        try:
            vacancy_pct = (
                float(vacant_hh) / float(total_hh)
                if total_hh else None
            )
        except (TypeError, ZeroDivisionError):
            vacancy_pct = None
        return {
            "population": int(pop) if pop else None,
            "median_household_income": int(mhi) if mhi else None,
            "owner_occupied_hh": int(own) if own else None,
            "renter_occupied_hh": int(ren) if ren else None,
            "renter_pct": renter_pct,
            "median_gross_rent": int(med_rent) if med_rent else None,
            "total_housing_units": int(total_hh) if total_hh else None,
            "vacant_housing_units": int(vacant_hh) if vacant_hh else None,
            "vacancy_pct": vacancy_pct,
            "acs_year": int(year) if year else None,
        }
    except (sqlite3.Error, pd.errors.DatabaseError):
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Multifamily inventory (HR City Assessors) — comprehensive property list
# ---------------------------------------------------------------------------

def get_multifamily_inventory(
    city: str | None = None,
    class_filter: str | None = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """Return rows from `va_multifamily_inventory`.

    Filters:
      city: e.g. "Norfolk" — limits to one HR city
      class_filter: substring match on `class_description` (e.g. "405" or "49+")
    """
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        where: list[str] = []
        params: list = []
        if city:
            where.append("city = ?")
            params.append(city)
        if class_filter:
            where.append("class_description LIKE ?")
            params.append(f"%{class_filter}%")
        sql = "SELECT * FROM va_multifamily_inventory"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY assessed_value DESC LIMIT ?"
        params.append(limit)
        df = pd.read_sql(sql, db, params=params)
        return df
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


def get_recent_assessment_jumps(
    city: str | None = None,
    min_pct: float = 0.20,
    limit: int = 50,
) -> pd.DataFrame:
    """Properties whose latest-FY assessed value jumped by ≥ `min_pct` vs
    the prior FY. Catches reassessments triggered by sales — these are
    where ownership likely just changed hands (= name-change candidates).

    Returns columns: city, parcel_id, gpin, address, owner, prior_fy,
    prior_value, latest_fy, latest_value, jump_pct.
    """
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        where_city = "AND inv.city = ?" if city else ""
        params: list = [min_pct, limit]
        if city:
            params.insert(0, city)
        sql = f"""
            WITH ranked AS (
                SELECT city, parcel_id, fiscal_year, assessed_value,
                       ROW_NUMBER() OVER (PARTITION BY city, parcel_id
                                          ORDER BY fiscal_year DESC) AS rn
                FROM va_assessment_history
                WHERE assessed_value > 0
            ),
            paired AS (
                SELECT a.city, a.parcel_id,
                       a.fiscal_year AS latest_fy, a.assessed_value AS latest_value,
                       b.fiscal_year AS prior_fy, b.assessed_value AS prior_value,
                       (a.assessed_value - b.assessed_value) * 1.0 / b.assessed_value AS jump_pct
                FROM ranked a
                JOIN ranked b ON a.city = b.city AND a.parcel_id = b.parcel_id
                              AND a.rn = 1 AND b.rn = 2
                WHERE b.assessed_value > 0
            )
            SELECT inv.city, inv.parcel_id, inv.gpin, inv.address, inv.owner,
                   inv.class_description, inv.year_built,
                   p.prior_fy, p.prior_value,
                   p.latest_fy, p.latest_value, p.jump_pct
            FROM paired p
            JOIN va_multifamily_inventory inv
              ON inv.city = p.city AND inv.parcel_id = p.parcel_id
            WHERE p.jump_pct >= ?
            {where_city}
            ORDER BY p.jump_pct DESC
            LIMIT ?
        """
        # Reorder params: city should come BEFORE jump_pct in the WHERE
        # We restructure the params to match parameter placeholder order:
        #   [min_pct, (city if any), limit]
        ordered_params: list = [min_pct]
        if city:
            ordered_params.append(city)
        ordered_params.append(limit)
        df = pd.read_sql(sql, db, params=ordered_params)
        return df
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


def get_etl_metadata() -> pd.DataFrame:
    """Return the `etl_metadata` sidecar table — provenance for every ETL
    table the pipeline writes. Columns: table_name, display_name,
    description, source_url, fetch_method, row_count, last_pull_at,
    last_pull_date.

    Returns an empty DataFrame if the metadata table doesn't exist (which
    happens before the first puller run with the metadata-tracking write fn).
    """
    db = _connect()
    if db is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql(
            "SELECT * FROM etl_metadata ORDER BY display_name",
            db,
        )
        return df
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()
    finally:
        db.close()


def get_local_unemployment(city: str | None) -> dict | None:
    """Latest unemployment rate for the city's county.

    Returns {"rate": 3.2, "year": 2025, "month": 12, "city": "Norfolk"}
    or None if not found.
    """
    if not city:
        return None
    db = _connect()
    if db is None:
        return None
    try:
        cur = db.execute(
            """
            SELECT unemployment_rate_pct, year, month
            FROM bls_laus
            WHERE county = ?
              AND unemployment_rate_pct IS NOT NULL
            ORDER BY year DESC, month DESC
            LIMIT 1
            """,
            (city,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "rate": float(row[0]),
            "year": int(row[1]),
            "month": int(row[2]),
            "city": city,
        }
    except sqlite3.Error:
        return None
    finally:
        db.close()
