"""Property Screener — one query surface over BOTH property pools.

The owner's ask (2026-08-25): a filter form that searches everything the
workbench knows about. That is two tables with different shapes:

  * ``properties``     — the curated records (name, class, management
                         company, occupancy, last-sold columns), and
  * ``properties_8r``  — the county backbone built by phase0 (address,
                         owner, units, year built; sales live separately
                         in ``sale_records``, keyed by normalized APN).

Rules that make one honest result set out of the two:

  * Free-text filters are case-insensitive CONTAINS ("Dolly" matches
    "Dolly Parton"), matching the Inventory tab's convention.
  * A filter a county record cannot answer (name, class, management
    company, occupancy) simply excludes county rows while it is in use —
    the owner chose this over pretending the data exists.
  * A property present in both pools appears ONCE, as its richer curated
    record: ``property_crosswalk`` maps curated ids to backbone ids, and
    every mapped backbone row is dropped.
  * Numeric filters exclude rows with the value missing — a range filter
    that kept unknowns would claim "built 1970-1990" about a property
    with no year on record.

All query logic lives here, not in the UI, so the tests can exercise it
against a temp database with no Streamlit involved (house convention —
see ui/granite_loans.py).
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from pathlib import Path
from typing import Any

from core.sale_history import _norm_apn

# Curated rows outrank county rows in the combined list; both sort by
# name/address within their pool. Cap defends the UI, not the query.
DEFAULT_LIMIT = 500

SOURCE_CURATED = "My records"
SOURCE_COUNTY = "County records"

# Filters only the curated pool can answer. Any of these being active
# excludes every county row (they have no such column to test).
_CURATED_ONLY = ("name", "asset_class", "management_company",
                 "occ_min", "occ_max")


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    return row is not None


def _contains(clauses: list[str], params: list[Any], col: str,
              text: str | None) -> None:
    """Case-insensitive substring match, NULL-safe."""
    q = (text or "").strip().lower()
    if not q:
        return
    clauses.append(f"LOWER(IFNULL({col},'')) LIKE ? ESCAPE '\\'")
    params.append("%" + q.replace("\\", "\\\\")
                         .replace("%", "\\%").replace("_", "\\_") + "%")


def _between(clauses: list[str], params: list[Any], col: str,
             lo: Any, hi: Any) -> None:
    """Range filter; when active, rows with the value missing drop out."""
    if lo is not None:
        clauses.append(f"{col} >= ?")
        params.append(lo)
    if hi is not None:
        clauses.append(f"{col} <= ?")
        params.append(hi)


def _active(filters: dict[str, Any], key: str) -> bool:
    v = filters.get(key)
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, set)):
        return bool(v)
    return v is not None


def _curated_rows(conn: sqlite3.Connection, f: dict[str, Any],
                  limit: int) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    _contains(clauses, params, "name", f.get("name"))
    _contains(clauses, params, "city", f.get("city"))
    _contains(clauses, params, "state", f.get("state"))
    _contains(clauses, params, "zip", f.get("zip"))
    _contains(clauses, params, "owner", f.get("owner"))
    _contains(clauses, params, "management_company",
              f.get("management_company"))
    if _active(f, "market"):
        sub: list[str] = []
        for col in ("market", "submarket"):
            _contains(sub, params, col, f.get("market"))
        clauses.append("(" + " OR ".join(sub) + ")")
    classes = [c for c in (f.get("asset_class") or []) if c]
    if classes:
        clauses.append("asset_class IN (%s)" % ",".join("?" * len(classes)))
        params.extend(classes)
    _between(clauses, params, "units", f.get("units_min"), f.get("units_max"))
    _between(clauses, params, "year_built",
             f.get("year_min"), f.get("year_max"))
    _between(clauses, params, "last_sold_amount",
             f.get("price_min"), f.get("price_max"))
    # The curated pool records a sale YEAR, not a date — compare on the
    # year of the requested bounds so "since 2015-06-01" means "2015+".
    _between(clauses, params, "last_sold_year",
             _year_of(f.get("date_from")), _year_of(f.get("date_to")))
    # UI passes whole percents; the column stores fractions (schema.sql).
    _between(clauses, params, "occupancy_pct",
             _frac(f.get("occ_min")), _frac(f.get("occ_max")))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM properties {where} ORDER BY name LIMIT ?",
        (*params, limit)).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        year = d.get("last_sold_year")
        out.append({
            "source": SOURCE_CURATED,
            "property_id": d.get("property_id"),
            "name": d.get("name"),
            "address": d.get("address"),
            "city": d.get("city"),
            "state": d.get("state"),
            "zip": d.get("zip"),
            "units": d.get("units"),
            "year_built": d.get("year_built"),
            "asset_class": d.get("asset_class"),
            "market": d.get("market"),
            "owner": d.get("owner"),
            "management_company": d.get("management_company"),
            "occupancy_pct": d.get("occupancy_pct"),
            "sale_price": d.get("last_sold_amount"),
            "sale_when": str(int(year)) if year else None,
        })
    return out


def _year_of(iso: str | None) -> int | None:
    s = (iso or "").strip()
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None


_US_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")


def parse_date(text: Any) -> str | None:
    """Normalize user date text to ISO, or None when it isn't a date.

    Both pools MUST see the same value: before this existed, "6/1/2015"
    silently disabled the curated year filter while lexicographically
    garbage-filtering the county pool to zero — one typed value, two
    contradictory wrong answers (review 2026-08-25). Accepts ISO and the
    US M/D/YYYY the owner actually types.
    """
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    m = _US_DATE.match(s)
    if m:
        mo, dy, yr = (int(g) for g in m.groups())
        try:
            return _dt.date(yr, mo, dy).isoformat()
        except ValueError:
            return None
    return None


def _frac(pct: Any) -> float | None:
    return None if pct is None else float(pct) / 100.0


def _county_rows(conn: sqlite3.Connection, f: dict[str, Any],
                 limit: int) -> list[dict[str, Any]]:
    if not _has_table(conn, "properties_8r"):
        return []
    # A curated-only filter in use means no county row can qualify.
    if any(_active(f, k) for k in _CURATED_ONLY):
        return []

    clauses: list[str] = []
    params: list[Any] = []
    _contains(clauses, params, "p.city", f.get("city"))
    _contains(clauses, params, "p.state", f.get("state"))
    _contains(clauses, params, "p.zip", f.get("zip"))
    _contains(clauses, params, "p.owner_name", f.get("owner"))
    if _active(f, "market"):
        sub: list[str] = []
        for col in ("p.r8_market", "p.r8_submarket"):
            _contains(sub, params, col, f.get("market"))
        clauses.append("(" + " OR ".join(sub) + ")")
    _between(clauses, params, "p.units",
             f.get("units_min"), f.get("units_max"))
    _between(clauses, params, "p.year_built",
             f.get("year_min"), f.get("year_max"))

    # Drop every backbone row the crosswalk maps to a curated record —
    # that property is already in the curated half of the results.
    if _has_table(conn, "property_crosswalk"):
        clauses.append("p.property_id NOT IN "
                       "(SELECT r8_property_id FROM property_crosswalk)")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sale_filter = any(_active(f, k)
                      for k in ("price_min", "price_max",
                                "date_from", "date_to"))
    have_sales = _has_table(conn, "sale_records")
    if sale_filter and not have_sales:
        return []  # can't answer a sale filter without the sale index

    if have_sales:
        # County sales live in sale_records keyed by normalized APN
        # (core/sale_index.py). The normalizer is Python, so register it
        # with sqlite; it runs only on rows that survive the WHERE above.
        conn.create_function("er_norm_apn", 1, _norm_apn)
        sale_clauses: list[str] = []
        _between(sale_clauses, params, "s.price",
                 f.get("price_min"), f.get("price_max"))
        _between(sale_clauses, params, "s.date",
                 f.get("date_from"), f.get("date_to"))
        sale_where = (" WHERE " + " AND ".join(sale_clauses)) \
            if sale_clauses else ""
        sql = f"""
            WITH base AS (
                SELECT * FROM properties_8r p {where}
            ),
            latest AS (
                -- apn_norm='' is real data: address-only sales land there
                -- (core/sale_index.py). Left in, every APN-less backbone
                -- row would borrow the county's newest address-only sale
                -- through a ''='' join. Price DESC breaks same-day ties
                -- deterministically.
                SELECT apn_norm, price, date FROM (
                    SELECT apn_norm, price, date,
                           ROW_NUMBER() OVER (PARTITION BY apn_norm
                                              ORDER BY date DESC,
                                                       price DESC) rn
                    FROM sale_records
                    WHERE price IS NOT NULL AND price > 0
                      AND apn_norm IS NOT NULL AND apn_norm <> ''
                ) WHERE rn = 1
            )
            SELECT base.*, s.price AS sale_price, s.date AS sale_date
            FROM base LEFT JOIN latest s
                 ON s.apn_norm = er_norm_apn(base.apn)
            {sale_where}
            ORDER BY base.address LIMIT ?
        """
    else:
        sql = (f"SELECT p.*, NULL AS sale_price, NULL AS sale_date "
               f"FROM properties_8r p {where} "
               f"ORDER BY p.address LIMIT ?")
    rows = conn.execute(sql, (*params, limit)).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        when = d.get("sale_date")
        out.append({
            "source": SOURCE_COUNTY,
            "property_id": d.get("property_id"),
            "name": d.get("address"),  # county records have no name
            "address": d.get("address"),
            "city": d.get("city"),
            "state": d.get("state"),
            "zip": d.get("zip"),
            "units": d.get("units"),
            "year_built": d.get("year_built"),
            "asset_class": None,
            "market": d.get("r8_market"),
            "owner": d.get("owner_name"),
            "management_company": None,
            "occupancy_pct": None,
            "sale_price": d.get("sale_price"),
            "sale_when": str(when)[:10] if when else None,
        })
    return out


def run_screener(filters: dict[str, Any], *, db_path: Path | str,
                 limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Search both pools and return one deduplicated, capped list."""
    # Normalize the date bounds ONCE so both pools answer the same
    # question; unparseable text deactivates the filter in both.
    filters = dict(filters)
    filters["date_from"] = parse_date(filters.get("date_from"))
    filters["date_to"] = parse_date(filters.get("date_to"))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        curated = _curated_rows(conn, filters, limit)
        county = _county_rows(conn, filters, limit)
    finally:
        conn.close()
    return (curated + county)[:limit]
