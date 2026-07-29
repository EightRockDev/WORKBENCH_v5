"""Phase 0 step P0-2: shadow parity between the legacy and 8R spines."""

from __future__ import annotations

import math
import sqlite3

from core import phase0_parity as pp


def _mk_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE properties (
        property_id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT,
        units INTEGER, year_built INTEGER, avg_rent REAL, asset_class TEXT,
        latitude REAL, longitude REAL)""")
    conn.execute("""CREATE TABLE properties_8r (
        property_id TEXT PRIMARY KEY, address TEXT, city TEXT,
        units INTEGER, year_built INTEGER, lat REAL, lng REAL,
        use_code TEXT)""")
    return conn


def _seed_world(conn, n=30, jitter=0.0, addr_style="long", drop_8r: set | None = None):
    """n legacy properties in a Norfolk-ish grid, mirrored into the 8R spine.

    jitter: degrees of coordinate noise on the 8R side.
    addr_style: 'long' writes 'Street'/'Avenue' on 8R vs abbreviated legacy,
    exercising address normalization.
    """
    drop_8r = drop_8r or set()
    for i in range(n):
        lat = 36.85 + (i % 6) * 0.01
        lng = -76.28 - (i // 6) * 0.01
        conn.execute(
            "INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ALN-{i}", f"Legacy {i}", f"{100 + i} Granby St", "Norfolk",
             20 + i, 1960 + i, 1000.0 + 5 * i, "C", lat, lng))
        if i in drop_8r:
            continue
        street = f"{100 + i} Granby Street" if addr_style == "long" else f"{100 + i} Granby St"
        conn.execute(
            "INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
            (f"8R-51710-{i:012x}", street, "Norfolk",
             20 + i, 1960 + i, lat + jitter, lng + jitter, "APARTMENT"))
    conn.commit()


# ----------------------------------------------------------- normalization

def test_address_normalization_bridges_the_two_styles():
    a = pp.normalize_address("1200 Ballentine Blvd, Apt 3")
    b = pp.normalize_address("1200 BALLENTINE BOULEVARD")
    assert a == b == "1200 ballentine blvd"


# ------------------------------------------------------------ matching

def test_identical_worlds_match_fully_and_pass_the_gate(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=30)
    conn.close()
    report = pp.run_parity(db, db)
    assert report.legacy_multifamily == 30
    assert report.match_rate == 1.0
    assert report.matched_by_address == 30      # normalization did the join
    assert report.unit_disagreement == 0
    assert report.comp_subjects > 0
    # Grid geometry creates exactly-equidistant comps; the 12-comp cutoff may
    # tie-break differently per spine, so demand near-perfect, not perfect.
    assert report.avg_comp_overlap >= 0.95
    assert report.gate_passed
    assert "PASSED" in report.summary()


def test_latlng_fallback_when_addresses_differ(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=12)
    # Break every 8R address; coordinates remain -> lat/lng path must match.
    conn.execute("UPDATE properties_8r SET address = 'PO BOX 1'")
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.match_rate == 1.0
    assert report.matched_by_latlng == 12


def test_missing_8r_rows_lower_match_rate(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=20, drop_8r={0, 1, 2, 3})
    conn.close()
    report = pp.run_parity(db, db)
    assert report.matched == 16
    assert abs(report.match_rate - 0.8) < 1e-9


def test_unit_disagreement_is_flagged_with_names(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=10)
    conn.execute("UPDATE properties_8r SET units = 99 WHERE property_id LIKE '%0'")
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.unit_disagreement >= 1
    assert report.worst_unit_mismatches
    assert "legacy" in report.worst_unit_mismatches[0]


# ------------------------------------------------------------ comp replay

def test_divergent_geography_fails_the_overlap_gate(tmp_path):
    """Shift the whole 8R world ~7 miles: matching dies, comp sets can't
    overlap, the gate must fail rather than silently pass on 0 subjects."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=30, jitter=0.1)
    conn.execute("UPDATE properties_8r SET address = 'PO BOX 9'")  # kill addr join
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert not report.gate_passed


def test_empty_spine_reports_zero_not_crash(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    conn.execute("INSERT INTO properties VALUES ('A','X','1 Main St','Norfolk',"
                 "20,1970,1000,'C',36.85,-76.28)")
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.matched == 0 and not report.gate_passed
    report.summary()   # renders without error


def test_replay_caps_subjects_at_fifty(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=80)
    conn.close()
    report = pp.run_parity(db, db)
    assert report.comp_subjects <= 50


# ------------------------------------------------------- condo aggregation

def test_condo_fragmented_complex_aggregates_to_one_entity(tmp_path):
    """A 40-unit community recorded as 40 one-unit parcels at the same situs
    must compare as ONE 40-unit property (the '700 Acqua: legacy 258 vs 8R 1'
    failure from the pilot host)."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("ALN-C", "Acqua", "700 Acqua Dr", "Norfolk",
                  40, 1990, 1200.0, "B", 36.90, -76.30))
    for i in range(40):
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
                     (f"8R-51710-c{i:011x}", "700 Acqua Drive", "Norfolk",
                      1, 1990, 36.90, -76.30, "CONDO HI RISE"))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.matched == 1
    assert report.unit_agreement == 1        # 40 summed units vs legacy 40
    assert report.unit_disagreement == 0


def test_mf_pool_excludes_single_family_from_the_replay(tmp_path):
    """Single-family parcels must not crowd the 8R comp pool."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=20)
    # Flood with SFH parcels near every subject; the replay must ignore them.
    for i in range(300):
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
                     (f"8R-51710-f{i:011x}", f"{i} Elm St", "Norfolk",
                      1, 1955, 36.85 + (i % 6) * 0.01,
                      -76.28 - (i % 5) * 0.01, "SINGLE FAMILY"))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.avg_comp_overlap >= 0.90
    assert report.gate_passed


def test_subject_matched_to_non_mf_entity_is_skipped_not_keyerror(tmp_path):
    """Host crash: KeyError '8R-51550-...' - a legacy subject matched a
    Chesapeake parcel with no unit data, which the MF-only comp pool
    excluded. Must skip that subject, never crash."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=15)
    # A legacy property whose only 8R counterpart carries no units/use.
    conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("ALN-CH", "Chesapeake Mystery", "9 Battlefield Blvd",
                  "Norfolk", 60, 1980, 1100.0, "C", 36.99, -76.20))
    conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
                 ("8R-51550-deadbeef0001", "9 Battlefield Boulevard",
                  "Norfolk", None, None, 36.99, -76.20, None))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)          # must not raise
    assert report.matched == 16             # the mystery row still matches
    assert report.gate_passed               # and doesn't poison the replay


def test_multi_parcel_complex_units_recovered_by_footprint(tmp_path):
    """'700 Acqua: legacy 258 vs 8R 20' - the community spans parcels at
    700/710/720... with different street numbers. The footprint total (all
    parcels within ~200m) must recover the unit agreement."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("ALN-W", "Acqua at Windy Knolls", "700 Acqua Dr", "Norfolk",
                  258, 1988, 1300.0, "B", 36.91, -76.31))
    for i in range(6):    # six buildings, 43 units each, distinct numbers
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
                     (f"8R-51710-w{i:011x}", f"{700 + 10 * i} Acqua Drive",
                      "Norfolk", 43, 1988, 36.91 + i * 0.0002, -76.31,
                      "APARTMENT"))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.matched == 1
    assert report.unit_agreement == 1
    assert report.unit_disagreement == 0
    assert report.footprint_recovered == 1


def test_per_city_breakdown_names_cities_without_mf_data(tmp_path):
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=10)
    # A Virginia Beach legacy row with NO 8R counterpart at all.
    conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("ALN-VB", "VB Mystery", "1 Atlantic Ave", "Virginia Beach",
                  100, 1985, 1400.0, "B", 36.85, -75.98))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    text = report.summary()
    assert "Virginia Beach" in text
    assert "no usable multifamily data" in text


def test_covered_match_rate_separates_parsing_from_missing_feeds(tmp_path):
    """10 Norfolk rows (covered, all match) + 5 Virginia Beach rows (no
    spine data): blended 66%, covered-cities 100%."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    _seed_world(conn, n=10)
    for i in range(5):
        conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (f"ALN-VB{i}", f"VB {i}", f"{i} Atlantic Ave",
                      "Virginia Beach", 50, 1985, 1400.0, "B", 36.85, -75.98))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert abs(report.match_rate - 10 / 15) < 1e-9
    assert report.covered_match_rate == 1.0
    assert "covered cities only" in report.summary()


def test_aln_street_number_ranges_match_the_first_parcel():
    """Legacy '700-780 Granby St' must key like the assessor's '700 Granby'."""
    assert pp.normalize_address("700-780 Granby St") == \
           pp.normalize_address("700 Granby Street")


def test_proximity_fallback_matches_distant_geocodes(tmp_path):
    """A complex whose legacy pin sits ~0.2 mi from the parcel centroid must
    still match - to the nearest MULTIFAMILY entity only."""
    db = tmp_path / "wb.db"
    conn = _mk_db(db)
    conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("ALN-FAR", "Faraway Pines", "1 Marketing Way", "Norfolk",
                  100, 1985, 1200.0, "B", 36.9000, -76.3000))
    # A single-family house NEARER than the complex (but outside the strict
    # 120 m radius) - the proximity pass must ignore it.
    conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
                 ("8R-51710-house000001", "5 Oak St", "Norfolk",
                  1, 1950, 36.9019, -76.3000, "SINGLE FAMILY"))
    # The real complex, ~0.2 miles away with a different address.
    conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?)",
                 ("8R-51710-cplx0000001", "900 Parcel Rd", "Norfolk",
                  100, 1985, 36.9028, -76.3000, "APARTMENT"))
    conn.commit(); conn.close()
    report = pp.run_parity(db, db)
    assert report.matched == 1
    assert report.matched_by_proximity == 1
    assert report.unit_agreement == 1


# ---------------------------------------------------------------------------
# Round 7: comp-pool unit floor + parallel-feed dedupe in aggregation
# ---------------------------------------------------------------------------

def test_known_small_units_beat_mf_label_in_pool():
    """VB labels 15.7K duplexes 'Multi Family'; a KNOWN unit count under 10
    keeps them out of the comp pool no matter the label."""
    assert pp._is_mf_entity({"units": 2, "use_code": "Multi Family"}) is False
    assert pp._is_mf_entity({"units": 48, "use_code": "Multi Family"}) is True
    # No unit data at all -> the code still decides (Norfolk-style rolls).
    assert pp._is_mf_entity({"units": None,
                             "use_code": "APARTMENT 20-49 UNITS"}) is True
    assert pp._is_mf_entity({"units": None, "use_code": "OFFICE"}) is False


def test_parallel_feed_duplicates_count_once_in_aggregation():
    """Chesapeake's four overlapping layers put the SAME 280-unit building
    into the spine several times; summing made Allure at Edinburgh 1,420
    units vs legacy 280. Identical large counts at one address merge."""
    rows = [{"property_id": f"8R-x{i}", "address": "1420 Allure Way",
             "city": "Chesapeake", "units": 280, "year_built": 2015,
             "lat": 36.7, "lng": -76.25, "use_code": "Apartments"}
            for i in range(5)]
    (entity,) = pp.aggregate_8r_parcels(rows)
    assert entity["units"] == 280


def test_distinct_large_parcels_still_sum_in_aggregation():
    """A real complex spanning parcels with DIFFERENT large counts must
    keep summing (100 + 180 = 280)."""
    rows = [{"property_id": "8R-a", "address": "9 Complex Ct",
             "city": "Chesapeake", "units": 100, "year_built": 1999,
             "lat": 36.7, "lng": -76.25, "use_code": "Apartments"},
            {"property_id": "8R-b", "address": "9 Complex Ct",
             "city": "Chesapeake", "units": 180, "year_built": 1999,
             "lat": 36.7, "lng": -76.25, "use_code": "Apartments"}]
    (entity,) = pp.aggregate_8r_parcels(rows)
    assert entity["units"] == 280


def test_condo_regime_small_parcels_still_sum():
    rows = [{"property_id": f"8R-c{i}", "address": "700 Acqua Dr",
             "city": "Norfolk", "units": 1, "year_built": 1990,
             "lat": 36.9, "lng": -76.3, "use_code": "CONDO HI RISE"}
            for i in range(40)]
    (entity,) = pp.aggregate_8r_parcels(rows)
    assert entity["units"] == 40


def test_distinct_identical_count_buildings_at_one_address_still_sum():
    """Four real 24-unit phase buildings share a situs but sit on separate
    parcels with separate centroids - they must sum to 96, while true feed
    duplicates (same count, same spot) still collapse."""
    rows = [{"property_id": f"8R-p{i}", "address": "100 Complex Dr",
             "city": "Chesapeake", "units": 24, "year_built": 1985,
             "lat": 36.70 + i * 0.0005, "lng": -76.25,   # ~55m apart
             "use_code": "Apartments"} for i in range(4)]
    (entity,) = pp.aggregate_8r_parcels(rows)
    assert entity["units"] == 96
