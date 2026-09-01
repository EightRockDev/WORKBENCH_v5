"""The metrics box must show the SAME property count as the daily brief.

Two definitions of "property" in one product is how the 2026-08-11
overnight review claimed 102,232 Richmond multifamily while the brief
said ~6,500. The box therefore reuses the spine's own classifier — these
tests hold it to that.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.phase0 import MIN_MF_UNITS
from core.screener_metrics import Breakdown, definition_text, market_breakdown


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "wb.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE properties_8r (
        property_id TEXT PRIMARY KEY, city TEXT, r8_market TEXT,
        r8_submarket TEXT, use_code TEXT, units INTEGER)""")
    conn.executemany(
        "INSERT INTO properties_8r VALUES (?,?,?,?,?,?)", [
            # Norfolk: 2 real apartment buildings, one sub-10, one house.
            ("N1", "Norfolk", "Hampton Roads", "Southside", "Apartment", 48),
            ("N2", "Norfolk", "Hampton Roads", "Southside", "Apartment", 250),
            ("N3", "Norfolk", "Hampton Roads", None, "Apartment", 4),
            ("N4", "Norfolk", "Hampton Roads", None, "Single Family", None),
            # Richmond: one by units, one by code with no count yet.
            ("R1", "Richmond", "Richmond", "The Fan", "R-48 Multi Family", 30),
            ("R2", "Richmond", "Richmond", None, "R-53 Multi Family", None),
            # Atlanta: qualifies by unit count alone.
            ("A1", "Atlanta", "Atlanta", None, None, 181),
        ])
    conn.execute("CREATE TABLE properties (property_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO properties VALUES ('P1')")
    conn.commit()
    conn.close()
    return path


def test_counts_use_the_spine_classifier_not_bare_units(db):
    bd = market_breakdown(db)

    assert bd.total == 5, (
        "expected N1,N2,R1,R2(code-only),A1 - the same rows phase0 counts")
    assert bd.total_records == 7
    assert bd.curated == 1
    by = {m.market: m for m in bd.markets}
    assert by["Hampton Roads"].count == 2
    assert by["Richmond"].count == 2, (
        "a code-only Richmond parcel is a property in the brief and must "
        "be one here too")
    assert by["Atlanta"].count == 1


def test_unit_coverage_is_reported_separately_from_the_count(db):
    """The honest split the owner keeps asking about: how many COUNT as
    properties vs how many have a real unit number behind them."""
    by = {m.market: m for m in market_breakdown(db).markets}
    assert by["Richmond"].count == 2 and by["Richmond"].with_units == 1
    assert by["Richmond"].units_total == 30
    assert by["Hampton Roads"].units_total == 298


def test_submarkets_roll_up_under_their_market(db):
    by = {m.market: m for m in market_breakdown(db).markets}
    assert by["Hampton Roads"].submarkets == {"Southside": 2}
    assert by["Richmond"].submarkets == {"The Fan": 1}


def test_markets_sort_by_size(db):
    counts = [m.count for m in market_breakdown(db).markets]
    assert counts == sorted(counts, reverse=True)


def test_a_missing_database_degrades_to_a_message(tmp_path):
    bd = market_breakdown(tmp_path / "nope.db")
    assert isinstance(bd, Breakdown)
    assert bd.total == 0 and bd.error, "no database must not crash the page"


def test_a_database_without_the_backbone_degrades_too(tmp_path):
    path = tmp_path / "bare.db"
    sqlite3.connect(path).execute("CREATE TABLE x (a)")
    bd = market_breakdown(path)
    assert bd.error and "autopilot" in bd.error


def test_the_definition_states_the_actual_threshold():
    """If MIN_MF_UNITS ever changes, the page must not keep saying 10."""
    text = definition_text()
    assert f"{MIN_MF_UNITS} or more rental units" in text
    assert "20-400 unit" in text, "the WHY should name the buy box"


def test_the_screener_page_renders_the_box():
    """Wiring check: the screen calls the box and the box explains itself."""
    import inspect

    from ui import property_screener as ps

    src = inspect.getsource(ps)
    assert "_render_metrics_box(c)" in src
    assert "definition_text()" in src
    assert "cache_data" in inspect.getsource(ps._cached_breakdown), (
        "an uncached 3M-row walk on every rerun would freeze the page")


def test_a_schema_drifted_backbone_degrades_instead_of_crashing(tmp_path):
    """Found in a real browser 2026-09-01: a backbone missing use_code
    (old build) raised OperationalError through the page. The box must
    absorb it as 'not ready'."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE properties_8r (property_id TEXT, city TEXT)")
    conn.commit(); conn.close()
    bd = market_breakdown(path)
    assert bd.error and "older build" in bd.error
