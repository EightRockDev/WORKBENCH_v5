"""The legacy read path has no parcel id; sale history must bridge to one.

Root cause found 2026-08-15 from an owner screenshot reading "No sale history
available". The app serves the licensed vendor table until the Phase 0 gates
hold, and `data/schema.sql` gives that table no `apn` column - only a provider
UUID and a provider integer. So `prop["apn"]` was always None, the apn branch
of the lookup was structurally dead, and every property in every market fell
back to matching a marketing address against an assessor situs address.
"""

from __future__ import annotations

import sqlite3

import pytest

from core import phase0, sale_history


@pytest.fixture()
def spine(tmp_path):
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.executescript(phase0._SPINE_SCHEMA)
    conn.execute(
        "INSERT INTO properties_8r (property_id, fips, apn, address, city, "
        "state, zip, units, provenance, built_at) VALUES "
        "('8R-1','51710','GPIN-99887','1200 Ballentine Blvd','Norfolk','VA',"
        "'23504',26,'8r','x')")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS property_crosswalk ("
        " legacy_property_id TEXT PRIMARY KEY, r8_property_id TEXT NOT NULL,"
        " via TEXT, n INT, at TEXT)")
    conn.execute("INSERT INTO property_crosswalk VALUES "
                 "('VENDOR-UUID-1','8R-1','address',1,'now')")
    conn.commit()
    conn.close()
    return db


def test_legacy_row_has_no_parcel_id_of_its_own():
    """Guards the premise: if the vendor table ever gains an apn column this
    bridge becomes unnecessary, and this test should be the thing that says so."""
    import pathlib
    schema = (pathlib.Path(__file__).resolve().parent.parent
              / "data" / "schema.sql").read_text()
    create = schema.split("CREATE TABLE IF NOT EXISTS properties (")[1]
    create = create.split(");")[0]
    assert "apn" not in create, (
        "the legacy properties table now HAS a parcel column - re-evaluate "
        "the crosswalk bridge in core/sale_history")


def test_crosswalk_resolves_a_parcel_id_for_a_legacy_property(spine):
    legacy = {"property_id": "VENDOR-UUID-1", "address": "3000 S Cape Henry",
              "city": "Norfolk"}
    assert sale_history._norm_apn(legacy.get("apn")) == ""
    assert sale_history._apn_via_crosswalk(legacy, spine) == "gpin99887"


def test_unmatched_property_degrades_to_address_matching(spine):
    """An unmatched property must return "" - not raise, and not invent a key."""
    legacy = {"property_id": "NOT-IN-CROSSWALK", "address": "1 Nowhere St"}
    assert sale_history._apn_via_crosswalk(legacy, spine) == ""


def test_missing_crosswalk_table_is_survivable(tmp_path):
    """A box that has never run parity has no crosswalk; that must not raise."""
    db = tmp_path / "bare.db"
    conn = sqlite3.connect(db)
    conn.executescript(phase0._SPINE_SCHEMA)
    conn.commit()
    conn.close()
    assert sale_history._apn_via_crosswalk({"property_id": "X"}, db) == ""


def test_address_bridge_resolves_a_parcel_the_crosswalk_never_matched(spine):
    """The crosswalk holds only what parity matched - a few hundred rows - so
    it answers for almost nothing. The backbone knows every parcel in the
    county by address and carries the id sales are keyed on. Owner screenshot
    2026-08-15: "186,843 recorded sales loaded for Norfolk, but this property
    is not yet tied to a county parcel"."""
    import sqlite3 as _s
    from core import sale_history as sh
    conn = _s.connect(spine)
    conn.execute(
        "INSERT INTO properties_8r (property_id, fips, apn, address, city, "
        "state, zip, provenance, built_at) VALUES "
        "('8R-N1','51710','14712345','3000 CAPE HENRY AVE','Norfolk','VA',"
        "'23503','8r','x')")
    conn.commit()
    conn.close()
    vendor_row = {"property_id": "NOT-IN-CROSSWALK",
                  "address": "3000 S. Cape Henry", "city": "Norfolk"}
    assert sh._apn_via_crosswalk(vendor_row, spine) == ""
    assert sh._apn_via_address(vendor_row, spine) == "14712345"


def test_address_bridge_refuses_an_ambiguous_match(spine):
    """Two parcels answering the same address must yield nothing - attaching
    a neighbour's sale history is worse than showing none."""
    import sqlite3 as _s
    from core import sale_history as sh
    conn = _s.connect(spine)
    for pid, apn, addr in (("8R-A", "111", "500 MAIN ST"),
                           ("8R-B", "222", "500 MAIN AVE")):
        conn.execute(
            "INSERT INTO properties_8r (property_id, fips, apn, address, city,"
            " state, zip, provenance, built_at) VALUES (?,'51710',?,?, "
            "'Norfolk','VA','23503','8r','x')", (pid, apn, addr))
    conn.commit()
    conn.close()
    assert sh._apn_via_address(
        {"address": "500 Main", "city": "Norfolk"}, spine) == ""
