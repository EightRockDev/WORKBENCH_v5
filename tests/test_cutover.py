"""P0-3 cutover foundations (spec 7.3): rent signal v1, persisted
crosswalk, and the spine read seam in data/db.py."""

from __future__ import annotations

import sqlite3

import config
from core import phase0_parity as pp
from core import rent_signal
from core.phase0 import _SPINE_SCHEMA


# ------------------------------------------------------------- fixtures

def _mk_etl_db(path, rows):
    """A hampton_roads.db lookalike with just the hud_fmr table."""
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE hud_fmr (
            fips_county_5 TEXT, year INTEGER, fmr_efficiency REAL,
            fmr_one_bedroom REAL, fmr_two_bedroom REAL,
            fmr_three_bedroom REAL, fmr_four_bedroom REAL)""")
        conn.executemany("INSERT INTO hud_fmr VALUES (?,?,?,?,?,?,?)", rows)
    return path


def _mk_spine_db(path, rows):
    """properties_8r on the real schema; rows are partial column dicts."""
    with sqlite3.connect(path) as conn:
        conn.executescript(_SPINE_SCHEMA)
        for r in rows:
            r = {"fips": "51710", "state": "VA", "built_at": "t", **r}
            cols = ", ".join(r)
            conn.execute(
                f"INSERT INTO properties_8r ({cols}) "
                f"VALUES ({', '.join('?' for _ in r)})", list(r.values()))
    return path


# ---------------------------------------------------------- rent signal

def test_blend_fmr_weights_and_renormalizes_missing_columns():
    full = rent_signal.blend_fmr({
        "fmr_efficiency": 1000, "fmr_one_bedroom": 1000,
        "fmr_two_bedroom": 1000, "fmr_three_bedroom": 1000})
    assert full == 1000  # equal inputs -> the blend is that value
    # Only 1BR/2BR published: weights renormalize over what exists.
    partial = rent_signal.blend_fmr(
        {"fmr_one_bedroom": 800, "fmr_two_bedroom": 1200})
    expected = (800 * 0.40 + 1200 * 0.45) / 0.85
    assert abs(partial - expected) < 1e-9
    assert rent_signal.blend_fmr({}) is None


def test_county_fmr_blend_uses_latest_year(tmp_path):
    etl = _mk_etl_db(tmp_path / "etl.db", [
        ("51710", 2024, 900, 1000, 1200, 1500, 1700),
        ("51710", 2026, 1000, 1100, 1300, 1600, 1800),  # newer wins
    ])
    blend = rent_signal.county_fmr_blend(etl)
    assert set(blend) == {"51710"}
    assert blend["51710"] > 1100  # built from the 2026 row


def test_apply_rent_signal_stamps_multifamily_including_code_only(tmp_path):
    """Norfolk-style rows have NO unit counts - only the use code says
    multifamily. A bare units>=10 filter would skip exactly those rows."""
    etl = _mk_etl_db(tmp_path / "etl.db",
                     [("51710", 2026, 1000, 1100, 1300, 1600, 1800)])
    spine = _mk_spine_db(tmp_path / "wb.db", [
        {"property_id": "8R-51710-aaa", "city": "Norfolk",
         "units": None, "use_code": "APARTMENT"},          # code-only MF
        {"property_id": "8R-51710-bbb", "city": "Norfolk",
         "units": 48, "use_code": None},                   # units MF
        {"property_id": "8R-51710-ccc", "city": "Norfolk",
         "units": 1, "use_code": "SINGLE FAMILY"},         # not MF
    ])
    assert rent_signal.apply_rent_signal(spine, etl) == 2
    with sqlite3.connect(spine) as conn:
        rows = dict(conn.execute(
            "SELECT property_id, est_avg_rent FROM properties_8r"))
    assert rows["8R-51710-aaa"] and rows["8R-51710-bbb"]
    assert rows["8R-51710-ccc"] is None


def test_apply_rent_signal_never_downgrades_listings(tmp_path):
    etl = _mk_etl_db(tmp_path / "etl.db",
                     [("51710", 2026, 1000, 1100, 1300, 1600, 1800)])
    spine = _mk_spine_db(tmp_path / "wb.db", [
        {"property_id": "8R-51710-aaa", "city": "Norfolk", "units": 48,
         "est_avg_rent": 1450.0, "rent_source": "listings"},
    ])
    rent_signal.apply_rent_signal(spine, etl)
    with sqlite3.connect(spine) as conn:
        rent, src = conn.execute(
            "SELECT est_avg_rent, rent_source FROM properties_8r").fetchone()
    assert (rent, src) == (1450.0, "listings")


def test_apply_rent_signal_without_etl_db_is_a_noop(tmp_path):
    spine = _mk_spine_db(tmp_path / "wb.db", [
        {"property_id": "8R-51710-aaa", "city": "Norfolk", "units": 48}])
    assert rent_signal.apply_rent_signal(
        spine, tmp_path / "missing.db") == 0


# ---------------------------------------------- crosswalk persistence

def _mk_parity_db(path, n=12, rent_factor=1.0):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE properties (
        property_id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT,
        units INTEGER, year_built INTEGER, avg_rent REAL, asset_class TEXT,
        latitude REAL, longitude REAL)""")
    conn.execute("""CREATE TABLE properties_8r (
        property_id TEXT PRIMARY KEY, address TEXT, city TEXT,
        units INTEGER, year_built INTEGER, lat REAL, lng REAL,
        use_code TEXT, est_avg_rent REAL)""")
    for i in range(n):
        lat, lng = 36.85 + (i % 6) * 0.01, -76.28 - (i // 6) * 0.01
        conn.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (f"ALN-{i}", f"Legacy {i}", f"{100 + i} Granby St",
                      "Norfolk", 20 + i, 1960 + i, 1400.0, "C", lat, lng))
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"8R-51710-{i:012x}", f"{100 + i} Granby St", "Norfolk",
                      20 + i, 1960 + i, lat, lng, "APARTMENT",
                      1400.0 * rent_factor))
    conn.commit()
    conn.close()
    return path


def test_run_parity_persists_the_crosswalk(tmp_path):
    db = _mk_parity_db(tmp_path / "wb.db")
    report = pp.run_parity(db, db)
    assert report.matched == 12
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """SELECT legacy_property_id, r8_property_id, match_method
                 FROM property_crosswalk ORDER BY legacy_property_id"""
        ).fetchall()
    assert len(rows) == 12
    assert rows[0][0].startswith("ALN-")
    assert rows[0][1].startswith("8R-51710-")
    assert all(m == "address" for _, _, m in rows)


def test_rent_gate_is_real_now(tmp_path):
    """Matching rents pass; a 2x-off estimate FAILS the gate - it can no
    longer pass vacuously just because the backbone had no rent data."""
    good = _mk_parity_db(tmp_path / "good.db", rent_factor=1.0)
    report = pp.run_parity(good, good)
    assert report.rent_pairs == 12
    assert report.avg_rent_delta == 0.0
    assert report.gate_passed

    bad = _mk_parity_db(tmp_path / "bad.db", rent_factor=2.0)
    report = pp.run_parity(bad, bad)
    assert report.avg_rent_delta and report.avg_rent_delta > 0.5
    assert not report.gate_passed


# --------------------------------------------------------- read seam

def test_read_seam_serves_the_backbone_in_legacy_shape(tmp_path):
    from data import db as dbmod
    spine = _mk_spine_db(tmp_path / "wb.db", [
        {"property_id": "8R-51710-aaa", "address": "500 Granby St",
         "city": "Norfolk", "zip": "23510", "units": 120, "year_built": 1985,
         "sqft": 108000.0, "use_code": "APARTMENT", "r8_form": "garden",
         "r8_market": "Hampton Roads", "r8_submarket": "Norfolk",
         "lat": 36.85, "lng": -76.28, "est_avg_rent": 1400.0,
         "rent_source": "hud_fmr"},
    ])
    pp.persist_crosswalk(spine, [("ALN-OLD-1", "8R-51710-aaa", "address", 1)])
    old = config.SPINE_READ_SOURCE
    config.SPINE_READ_SOURCE = "8r"
    try:
        rows = dbmod.list_properties(db_path=spine)
        assert len(rows) == 1
        row = rows[0]
        # Legacy shape: consumers read latitude/longitude/avg_rent/avg_sqft.
        assert row["latitude"] == 36.85 and row["longitude"] == -76.28
        assert row["avg_rent"] == 1400.0
        assert row["avg_sqft"] == 108000.0 / 120
        assert row["property_type"] == "garden"
        assert row["occupancy_pct"] is None    # never fabricated
        # Direct 8R id and crosswalked LEGACY id both resolve.
        assert dbmod.get_property("8R-51710-aaa", db_path=spine)
        via_legacy = dbmod.get_property("ALN-OLD-1", db_path=spine)
        assert via_legacy and via_legacy["property_id"] == "8R-51710-aaa"
        # Filters the backbone can't answer yet return empty, not wrong.
        assert dbmod.list_properties(management_company="Drucker",
                                     db_path=spine) == []
    finally:
        config.SPINE_READ_SOURCE = old


def test_read_seam_default_stays_legacy():
    assert config.SPINE_READ_SOURCE == "legacy"
