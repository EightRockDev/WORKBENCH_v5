"""GRANITE Loans — the loan-module data layer (spec 6.1, Tabs 2-5).

Deterministic queries only (spec 11: core never needs an LLM). Three
surfaces, each backed by data already in hand:

  * Lender database  - hmda_lender_summary (ETL db), multi-year, incl.
                       median rate spread (pulled but never read before).
  * Loan comps       - hmda_originations (ETL db): 74K+ rows that had
                       ZERO readers until this module.
  * Borrower intel   - properties_8r.owner_name (the self-sourced
                       backbone): an entity's whole Hampton Roads
                       footprint in one query, feeding Module A.

Every function returns plain lists/dicts and degrades to empty when a
database is absent (fresh checkout, ETL not copied) - the UI hides what
it cannot source, never errors.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core import etl_db
from core.market_data import HR_CITY_TO_COUNTY_FIPS_5


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def lender_history(city: str | None = None, top_n: int = 25,
                   etl_path: Path | None = None) -> list[dict]:
    """Per-lender rollup across all years - the lender DATABASE, not a
    5-row teaser. Includes median rate spread and the years active."""
    path = etl_path or etl_db.resolve_etl_db()
    if path is None:
        return []
    fips = HR_CITY_TO_COUNTY_FIPS_5.get(city) if city else None
    where, params = "", []
    if fips:
        where = "WHERE county_code = ?"
        params.append(int(fips))
    try:
        with _ro(path) as db:
            rows = db.execute(
                f"""SELECT lender_name,
                           SUM(n_originations)      AS originations,
                           SUM(total_loan_amount)   AS total_volume,
                           AVG(median_loan_amount)  AS avg_median_loan,
                           AVG(median_rate_spread)  AS avg_rate_spread,
                           MIN(year) AS first_year, MAX(year) AS last_year
                      FROM hmda_lender_summary {where}
                     GROUP BY lender_name
                     ORDER BY originations DESC LIMIT ?""",
                (*params, top_n)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def loan_comps(city: str, min_amount: float | None = None,
               max_amount: float | None = None, top_n: int = 100,
               etl_path: Path | None = None) -> list[dict]:
    """Comparable originations for a county: amount, LTV, rate spread,
    purpose, lender, year. The raw hmda_originations rows, newest first,
    optionally banded by loan amount to bracket a subject deal."""
    path = etl_path or etl_db.resolve_etl_db()
    fips = HR_CITY_TO_COUNTY_FIPS_5.get(city)
    if path is None or fips is None:
        return []
    where = ["county_code = ?", "action_taken = 1"]
    params: list = [int(fips)]
    if min_amount is not None:
        where.append("loan_amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        where.append("loan_amount <= ?")
        params.append(max_amount)
    try:
        with _ro(path) as db:
            rows = db.execute(
                f"""SELECT year, lender_name, loan_amount, loan_to_value,
                           rate_spread, loan_purpose
                      FROM hmda_originations
                     WHERE {' AND '.join(where)}
                     ORDER BY year DESC, loan_amount DESC LIMIT ?""",
                (*params, top_n)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def entity_portfolio(name_fragment: str, db_path: Path,
                     limit: int = 200) -> list[dict]:
    """Borrower intelligence, step 1: an owner entity's full footprint on
    the self-sourced backbone. Case-insensitive substring on owner_name;
    returns each parcel with units/value/coords so the UI can rank the
    portfolio and hand any row to Module A's resolve_contacts."""
    frag = (name_fragment or "").strip()
    if len(frag) < 3:      # 1-2 chars would match half the county
        return []
    try:
        with _ro(db_path) as db:
            rows = db.execute(
                """SELECT property_id, owner_name, address, city, units,
                          year_built, assessed_value, use_code, lat, lng
                     FROM properties_8r
                    WHERE owner_name LIKE ? COLLATE NOCASE
                    ORDER BY COALESCE(units, 0) DESC,
                             COALESCE(assessed_value, 0) DESC
                    LIMIT ?""",
                (f"%{frag}%", limit)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def portfolio_rollup(rows: list[dict]) -> dict:
    """One-line summary of an entity's holdings for the intel header."""
    return {
        "parcels": len(rows),
        "units": sum(int(r.get("units") or 0) for r in rows),
        "assessed_value": sum(float(r.get("assessed_value") or 0)
                              for r in rows),
        "cities": sorted({r.get("city") for r in rows if r.get("city")}),
        "owners": sorted({r.get("owner_name") for r in rows
                          if r.get("owner_name")})[:8],
    }
