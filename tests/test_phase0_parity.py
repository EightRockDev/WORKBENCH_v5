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
        units INTEGER, year_built INTEGER, lat REAL, lng REAL)""")
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
            "INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?)",
            (f"8R-51710-{i:012x}", street, "Norfolk",
             20 + i, 1960 + i, lat + jitter, lng + jitter))
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
