"""The owner's MAILING address is captured, not discarded (2026-08-13).

core/skiptrace/pipeline.py anchors on prop["owner_address"] and traces on it
(S1/S4) - "the property mailing address is their address - use it, it's the
best". But every postal field sat in phase0._IGNORED_KEYS, so the backbone
never carried one and every trace fell back to the PROPERTY address. For an
LLC-owned building that is the single address the owner is not at. It is also
free: the assessor mails the tax bill there by construction.
"""

from __future__ import annotations

import sqlite3

from core import phase0

NORFOLK_ROW = {
    "OWNERNAME": "CROSSROADS APTS LLC",
    "SITUSADDRESS": "1200 Ballentine Blvd",
    "PSTLADDRESS1": "PO Box 1234",
    "PSTLCITY": "Virginia Beach",
    "PSTLSTATE": "VA",
    "PSTLZIP5": "23451",
}


def test_mailing_address_is_mapped_not_ignored():
    m = phase0.normalize_record("Norfolk", "VA", NORFOLK_ROW)
    assert m["owner_address"] == "PO Box 1234"
    assert m["owner_city"] == "Virginia Beach"
    assert m["owner_zip"] == "23451"


def test_assembled_mailing_address_is_a_usable_trace_input():
    m = phase0.normalize_record("Norfolk", "VA", NORFOLK_ROW)
    assert phase0._owner_mailing(m) == "PO Box 1234, Virginia Beach VA 23451"


def test_mailing_address_never_overwrites_the_property_address():
    """The whole point: these are two DIFFERENT places."""
    m = phase0.normalize_record("Norfolk", "VA", NORFOLK_ROW)
    assert m["address"] == "1200 Ballentine Blvd"
    assert phase0._owner_mailing(m) != m["address"]


def test_missing_postal_fields_stay_none():
    m = phase0.normalize_record("Norfolk", "VA", {"OWNERNAME": "X LLC"})
    assert phase0._owner_mailing(m) is None


def test_street_only_still_yields_an_address():
    m = phase0.normalize_record("Norfolk", "VA", {"MAILINGADDRESS": "5 Main St"})
    assert phase0._owner_mailing(m) == "5 Main St"


def test_alternate_spellings_map(monkeypatch):
    for key in ("MAILINGADDRESS", "OWNERADDRESS", "TAXBILLADDRESS",
                "BILLINGADDRESS"):
        m = phase0.normalize_record("Norfolk", "VA", {key: "9 Elm Ave"})
        assert m.get("owner_address") == "9 Elm Ave", key


def test_existing_backbone_migrates_in_place(tmp_path):
    """A db built before this change has no owner_address column; the
    builder must ALTER it rather than fail every write."""
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        phase0._SPINE_SCHEMA.replace("    owner_address  TEXT,\n", ""))
    conn.execute("CREATE TABLE IF NOT EXISTS muni_records ("
                 "id INTEGER PRIMARY KEY, market TEXT, state TEXT, "
                 "county TEXT, kind TEXT, source_url TEXT, pulled_at TEXT, "
                 "record TEXT)")
    conn.commit()
    conn.close()
    cols_before = _cols(db)
    assert "owner_address" not in cols_before
    phase0.build_spine(db)          # the real builder, on the old db
    assert "owner_address" in _cols(db)


def _cols(db) -> set:
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(properties_8r)")}
    finally:
        conn.close()


def test_mailing_address_reaches_the_skiptrace_read_shape(tmp_path):
    """End-to-end: backbone column -> data/db read shape -> the key the
    skip-trace pipeline actually reads (prop["owner_address"], its S1 anchor
    and S4 trace address). A column nobody surfaces is a column nobody uses."""
    from data import db as dbmod
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.executescript(phase0._SPINE_SCHEMA)
    conn.execute(
        "INSERT INTO properties_8r (property_id, fips, apn, address, city, "
        "state, zip, units, owner_name, owner_address, provenance, built_at) "
        "VALUES ('8R-T-1','51710','A1','1200 Ballentine Blvd','Norfolk','VA',"
        "'23504',26,'CROSSROADS APTS LLC',"
        "'PO Box 1234, Virginia Beach VA 23451','8r','x')")
    conn.commit()
    conn.row_factory = sqlite3.Row
    raw = dict(conn.execute("SELECT * FROM properties_8r").fetchone())
    conn.close()

    shaped = dbmod._r8_to_legacy_shape(raw)
    assert shaped["owner_address"] == "PO Box 1234, Virginia Beach VA 23451"
    # ...and it is NOT the building, which is the whole point.
    assert shaped["owner_address"] != shaped["address"]
