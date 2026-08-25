"""Property Screener query layer (core/screener.py).

The screen's promise, in the owner's words: type "Dolly" and get every
record containing "Dolly"; filters a county record can't answer exclude
it; a property in both pools shows up once. Each promise is a test
against a temp database shaped like the real one.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.screener import SOURCE_COUNTY, SOURCE_CURATED, run_screener


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "workbench.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE properties (
            property_id TEXT PRIMARY KEY, name TEXT, address TEXT,
            city TEXT, state TEXT, zip TEXT, units INTEGER,
            year_built INTEGER, asset_class TEXT, market TEXT,
            submarket TEXT, owner TEXT, management_company TEXT,
            occupancy_pct REAL, last_sold_year INTEGER,
            last_sold_amount REAL);
        CREATE TABLE properties_8r (
            property_id TEXT PRIMARY KEY, fips TEXT, apn TEXT,
            address TEXT, city TEXT, state TEXT, zip TEXT,
            units INTEGER, year_built INTEGER, r8_market TEXT,
            r8_submarket TEXT, owner_name TEXT);
        CREATE TABLE property_crosswalk (
            legacy_property_id TEXT PRIMARY KEY,
            r8_property_id TEXT NOT NULL);
        CREATE TABLE sale_records (
            apn_norm TEXT, addr_norm TEXT, date TEXT, price REAL);
    """)
    conn.executemany(
        "INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("P1", "Grand Hampton at Langley", "611 Michigan Dr", "Hampton",
             "VA", "23669", 192, 1967, "C", "Hampton Roads", "Peninsula",
             "Dolly Parton Holdings LLC", "Dollywood Mgmt", 0.94,
             2019, 21_500_000),
            ("P2", "Osprey Landing", "100 Bay Ave", "Norfolk", "VA",
             "23503", 88, 1985, "B", "Hampton Roads", "Southside",
             "Bayfront Partners", "Harbor Mgmt", 0.97, 2021, 9_000_000),
        ])
    conn.executemany(
        "INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # Crosswalked twin of P1 — must never appear alongside it.
            ("8R-A", "51650", "1000-01", "611 MICHIGAN DR", "Hampton",
             "VA", "23669", 192, 1967, "Hampton Roads", None,
             "DOLLY PARTON HOLDINGS LLC"),
            # County-only rows.
            ("8R-B", "51760", "N000-123/045", "1 BROAD ST", "Richmond",
             "VA", "23219", 40, 1930, "Richmond", None,
             "RIVER CITY APTS LLC"),
            ("8R-C", "51710", "2000-77", "9 GRANBY ST", "Norfolk", "VA",
             "23510", 250, 1972, "Hampton Roads", None, "GRANBY TOWERS LP"),
        ])
    conn.execute("INSERT INTO property_crosswalk VALUES ('P1', '8R-A')")
    conn.executemany(
        "INSERT INTO sale_records VALUES (?,?,?,?)",
        [
            # apn_norm = lowercased alphanumerics of the APN (sale_history).
            ("n00012345", "1 broad st", "2016-03-01", 2_400_000),
            ("n00012345", "1 broad st", "2009-01-15", 900_000),  # older
            ("200077", "9 granby st", "2022-08-30", 31_000_000),
        ])
    conn.commit()
    conn.close()
    return path


def _by_id(rows):
    return {r["property_id"]: r for r in rows}


def test_no_filters_returns_both_pools_deduplicated(db):
    rows = run_screener({}, db_path=db)
    ids = _by_id(rows)
    assert set(ids) == {"P1", "P2", "8R-B", "8R-C"}, (
        "expected both pools minus the crosswalked county twin of P1")
    assert ids["P1"]["source"] == SOURCE_CURATED
    assert ids["8R-B"]["source"] == SOURCE_COUNTY


def test_free_text_is_case_insensitive_contains(db):
    """The owner's own example: "Dolly" finds "Dolly Parton"."""
    rows = run_screener({"owner": "dolly"}, db_path=db)
    assert [r["property_id"] for r in rows] == ["P1"], (
        "substring owner search missed Dolly Parton Holdings")


def test_owner_search_reaches_county_records(db):
    rows = run_screener({"owner": "granby"}, db_path=db)
    assert [r["property_id"] for r in rows] == ["8R-C"]


def test_curated_only_filters_exclude_county_rows(db):
    """Class exists only on curated records — filtering on it must not
    pretend county rows have one."""
    rows = run_screener({"asset_class": ["B", "C"]}, db_path=db)
    assert {r["source"] for r in rows} == {SOURCE_CURATED}
    assert {r["property_id"] for r in rows} == {"P1", "P2"}


def test_unit_range_spans_both_pools(db):
    rows = run_screener({"units_min": 100}, db_path=db)
    assert {r["property_id"] for r in rows} == {"P1", "8R-C"}


def test_year_built_range(db):
    rows = run_screener({"year_min": 1980, "year_max": 1990}, db_path=db)
    assert {r["property_id"] for r in rows} == {"P2"}


def test_sale_price_filter_uses_the_latest_county_sale(db):
    """8R-B sold twice; only the LATEST sale ($2.4M, 2016) counts. A
    min above it must drop the row even though an older sale matches
    nothing — and 8R-C's $31M sale stays in."""
    rows = run_screener({"price_min": 10_000_000}, db_path=db)
    assert {r["property_id"] for r in rows} == {"P1", "8R-C"}
    prices = {r["property_id"]: r["sale_price"] for r in rows}
    assert prices["8R-C"] == 31_000_000


def test_sale_date_filter(db):
    rows = run_screener({"date_from": "2020-01-01"}, db_path=db)
    # P2 sold 2021 (year-grain on curated), 8R-C sold 2022-08-30.
    assert {r["property_id"] for r in rows} == {"P2", "8R-C"}


def test_occupancy_filter_is_percent_in_fraction_out(db):
    rows = run_screener({"occ_min": 95}, db_path=db)
    assert {r["property_id"] for r in rows} == {"P2"}


def test_like_wildcards_in_user_text_are_literal(db):
    assert run_screener({"owner": "%"}, db_path=db) == []
    assert run_screener({"name": "_"}, db_path=db) == []


def test_apnless_county_rows_do_not_borrow_someone_elses_sale(db):
    """Review 2026-08-25 (confirmed live): sale_records keeps address-only
    sales with apn_norm='', and _norm_apn maps a NULL APN to '' — so every
    APN-less backbone row LEFT-JOINed to the county's newest address-only
    sale through ''=''. Wrong price on screen, false positives on price
    filters."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO properties_8r VALUES ('8R-NOAPN','51760',NULL,"
        "'7 NO APN ST','Richmond','VA','23220',30,1950,'Richmond',NULL,"
        "'NO APN LLC')")
    # An address-only sale for an UNRELATED property.
    conn.execute("INSERT INTO sale_records VALUES "
                 "('', '99 elsewhere ave', '2024-05-01', 99000000)")
    conn.commit()
    conn.close()

    rows = _by_id(run_screener({}, db_path=db))
    assert rows["8R-NOAPN"]["sale_price"] is None, (
        "an APN-less county row borrowed an unrelated address-only sale")
    assert "8R-NOAPN" not in _by_id(
        run_screener({"price_min": 50_000_000}, db_path=db)), (
        "a never-sold APN-less row passed a $50M price filter")


def test_us_format_dates_work_and_junk_disables_both_pools_alike(db):
    """Review 2026-08-25 (confirmed live): '6/1/2015' used to silently
    no-op the curated year filter while lexicographically zeroing the
    county pool. Now M/D/YYYY parses, and true junk deactivates the
    filter in BOTH pools instead of answering two different questions."""
    us = run_screener({"date_from": "1/1/2020"}, db_path=db)
    iso = run_screener({"date_from": "2020-01-01"}, db_path=db)
    assert {r["property_id"] for r in us} == \
           {r["property_id"] for r in iso} == {"P2", "8R-C"}

    junk = run_screener({"date_from": "abc"}, db_path=db)
    everything = run_screener({}, db_path=db)
    assert {r["property_id"] for r in junk} == \
           {r["property_id"] for r in everything}, (
        "junk date text filtered the pools inconsistently")


def test_same_day_sales_pick_deterministically(db):
    conn = sqlite3.connect(db)
    # Same parcel, same date as its existing 2022-08-30 $31M sale.
    conn.execute("INSERT INTO sale_records VALUES "
                 "('200077','9 granby st','2022-08-30', 500000)")
    conn.commit()
    conn.close()
    rows = _by_id(run_screener({}, db_path=db))
    assert rows["8R-C"]["sale_price"] == 31_000_000, (
        "same-day tie must resolve to the higher-priced (arm's-length) "
        "record, not whichever row sqlite met first")


def test_missing_backbone_tables_degrade_to_curated_only(tmp_path):
    """A dev DB with just the curated table must not crash the screen."""
    path = tmp_path / "bare.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE properties (
        property_id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT,
        state TEXT, zip TEXT, units INTEGER, year_built INTEGER,
        asset_class TEXT, market TEXT, submarket TEXT, owner TEXT,
        management_company TEXT, occupancy_pct REAL,
        last_sold_year INTEGER, last_sold_amount REAL)""")
    conn.execute("INSERT INTO properties VALUES ('P9','Solo','1 Main',"
                 "'Norfolk','VA','23510',50,1990,'B','HR',NULL,"
                 "'Owner LLC',NULL,NULL,NULL,NULL)")
    conn.commit()
    conn.close()
    rows = run_screener({}, db_path=path)
    assert [r["property_id"] for r in rows] == ["P9"]


def test_result_cap(db):
    assert len(run_screener({}, db_path=db, limit=2)) == 2


def test_the_new_module_is_wired_into_the_app():
    """The screen exists AND the app routes to it: nav lists carry the
    slug, and app.main dispatches it to render_property_screener."""
    import inspect

    import app as app_mod
    from ui import sidebar
    from ui.v2_theme_05292026 import MODULE_SLUGS

    assert "property_screener" in MODULE_SLUGS
    assert "property_screener" in inspect.getsource(
        sidebar._render_module_switcher)
    src = inspect.getsource(app_mod.main)
    assert 'active_module == "property_screener"' in src
    assert "render_property_screener" in src


def test_the_loans_button_dropped_the_granite_prefix():
    """Owner 2026-08-25: the button reads "Loans". The slug and the
    "granite" permission key are load-bearing and must survive."""
    import inspect

    import app as app_mod
    from ui import sidebar
    from ui.v2_theme_05292026 import MODULE_NAV

    labels = {label for _s, _i, label in MODULE_NAV}
    assert "Loans" in labels and "GRANITE Loans" not in labels
    side_src = inspect.getsource(sidebar._render_module_switcher)
    assert "🏦 Loans" in side_src and "GRANITE Loans" not in side_src
    app_src = inspect.getsource(app_mod.main)
    assert 'guard_module("granite", "Loans")' in app_src
