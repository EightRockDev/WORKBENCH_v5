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
