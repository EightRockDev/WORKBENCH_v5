"""GRANITE Loans data layer (spec 6.1 Tabs 2-5): lender database, loan
comps, borrower intelligence - deterministic queries, empty on missing DBs."""

from __future__ import annotations

import sqlite3

from core import granite_loans as gl
from core.phase0 import _SPINE_SCHEMA


def _mk_etl(path):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE hmda_lender_summary (
            year INTEGER, lei TEXT, lender_name TEXT, county_code INTEGER,
            n_originations INTEGER, total_loan_amount REAL,
            median_loan_amount REAL, median_rate_spread REAL)""")
        conn.executemany(
            "INSERT INTO hmda_lender_summary VALUES (?,?,?,?,?,?,?,?)", [
                (2023, "L1", "TOWNE BANK", 51710, 12, 24e6, 1.8e6, 0.55),
                (2024, "L1", "TOWNE BANK", 51710, 15, 30e6, 2.0e6, 0.60),
                (2024, "L2", "ATLANTIC UNION", 51710, 6, 9e6, 1.4e6, 0.90),
                (2024, "L3", "VB ONLY BANK", 51810, 4, 5e6, 1.2e6, 1.10),
            ])
        conn.execute("""CREATE TABLE hmda_originations (
            lei TEXT, state_code TEXT, county_code INTEGER,
            census_tract TEXT, loan_amount REAL, loan_purpose INTEGER,
            dwelling_category TEXT, action_taken INTEGER, rate_spread REAL,
            loan_to_value REAL, year INTEGER, lender_name TEXT)""")
        conn.executemany(
            "INSERT INTO hmda_originations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("L1", "VA", 51710, "t", 2_500_000, 1, "MF", 1, 0.5, 70, 2024, "TOWNE BANK"),
                ("L1", "VA", 51710, "t", 900_000, 1, "MF", 1, 0.7, 65, 2023, "TOWNE BANK"),
                ("L2", "VA", 51710, "t", 5_000_000, 31, "MF", 3, None, None, 2024, "ATLANTIC UNION"),  # denied - excluded
                ("L3", "VA", 51810, "t", 1_200_000, 1, "MF", 1, 1.1, 75, 2024, "VB ONLY BANK"),
            ])
    return path


def test_lender_history_rolls_up_years_and_filters_by_city(tmp_path):
    etl = _mk_etl(tmp_path / "etl.db")
    rows = gl.lender_history("Norfolk", etl_path=etl)
    assert [r["lender_name"] for r in rows] == ["TOWNE BANK", "ATLANTIC UNION"]
    towne = rows[0]
    assert towne["originations"] == 27          # 12 + 15 across years
    assert towne["first_year"] == 2023 and towne["last_year"] == 2024
    assert 0.5 < towne["avg_rate_spread"] < 0.65
    # All-markets view includes the VB-only lender too.
    assert len(gl.lender_history(None, etl_path=etl)) == 3


def test_loan_comps_bands_by_amount_and_drops_non_originations(tmp_path):
    etl = _mk_etl(tmp_path / "etl.db")
    rows = gl.loan_comps("Norfolk", etl_path=etl)
    # The denied application (action_taken=3) never shows as a comp.
    assert [r["loan_amount"] for r in rows] == [2_500_000, 900_000]
    banded = gl.loan_comps("Norfolk", min_amount=1_000_000, etl_path=etl)
    assert [r["loan_amount"] for r in banded] == [2_500_000]
    assert gl.loan_comps("Norfolk", etl_path=tmp_path / "none.db") == []


def test_entity_portfolio_finds_footprint_on_the_backbone(tmp_path):
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(_SPINE_SCHEMA)
        conn.executemany(
            """INSERT INTO properties_8r
               (property_id, fips, address, city, state, units,
                assessed_value, owner_name, use_code, built_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", [
                ("8R-51710-a", "51710", "500 Granby St", "Norfolk", "VA",
                 120, 9e6, "GRANBY HOLDINGS LLC", "APARTMENT", "t"),
                ("8R-51710-b", "51710", "9 Duke St", "Norfolk", "VA",
                 24, 2e6, "GRANBY HOLDINGS II LLC", "APARTMENT", "t"),
                ("8R-51550-c", "51550", "1 Elm Ave", "Chesapeake", "VA",
                 8, 5e5, "OTHER OWNER", "DUPLEX", "t"),
            ])
    rows = gl.entity_portfolio("granby", db)
    assert [r["property_id"] for r in rows] == ["8R-51710-a", "8R-51710-b"]
    roll = gl.portfolio_rollup(rows)
    assert roll["parcels"] == 2 and roll["units"] == 144
    assert roll["cities"] == ["Norfolk"]
    # Too-short fragments never dragnet the county.
    assert gl.entity_portfolio("gr", db) == []
