"""Phase 0 step P0-1: muni_records -> properties_8r (spec 7.3)."""

from __future__ import annotations

import json
import sqlite3

from core import phase0, spine


def _seed_muni(db, rows):
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        conn.executemany(
            "INSERT INTO muni_records (market,state,county,kind,record) "
            "VALUES (?,?,?,?,?)",
            [(m, s, m, k, json.dumps(r)) for m, s, k, r in rows])
        conn.commit()


def _norfolk(parcel, units=24, use="APARTMENT 20-49 UNITS", value=2_400_000):
    """Socrata shape: flat lowercase keys (Norfolk reference model)."""
    return ("Norfolk", "VA", "assessor+sales", {
        "gpin": parcel, "propertystreet": f"{parcel} Granby St",
        "yearbuilt": "1968", "usedescription": use,
        "totalvalue": str(value), "owner": "GRANBY HOLDINGS LLC",
        "livingunits": units,
    })


def _newport_news(parcel, units=16):
    """ArcGIS shape: attributes/geometry nesting, UPPERCASE keys, LIVUNIT."""
    return ("Newport News", "VA", "assessor+sales", {
        "attributes": {"PARCELID": parcel, "SITUSADDRESS": "12 Warwick Blvd",
                       "YRBLT": 1975, "LIVUNIT": units,
                       "USECODE": "405 APARTMENT", "TOTALVALUE": 1_900_000,
                       "OWNERNAME": "WARWICK APTS LLC"},
        "geometry": {"x": -76.47, "y": 37.05},
    })


def test_norfolk_socrata_shape_normalizes(tmp_path):
    db = tmp_path / "workbench.db"
    _seed_muni(db, [_norfolk("1234567890")])
    report = phase0.build_spine(db)
    assert report.written == 1 and report.multifamily == 1
    with sqlite3.connect(db) as conn:
        row = dict(zip([c[0] for c in conn.execute(
            "SELECT * FROM properties_8r").description],
            conn.execute("SELECT * FROM properties_8r").fetchone()))
    assert row["property_id"] == spine.property_id("51710", "1234567890")
    assert row["units"] == 24 and row["year_built"] == 1968
    assert row["city"] == "Norfolk" and row["r8_submarket"] == "Norfolk"
    assert row["provenance"] == "8r"


def test_arcgis_nested_shape_normalizes(tmp_path):
    db = tmp_path / "workbench.db"
    _seed_muni(db, [_newport_news("NN-4455")])
    report = phase0.build_spine(db)
    assert report.written == 1
    with sqlite3.connect(db) as conn:
        pid, units, lat, lng = conn.execute(
            "SELECT property_id, units, lat, lng FROM properties_8r").fetchone()
    assert pid == spine.property_id("51700", "NN-4455")
    assert units == 16
    assert (lat, lng) == (37.05, -76.47)


def test_ids_are_deterministic_across_rebuilds(tmp_path):
    db = tmp_path / "workbench.db"
    _seed_muni(db, [_norfolk("777-A-12")])
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        first = conn.execute("SELECT property_id FROM properties_8r").fetchone()[0]
    phase0.build_spine(db)     # rebuild - same muni data, same id
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT property_id FROM properties_8r").fetchall()
    assert rows == [(first,)]


def test_no_apn_falls_back_to_provisional_geohash_id(tmp_path):
    db = tmp_path / "workbench.db"
    row = _newport_news("ignored")
    del row[3]["attributes"]["PARCELID"]
    _seed_muni(db, [row])
    report = phase0.build_spine(db)
    assert report.written == 1 and report.provisional_ids == 1
    with sqlite3.connect(db) as conn:
        pid = conn.execute("SELECT property_id FROM properties_8r").fetchone()[0]
    assert spine.is_provisional(pid)


def test_gate_math_counts_unusable_multifamily(tmp_path):
    """A multifamily record with neither parcel nor lat/lng hurts coverage."""
    db = tmp_path / "workbench.db"
    broken = ("Norfolk", "VA", "assessor", {"usedescription": "APARTMENT",
                                            "livingunits": 30})
    _seed_muni(db, [_norfolk("1"), _norfolk("2"), broken])
    report = phase0.build_spine(db)
    assert report.multifamily == 2
    assert report.skipped_no_parcel_or_latlng == 1
    assert abs(report.coverage - 2 / 3) < 1e-9
    assert not report.gate_passed


def test_gate_passes_at_full_coverage(tmp_path):
    db = tmp_path / "workbench.db"
    _seed_muni(db, [_norfolk(str(i)) for i in range(40)])
    report = phase0.build_spine(db)
    assert report.gate_passed


def test_single_family_records_do_not_count_toward_the_gate(tmp_path):
    db = tmp_path / "workbench.db"
    sfh = ("Norfolk", "VA", "assessor", {"gpin": "SF-1",
                                         "usedescription": "SINGLE FAMILY",
                                         "livingunits": 1})
    _seed_muni(db, [sfh, _norfolk("MF-1")])
    report = phase0.build_spine(db)
    assert report.written == 2          # SFH is kept in the spine
    assert report.multifamily == 1      # but only MF counts for the gate


def test_unmapped_keys_are_reported_for_tuning(tmp_path):
    db = tmp_path / "workbench.db"
    weird = ("Norfolk", "VA", "assessor", {"gpin": "W-1",
                                           "ZQX_UNITS_TOTAL": 12,
                                           "usedescription": "APARTMENT"})
    _seed_muni(db, [weird])
    report = phase0.build_spine(db)
    assert "ZQX_UNITS_TOTAL" in report.unmapped_keys["Norfolk"]
    assert "ZQX_UNITS_TOTAL" in report.summary()


def test_permits_and_other_kinds_are_ignored(tmp_path):
    db = tmp_path / "workbench.db"
    permit = ("Norfolk", "VA", "permits", {"gpin": "P-1", "livingunits": 50})
    _seed_muni(db, [permit, _norfolk("A-1")])
    report = phase0.build_spine(db)
    assert report.scanned == 1 and report.written == 1


def test_spine_rows_pass_the_cleanliness_check(tmp_path):
    """Every written row must satisfy AC-P0-1/2 (record_is_clean)."""
    db = tmp_path / "workbench.db"
    _seed_muni(db, [_norfolk("C-1"), _newport_news("C-2")])
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM properties_8r"):
            ok, problems = spine.record_is_clean(dict(row))
            assert ok, problems


def test_missing_muni_table_reports_zero_not_crash(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    assert phase0.has_muni_records(db) == 0


# --------------------------------------------- host-run tuning (2026-07-28)

def test_norfolk_split_address_gets_its_house_number(tmp_path):
    """Norfolk's feed splits number and street; without joining them no
    address ever matched the legacy spine (31% match on the pilot host)."""
    db = tmp_path / "workbench.db"
    row = ("Norfolk", "VA", "assessor+sales", {
        "gpin": "N-77", "property_street_number": "700",
        "propertystreet": "Acqua Drive", "livingunits": 40,
        "usedescription": "APARTMENT"})
    _seed_muni(db, [row])
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        addr = conn.execute("SELECT address FROM properties_8r").fetchone()[0]
    assert addr == "700 Acqua Drive"


def test_yearbuilt_beats_effectiveyear(tmp_path):
    """effective_year is a reassessment concept, not construction vintage -
    it must lose to a real yearbuilt when both are present."""
    db = tmp_path / "workbench.db"
    row = ("Norfolk", "VA", "assessor", {
        "gpin": "Y-1", "yearbuilt": 1965, "effective_year": 1999,
        "livingunits": 12, "usedescription": "APARTMENT"})
    _seed_muni(db, [row])
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        year = conn.execute("SELECT year_built FROM properties_8r").fetchone()[0]
    assert year == 1965


def test_chesapeake_and_nn_aliases_map(tmp_path):
    db = tmp_path / "workbench.db"
    rows = [
        ("Chesapeake", "VA", "assessor", {
            "MAP_PARCEL": "CH-9", "PROPCLASS": "APARTMENT", "livingunits": 18}),
        ("Newport News", "VA", "assessor", {
            "attributes": {"PARCELID": "NN-9", "CLASSCD": "405 APARTMENT",
                           "OWNERNME1": "NN HOLDINGS", "LIVUNIT": 22},
            "geometry": {"x": -76.4, "y": 37.0}}),
    ]
    _seed_muni(db, rows)
    report = phase0.build_spine(db)
    assert report.multifamily == 2
    assert not report.unmapped_keys.get("Chesapeake")
    with sqlite3.connect(db) as conn:
        owners = {r[0] for r in conn.execute(
            "SELECT owner_name FROM properties_8r").fetchall()}
    assert "NN HOLDINGS" in owners


def test_bookkeeping_keys_stay_out_of_the_tuning_report(tmp_path):
    db = tmp_path / "workbench.db"
    row = ("Norfolk", "VA", "assessor", {
        "gpin": "B-1", "livingunits": 15, "usedescription": "APARTMENT",
        "OBJECTID": 9, "SHAPE.STArea()": 120.5, "legal_description": "LOT 4",
        "PublicLink": "http://x", "Sale_Price": 100})
    _seed_muni(db, [row])
    report = phase0.build_spine(db)
    assert not report.unmapped_keys.get("Norfolk")


def test_norfolk_three_part_address_assembles(tmp_path):
    """Round 2: Norfolk splits number + name + TYPE across three fields."""
    db = tmp_path / "workbench.db"
    row = ("Norfolk", "VA", "assessor", {
        "gpin": "N3-1", "property_street_number": "700",
        "property_street_name": "Acqua", "property_street_type": "DR",
        "livingunits": 40, "property_class_description": "APARTMENT"})
    _seed_muni(db, [row])
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        addr = conn.execute("SELECT address FROM properties_8r").fetchone()[0]
    assert addr == "700 Acqua DR"


def test_round2_aliases_and_ignores(tmp_path):
    db = tmp_path / "workbench.db"
    rows = [
        ("Newport News", "VA", "assessor", {
            "attributes": {"PARCELID": "NN-R2", "USECD": "405",
                           "CLASSDSCRP": "APARTMENT", "RESFLRAREA": 18_000,
                           "LIVUNIT": 20, "HUBZONE": "Y", "CENSUSTRACT": "1"},
            "geometry": {"x": -76.4, "y": 37.0}}),
        ("Chesapeake", "VA", "assessor", {
            "MAP_PARCEL": "CH-R2", "PROPCLASS": "APARTMENT",
            "ADDRESSZIP": "23320", "PARNO": "ignored-lower-priority",
            "DEEDBK": "111", "DEEDPG": "22", "CALCACREAGE": 2.5}),
    ]
    _seed_muni(db, rows)
    report = phase0.build_spine(db)
    assert not report.unmapped_keys.get("Newport News")
    assert not report.unmapped_keys.get("Chesapeake")
    with sqlite3.connect(db) as conn:
        sqfts = {r[0] for r in conn.execute(
            "SELECT sqft FROM properties_8r").fetchall()}
        zips = {r[0] for r in conn.execute(
            "SELECT zip FROM properties_8r").fetchall()}
    assert 18_000.0 in sqfts
    assert "23320" in zips


def test_norfolk_five_part_address_assembles(tmp_path):
    """Round 3: number + number-suffix + direction + name + type."""
    db = tmp_path / "workbench.db"
    row = ("Norfolk", "VA", "assessor", {
        "gpin": "N5-1", "property_street_number": "921",
        "property_street_number_suffix": "A",
        "property_street_direction": "W",
        "property_street_name": "21st", "property_street_type": "ST",
        "livingunits": 30, "property_class_description": "APARTMENT",
        "residential_finished_living": 24_000, "grantor": "OLD OWNER LLC"})
    _seed_muni(db, [row])
    report = phase0.build_spine(db)
    assert not report.unmapped_keys.get("Norfolk")
    with sqlite3.connect(db) as conn:
        addr, sqft = conn.execute(
            "SELECT address, sqft FROM properties_8r").fetchone()
    assert addr == "921A W 21st ST"
    assert sqft == 24_000.0
