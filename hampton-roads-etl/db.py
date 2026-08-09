"""Shared SQLite write helper.

All pullers funnel through `write()` so the table-replace + pull_date pattern
is consistent across Phase 1 and Phase 2. Writes also stamp the
`etl_metadata` sidecar table with a full ISO timestamp + source URL +
description so the workbench can surface data provenance.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3
from typing import Any

import pandas as pd

from config import DB_PATH

# Per-table provenance metadata. The workbench's Comps tab reads this back
# to render a "Data Sources" panel with the source URL and last-pull time.
TABLE_PROVENANCE: dict[str, dict[str, str]] = {
    "bah_rates": {
        "display_name": "DoD BAH Rates",
        "description": "Military housing allowance rates by Military Housing Area (MHA), paygrade, dependent status",
        "source_url": "https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/",
        "fetch_method": "Manual download (DoD blocks bots)",
    },
    "bah_zip_mha": {
        "display_name": "DoD BAH ZIP→MHA Crosswalk",
        "description": "ZIP code to Military Housing Area lookup table",
        "source_url": "https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/",
        "fetch_method": "Manual download",
    },
    "census_bps": {
        "display_name": "Census Building Permits Survey (BPS)",
        "description": "Monthly multifamily permits by city (Hampton Roads, Place-level)",
        "source_url": "https://www2.census.gov/econ/bps/Place/South%20Region/",
        "fetch_method": "Direct file fetch (per-month so{YY}{MM}c.txt)",
    },
    "hud_fmr": {
        "display_name": "HUD Fair Market Rent",
        "description": "Annual FMR by HR county, by bedroom count",
        "source_url": "https://www.huduser.gov/hudapi/public/fmr/statedata/VA",
        "fetch_method": "HUD User API (Bearer token)",
    },
    "hmda_originations": {
        "display_name": "FFIEC HMDA Originations",
        "description": "Multifamily loan originations by lender, county, year (last 3 years)",
        "source_url": "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv",
        "fetch_method": "FFIEC Data Browser API (no auth)",
    },
    "hmda_lender_summary": {
        "display_name": "FFIEC HMDA Lender Summary",
        "description": "Top multifamily lenders aggregated by county + year + LEI (resolved via GLEIF)",
        "source_url": "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv",
        "fetch_method": "FFIEC + GLEIF lookup",
    },
    "fred_series": {
        "display_name": "FRED Macro Series",
        "description": "10Y Treasury, 30Y Mortgage, CPI, Fed Funds, HR HPI, etc.",
        "source_url": "https://api.stlouisfed.org/fred/series/observations",
        "fetch_method": "St. Louis Fed FRED API (free key)",
    },
    "bls_laus": {
        "display_name": "BLS Local Area Unemployment Statistics",
        "description": "Monthly unemployment rate by HR city",
        "source_url": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        "fetch_method": "BLS Public Data API (free key)",
    },
    "hud_lihtc": {
        "display_name": "HUD LIHTC Database",
        "description": "Every Virginia LIHTC property since 1987 with placed-in-service year and compliance windows",
        "source_url": "https://egis.hud.gov/arcgis/rest/services/affht/AffhtMapService/MapServer/30/query",
        "fetch_method": "HUD ArcGIS REST endpoint (no auth)",
    },
    "census_acs": {
        "display_name": "Census ACS 5-Year",
        "description": "Tract-level demographics (population, income, household composition)",
        "source_url": "https://api.census.gov/data/2023/acs/acs5",
        "fetch_method": "Census Data API (free key)",
    },
    "va_multifamily_inventory": {
        "display_name": "HR Multifamily Inventory (City Assessors)",
        "description": "Comprehensive list of every multifamily parcel in HR cities — class codes 401–407 (5+ unit residential). Latest fiscal-year snapshot per parcel: address, owner, GPIN, year built, current values, last sale.",
        "source_url": "https://data.norfolk.gov/ (Norfolk only as of v0.45; other HR cities planned)",
        "fetch_method": "City open-data portals (Socrata, ESRI, scrape)",
    },
    "va_assessment_history": {
        "display_name": "HR Assessment History (City Assessors)",
        "description": "FY-by-FY assessed value history for every multifamily parcel — feeds the Subject tab's Tax Assessment History panel and underwrites post-sale reassessment risk.",
        "source_url": "https://data.norfolk.gov/ (Norfolk only as of v0.45; other HR cities planned)",
        "fetch_method": "City open-data portals (per-FY datasets)",
    },
    "rent_listings": {
        "display_name": "Rent Listings (in-house scraper)",
        "description": "Live asking rents + concessions per property from public listing aggregators (Apartments.com initially; Zillow + Rent.com to follow). Effective rent computed as asking × (lease − months_free) / lease.",
        "source_url": "Apartments.com (others added in week 2)",
        "fetch_method": "Polite HTTP scrape (3-sec delay, robots.txt respect, rotated UA). Eight Rock's HelloData alternative.",
    },
}


def _ensure_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etl_metadata (
            table_name      TEXT PRIMARY KEY,
            display_name    TEXT,
            description     TEXT,
            source_url      TEXT,
            fetch_method    TEXT,
            row_count       INTEGER,
            last_pull_at    TEXT,  -- ISO 8601 with seconds, local time
            last_pull_date  TEXT   -- date-only convenience copy
        )
        """
    )


def _record_metadata(
    conn: sqlite3.Connection,
    table: str,
    row_count: int,
    when: dt.datetime,
) -> None:
    """Upsert a row into `etl_metadata` after `write()` finishes."""
    _ensure_metadata_table(conn)
    prov = TABLE_PROVENANCE.get(table, {})
    conn.execute(
        """
        INSERT INTO etl_metadata
            (table_name, display_name, description, source_url, fetch_method,
             row_count, last_pull_at, last_pull_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            display_name = excluded.display_name,
            description = excluded.description,
            source_url = excluded.source_url,
            fetch_method = excluded.fetch_method,
            row_count = excluded.row_count,
            last_pull_at = excluded.last_pull_at,
            last_pull_date = excluded.last_pull_date
        """,
        (
            table,
            prov.get("display_name", table),
            prov.get("description", ""),
            prov.get("source_url", ""),
            prov.get("fetch_method", ""),
            row_count,
            when.isoformat(timespec="seconds"),
            when.date().isoformat(),
        ),
    )


def write(
    df: pd.DataFrame,
    table: str,
    *,
    db_path: pathlib.Path = DB_PATH,
    if_exists: str = "replace",
) -> int:
    """Write a DataFrame to SQLite, stamping a `pull_date` column AND
    upserting an `etl_metadata` row with the source URL + ISO timestamp.

    Idempotent: defaults to `if_exists="replace"` so re-running a puller
    overwrites the prior pull. The `pull_date` column is added if missing.

    Returns the number of rows written.
    """
    if df.empty:
        return 0
    now = dt.datetime.now()
    if "pull_date" not in df.columns:
        df = df.assign(pull_date=now.date().isoformat())

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        df.to_sql(table, conn, if_exists=if_exists, index=False)
        _record_metadata(conn, table, len(df), now)
    return len(df)


def query(sql: str, *params: Any, db_path: pathlib.Path = DB_PATH) -> list[dict]:
    """Run a read-only SQL query, returning rows as dicts."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def backfill_metadata(db_path: pathlib.Path = DB_PATH) -> int:
    """One-shot backfill of `etl_metadata` for tables that already exist
    but were written before the metadata-tracking change.

    Uses the existing tables' row counts and a single fallback timestamp
    (the DB file's mtime, which represents the most-recent write across all
    tables). Future writes via `write()` will overwrite with accurate
    per-table timestamps. Returns the number of rows upserted.
    """
    if not db_path.is_file():
        return 0

    fallback_ts = dt.datetime.fromtimestamp(db_path.stat().st_mtime)
    with sqlite3.connect(db_path) as conn:
        _ensure_metadata_table(conn)
        # Find every user-data table currently in the DB
        existing = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT IN ('etl_metadata', 'sqlite_sequence')"
            ).fetchall()
        }
        n = 0
        for table in TABLE_PROVENANCE:
            if table not in existing:
                continue
            try:
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue
            _record_metadata(conn, table, row_count, fallback_ts)
            n += 1
    return n
