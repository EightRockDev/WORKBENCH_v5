"""Tests for data.db — SQLite query helpers.

Strategy: build a tiny SQLite fixture in tmp_path with a few hand-crafted
rows, run each query helper, verify behavior. Plus one smoke test against the
real workbench.db if it exists.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data.db import (
    DB_PATH,
    count_properties,
    get_connection,
    get_property,
    list_distinct_cities,
    list_distinct_markets,
    list_properties,
)


def _build_fixture_db(tmp_path: Path) -> Path:
    """Build a tiny test DB with 5 properties spanning markets/cities/classes."""
    db_path = tmp_path / "test.db"
    schema_sql = (
        Path(__file__).resolve().parent.parent / "data" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        rows = [
            # property_id, aln_id, name, address, city, state, zip, county, units, year_built,
            # last_remodel, occupancy_pct, avg_sqft, avg_rent, rent_per_sqft, asset_class,
            # property_type, market, submarket, latitude, longitude, owner, owner_address,
            # owner_phone, manager, management_company, pm_software, asset_or_fee, lease_terms,
            # tags, status, property_phone, website, email, aln_pull_date, raw_row
            ("p1", "100", "Pinewood Gardens", "123 Pine St", "Norfolk", "VA", "23502", "Norfolk",
             761, 1970, None, 0.987, 850, 1436, 1.69, "C", "Garden", "NOR", "NOR-CENTRAL",
             36.9152, -76.2294, "Pinewood LLC", "PO Box 1", "555-0001", "Mgr Smith",
             "ABC Mgmt", "Yardi", "Asset", "12mo", "", "92-Published", "555-0002",
             "https://pinewood.com", "info@pinewood.com", "2026-05-06", "{}"),
            ("p2", "101", "Sunset Ridge", "456 Sun Ave", "Virginia Beach", "VA", "23454", "VBeach",
             280, 1985, 2010, 0.95, 920, 1500, 1.63, "B", "Garden", "VRB", "VRB-NORTH",
             36.85, -76.10, "Sunset LLC", "PO Box 2", "555-0003", "Mgr Jones",
             "XYZ Mgmt", "Entrata", "Asset", "12mo", "", "92-Published", "555-0004",
             "", "", "2026-05-06", "{}"),
            ("p3", "102", "Norfolk Heights", "789 Hill Rd", "Norfolk", "VA", "23510", "Norfolk",
             50, 1960, None, 0.85, 700, 1100, 1.57, "D", "Garden", "NOR", "NOR-CENTRAL",
             36.9, -76.3, "Heights LLC", "PO Box 3", "555-0005", "Mgr Lee",
             "DEF Mgmt", "RealPage", "Fee", "MTM", "", "92-Published", "555-0006",
             "", "", "2026-05-06", "{}"),
            ("p4", "103", "Suffolk Pines", "111 Pine Way", "Suffolk", "VA", "23434", "Suffolk",
             120, 2005, None, 0.97, 950, 1800, 1.89, "B", "Garden", "SFK", "SFK-CENTRAL",
             36.7, -76.6, "Pines LLC", "PO Box 4", "555-0007", "Mgr Brown",
             "GHI Mgmt", "Yardi", "Asset", "12mo", "", "92-Published", "555-0008",
             "", "", "2026-05-06", "{}"),
            ("p5", "104", "Richmond Manor", "222 Manor Pl", "Richmond", "VA", "23226", "Henrico",
             200, 1990, None, 0.92, 800, 1400, 1.75, "C", "Garden", "RIC", "RIC-WEST",
             37.5, -77.5, "Manor LLC", "PO Box 5", "555-0009", "Mgr White",
             "JKL Mgmt", "Yardi", "Asset", "12mo", "", "92-Published", "555-0010",
             "", "", "2026-05-06", "{}"),
        ]
        cols = (
            "property_id, aln_id, name, address, city, state, zip, county, units, year_built, "
            "last_remodel, occupancy_pct, avg_sqft, avg_rent, rent_per_sqft, asset_class, "
            "property_type, market, submarket, latitude, longitude, owner, owner_address, "
            "owner_phone, manager, management_company, pm_software, asset_or_fee, lease_terms, "
            "tags, status, property_phone, website, email, aln_pull_date, raw_row"
        )
        placeholders = ", ".join("?" for _ in rows[0])
        conn.executemany(
            f"INSERT INTO properties ({cols}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# get_connection / get_property
# ---------------------------------------------------------------------------


def test_get_connection_uses_row_factory(tmp_path):
    db = _build_fixture_db(tmp_path)
    with get_connection(db) as conn:
        row = conn.execute("SELECT * FROM properties WHERE property_id = ?", ("p1",)).fetchone()
        # sqlite3.Row supports dict-like access by column name
        assert row["name"] == "Pinewood Gardens"
        assert row["units"] == 761


def test_get_property_returns_dict(tmp_path):
    db = _build_fixture_db(tmp_path)
    p = get_property("p1", db_path=db)
    assert p is not None
    assert p["name"] == "Pinewood Gardens"
    assert p["asset_class"] == "C"


def test_get_property_returns_none_when_missing(tmp_path):
    db = _build_fixture_db(tmp_path)
    assert get_property("does-not-exist", db_path=db) is None


# ---------------------------------------------------------------------------
# list_properties — filters
# ---------------------------------------------------------------------------


def test_list_no_filters_returns_all(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(db_path=db)
    assert len(rows) == 5


def test_list_filters_by_class(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(asset_class="C", db_path=db)
    names = {r["name"] for r in rows}
    assert names == {"Pinewood Gardens", "Richmond Manor"}


def test_list_filters_by_city(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(city="Norfolk", db_path=db)
    assert {r["name"] for r in rows} == {"Pinewood Gardens", "Norfolk Heights"}


def test_list_filters_by_market(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(market="NOR", db_path=db)
    assert {r["market"] for r in rows} == {"NOR"}
    assert len(rows) == 2


def test_list_filters_by_units_range(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(units_min=100, units_max=300, db_path=db)
    names = {r["name"] for r in rows}
    assert names == {"Suffolk Pines", "Sunset Ridge", "Richmond Manor"}


def test_list_search_matches_name_substring(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(search="pine", db_path=db)
    # "Pinewood Gardens" + "Suffolk Pines" both contain "pine"
    assert {r["name"] for r in rows} == {"Pinewood Gardens", "Suffolk Pines"}


def test_list_search_matches_city(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(search="virginia beach", db_path=db)
    assert {r["name"] for r in rows} == {"Sunset Ridge"}


def test_list_search_matches_full_address(tmp_path):
    """Locks in the behavior Brian requested 2026-05-27: searching the full
    street address should find the property, even if the user gives the
    house number + street together. Fixture has 'Pinewood Gardens' at
    '123 Pine St' — this confirms address-search works end-to-end at the
    DB layer (sidebar then bypasses class/city/units to actually show it).
    """
    db = _build_fixture_db(tmp_path)
    rows = list_properties(search="123 Pine St", db_path=db)
    assert {r["name"] for r in rows} == {"Pinewood Gardens"}


def test_list_search_matches_address_house_number_only(tmp_path):
    """Searching by just the house number ('456') should also work — useful
    when the user types fast or copies a number out of a broker email."""
    db = _build_fixture_db(tmp_path)
    rows = list_properties(search="456", db_path=db)
    assert {r["name"] for r in rows} == {"Sunset Ridge"}


def test_list_search_matches_address_street_name_only(tmp_path):
    """Searching by just the street name ('Manor Pl') should find Richmond
    Manor even though 'Manor' also appears in the property name — confirms
    the OR across name/address/city does not double-count."""
    db = _build_fixture_db(tmp_path)
    rows = list_properties(search="Manor Pl", db_path=db)
    assert {r["name"] for r in rows} == {"Richmond Manor"}


def test_list_combined_filters(tmp_path):
    """City + class together should narrow further than either alone."""
    db = _build_fixture_db(tmp_path)
    rows = list_properties(city="Norfolk", asset_class="C", db_path=db)
    assert {r["name"] for r in rows} == {"Pinewood Gardens"}


def test_list_require_latlng_excludes_null(tmp_path):
    """All fixture rows have lat/lng — confirm the flag is plumbed correctly."""
    db = _build_fixture_db(tmp_path)
    # Add a row without coords
    with get_connection(db) as conn:
        conn.execute(
            "INSERT INTO properties (property_id, name, asset_class, market, city, units, "
            "latitude, longitude, aln_pull_date) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            ("p_nogeo", "No Geo", "C", "NOR", "Norfolk", 100, "2026-05-06"),
        )
        conn.commit()
    all_rows = list_properties(db_path=db)
    geo_rows = list_properties(require_latlng=True, db_path=db)
    assert len(all_rows) == 6
    assert len(geo_rows) == 5  # the no-geo row dropped


def test_list_orders_by_name(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(db_path=db)
    names = [r["name"] for r in rows]
    assert names == sorted(names)


def test_list_respects_limit(tmp_path):
    db = _build_fixture_db(tmp_path)
    rows = list_properties(limit=2, db_path=db)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# count_properties
# ---------------------------------------------------------------------------


def test_count_no_filters(tmp_path):
    db = _build_fixture_db(tmp_path)
    assert count_properties(db_path=db) == 5


def test_count_matches_list_with_same_filters(tmp_path):
    db = _build_fixture_db(tmp_path)
    filters = dict(asset_class="C", city="Norfolk")
    assert count_properties(**filters, db_path=db) == len(list_properties(**filters, db_path=db))


# ---------------------------------------------------------------------------
# distinct cities / markets
# ---------------------------------------------------------------------------


def test_list_distinct_markets(tmp_path):
    db = _build_fixture_db(tmp_path)
    markets = list_distinct_markets(db_path=db)
    assert markets == ["NOR", "RIC", "SFK", "VRB"]


def test_list_distinct_cities(tmp_path):
    db = _build_fixture_db(tmp_path)
    cities = list_distinct_cities(db_path=db)
    assert cities == ["Norfolk", "Richmond", "Suffolk", "Virginia Beach"]


# ---------------------------------------------------------------------------
# Smoke test against the real workbench.db (if present)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DB_PATH.is_file(),
    reason="real workbench.db not present (run aln_loader.sync first)",
)
def test_smoke_real_db_returns_hampton_roads_class_c():
    """Confirm the real DB query returns the ~164 Hampton Roads Class C
    properties expected from SUMMARY-FORMAT.md calibration."""
    hr_cities = (
        "Norfolk", "Virginia Beach", "Chesapeake", "Hampton",
        "Newport News", "Portsmouth", "Suffolk",
    )
    total = 0
    for city in hr_cities:
        total += count_properties(asset_class="C", city=city, units_min=20, units_max=400)
    # SUMMARY-FORMAT said 170; current export shows ~164. Allow a buffer.
    assert 100 < total < 200
