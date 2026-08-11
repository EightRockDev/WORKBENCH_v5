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


def test_units_derived_from_address_point_multiplicity(tmp_path):
    """Chesapeake/Norfolk address-point feeds: one row PER APARTMENT sharing
    the parcel id - the row count IS the unit count."""
    db = tmp_path / "workbench.db"
    rows = []
    for i in range(24):
        rows.append(("Chesapeake", "VA", "assessor", {
            "MAP_PARCEL": "CH-PTS-1", "address": "100 Battlefield Blvd",
            "UNIT": str(i + 1), "PROPCLASS": "APARTMENT"}))
    _seed_muni(db, rows)
    report = phase0.build_spine(db)
    assert report.units_from_points == 1
    with sqlite3.connect(db) as conn:
        units = conn.execute("SELECT units FROM properties_8r").fetchone()[0]
    assert units == 24
    assert report.multifamily == 1        # derived units count toward the gate


def test_overlapping_feeds_do_not_double_count_points(tmp_path):
    """The same parcel appearing in TWO feeds must take the max per-feed
    count, not the sum."""
    db = tmp_path / "workbench.db"
    rows = []
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        for src, n in (("https://a.test/0", 12), ("https://b.test/0", 1)):
            for i in range(n):
                conn.execute(
                    "INSERT INTO muni_records (market,state,county,kind,"
                    "source_url,record) VALUES (?,?,?,?,?,?)",
                    ("Chesapeake", "VA", "Chesapeake", "assessor", src,
                     json.dumps({"MAP_PARCEL": "CH-DUP-1",
                                 "PROPCLASS": "APARTMENT", "UNIT": str(i)})))
        conn.commit()
    report = phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units = conn.execute("SELECT units FROM properties_8r").fetchone()[0]
    assert units == 12


def test_explicit_units_beat_point_counting(tmp_path):
    """A feed with a real unit field is never overridden by row counting."""
    db = tmp_path / "workbench.db"
    _seed_muni(db, [_norfolk("EXPL-1", units=48), _norfolk("EXPL-1", units=48)])
    report = phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units = conn.execute("SELECT units FROM properties_8r").fetchone()[0]
    assert units == 48


# ---------------------------------------------------------------------------
# Round 6: token-aware multifamily matching + coordinate hygiene
# ---------------------------------------------------------------------------

def test_short_mf_codes_match_whole_tokens_only():
    """VB zoning 'R-40' (single-family) substring-contains 'r-4' - that bug
    classified ~116K SFH parcels as multifamily. Short codes now require an
    exact token - and 'r-4' itself is GONE from the global set: Richmond's
    roll says 'R-4 Single Family' (x9,001), Durham and Atlanta agree. A city
    where R-4 truly means apartments re-adds it via use_code_learn."""
    assert phase0.is_multifamily("R-40", None) is False
    assert phase0.is_multifamily("R-4", None) is False
    assert phase0.is_multifamily("MF", None) is True
    assert phase0.is_multifamily("MFG WAREHOUSE", None) is False
    assert phase0.is_multifamily("405", None) is True
    assert phase0.is_multifamily("405 APARTMENT", None) is True
    assert phase0.is_multifamily("1405", None) is False
    assert phase0.is_multifamily("APT", None) is True


def test_single_family_text_vetoes_any_code_match():
    """The roll's own words beat a code lookup: 'R-4 Single Family' must
    never classify as multifamily, whatever any token list says - but an
    explicit unit count >= the bar still wins over the label."""
    assert phase0.is_multifamily("R-4 Single Family", None) is False
    assert phase0.is_multifamily("MF SINGLE FAMILY CONVERSION", None) is False
    assert phase0.is_multifamily("APT 1 FAM", None) is False
    assert phase0.is_multifamily("R-4 Single Family", 24) is True
    assert phase0.is_multifamily("R-48 Multi Family", None) is True
    assert phase0.is_multifamily("R-63 Multi Family Urban Res.", None) is True


def test_small_plex_forms_are_not_ten_plus_multifamily():
    """The product bar is >= 10 units (spec 7.3); duplex/triplex are 2-3."""
    assert phase0.is_multifamily("DUPLEX", None) is False
    assert phase0.is_multifamily("TRIPLEX", None) is False
    assert phase0.is_multifamily(None, 12) is True


def test_chesapeake_split_address_assembles():
    """Chesapeake's layer splits the situs into ST_NUM/ST_NAME/ST_TYPE."""
    m = phase0.normalize_record("Chesapeake", "VA", {
        "ST_NUM": "701", "ST_NAME": "RIVER WALK", "ST_TYPE": "DR",
        "GPIN": "123"})
    assert m["address"] == "701 RIVER WALK DR"


def test_round6_aliases_map():
    m = phase0.normalize_record("Newport News", "VA", {
        "RESYRBLT": 1987, "TOT_SQ_FT": 9000, "BLDG_USE": "APT",
        "MASTER_GPIN": "55"})
    assert m["year_built"] == 1987
    assert m["sqft"] == 9000
    assert m["use_code"] == "APT"
    assert m["apn"] == "55"


def test_sanitize_latlng_guards():
    import math
    # Web Mercator meters convert to degrees...
    x = math.radians(-76.28) * 6378137
    y = 6378137 * math.log(math.tan(math.pi / 4 + math.radians(36.85) / 2))
    lat, lng = phase0.sanitize_latlng(y, x)
    assert abs(lat - 36.85) < 1e-6 and abs(lng + 76.28) < 1e-6
    # ...state-plane feet and null-island junk are dropped, real degrees pass.
    assert phase0.sanitize_latlng(3_400_000, 12_100_000) == (None, None)
    assert phase0.sanitize_latlng(0.0, 0.0) == (None, None)
    assert phase0.sanitize_latlng(36.9, -76.2) == (36.9, -76.2)
    assert phase0.sanitize_latlng(None, -76.2) == (None, None)


# ---------------------------------------------------------------------------
# Round 7: address-point derivation guard + non-clobbering feed merge
# ---------------------------------------------------------------------------

def _seed_pointed(db, city, parcel, use, n_points, feed="https://pts.test/0"):
    """One parcel appearing as N address-point rows in a single feed."""
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        for i in range(n_points):
            conn.execute(
                "INSERT INTO muni_records (market,state,county,kind,"
                "source_url,record) VALUES (?,?,?,?,?,?)",
                (city, "VA", city, "assessor", feed,
                 json.dumps({"MAP_PARCEL": parcel, "PROPCLASS": use,
                             "UNITNUMBER": str(i)})))
        conn.commit()


def test_marina_boat_slips_never_become_units(tmp_path):
    """Chesapeake classified 'BOAT SLIP x92' as multifamily: one address
    point per slip read as one unit each. Non-residential parcels are now
    excluded from point-derived units."""
    db = tmp_path / "workbench.db"
    _seed_pointed(db, "Chesapeake", "CH-MARINA-1", "BOAT SLIP", 92)
    report = phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units = conn.execute("SELECT units FROM properties_8r").fetchone()[0]
    assert units is None
    assert report.units_from_points_skipped == 1
    assert report.multifamily == 0


def test_shopping_center_suites_never_become_units(tmp_path):
    db = tmp_path / "workbench.db"
    _seed_pointed(db, "Chesapeake", "CH-MALL-1", "Shopping Centers", 41)
    report = phase0.build_spine(db)
    assert report.multifamily == 0
    assert report.units_from_points_skipped == 1


def test_single_family_plat_points_never_become_units(tmp_path):
    """A subdivision plat puts many points on one still-single-family
    parcel; the assessor's own label must win over point counting."""
    db = tmp_path / "workbench.db"
    _seed_pointed(db, "Virginia Beach", "VB-PLAT-1",
                  "Single Family or Duplex", 15)
    report = phase0.build_spine(db)
    assert report.multifamily == 0


def test_apartment_labeled_points_still_derive_units(tmp_path):
    """The guard must not break the legit case - apartment-coded parcels
    keep deriving units from point multiplicity."""
    db = tmp_path / "workbench.db"
    _seed_pointed(db, "Chesapeake", "CH-APTS-1", "Apartments", 24)
    report = phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units = conn.execute("SELECT units FROM properties_8r").fetchone()[0]
    assert units == 24
    assert report.units_from_points == 1
    assert report.multifamily == 1


def test_later_feed_nulls_do_not_clobber_explicit_values(tmp_path):
    """Chesapeake has 4 overlapping feeds. A bare address-point row landing
    AFTER the parcel row must not wipe its units/use code (then re-derive
    units=3 from multiplicity)."""
    db = tmp_path / "workbench.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        conn.execute(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "record) VALUES (?,?,?,?,?,?)",
            ("Chesapeake", "VA", "Chesapeake", "assessor",
             "https://parcels.test/0",
             json.dumps({"MAP_PARCEL": "CH-KEEP-1", "LIVUNIT": 48,
                         "PROPCLASS": "APARTMENTS"})))
        for i in range(3):
            conn.execute(
                "INSERT INTO muni_records (market,state,county,kind,"
                "source_url,record) VALUES (?,?,?,?,?,?)",
                ("Chesapeake", "VA", "Chesapeake", "assessor",
                 "https://points.test/0",
                 json.dumps({"MAP_PARCEL": "CH-KEEP-1",
                             "UNITNUMBER": str(i)})))
        conn.commit()
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units, use = conn.execute(
            "SELECT units, use_code FROM properties_8r").fetchone()
    assert units == 48
    assert use == "APARTMENTS"


# ---------------------------------------------------------------------------
# Round 7b: adversarial-review regressions (allowlist, r8_form, gate rule)
# ---------------------------------------------------------------------------

def test_unenumerable_single_family_spellings_never_derive_units(tmp_path):
    """'1 FAM RES', 'R-1', numeric class '101' - no blocklist can enumerate
    these. The allowlist (derive only for MF codes or NO code) shuts the
    whole class down."""
    for i, use in enumerate(("1 FAM RES", "SINGLE FAM", "R-1", "101")):
        db = tmp_path / f"wb{i}.db"
        _seed_pointed(db, "Chesapeake", f"CH-SF-{i}", use, 15)
        report = phase0.build_spine(db)
        assert report.multifamily == 0, use
        assert report.units_from_points_skipped == 1, use


def test_subsidized_housing_still_derives_units(tmp_path):
    db = tmp_path / "wb.db"
    _seed_pointed(db, "Norfolk", "NF-PH-1", "GOVERNMENT SUBSIDIZED HOUSING", 36)
    report = phase0.build_spine(db)
    assert report.units_from_points == 1
    assert report.multifamily == 1


def test_building_card_units_of_one_are_overridden_by_point_count(tmp_path):
    """CAMA building-card feeds write units=1 per row; COALESCE freezes the
    first card's 1. Twelve cards on one apartment parcel mean 12 units."""
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        for i in range(12):
            conn.execute(
                "INSERT INTO muni_records (market,state,county,kind,"
                "source_url,record) VALUES (?,?,?,?,?,?)",
                ("Chesapeake", "VA", "Chesapeake", "assessor",
                 "https://cards.test/0",
                 json.dumps({"MAP_PARCEL": "CH-CARDS-9", "LIVUNIT": 1,
                             "PROPCLASS": "APARTMENTS", "UNITNUMBER": str(i)})))
        conn.commit()
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units = conn.execute("SELECT units FROM properties_8r").fetchone()[0]
    assert units == 12


def test_r8_form_is_computed_from_the_merged_row(tmp_path):
    """Two review-confirmed bugs: build_row passed year_built as the STORIES
    parameter (everything became 'high-rise'), and a later bare row could
    clobber r8_form via the upsert CASE. r8_form now recomputes after the
    merge settles."""
    db = tmp_path / "wb.db"
    _seed_muni(db, [_norfolk("FORM-1", units=24)])       # yearbuilt present
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        form = conn.execute("SELECT r8_form FROM properties_8r").fetchone()[0]
    assert form == "garden"        # NOT 'high-rise' (year is not stories)


def test_bare_point_rows_do_not_clobber_r8_form(tmp_path):
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        conn.execute(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "record) VALUES (?,?,?,?,?,?)",
            ("Chesapeake", "VA", "Chesapeake", "assessor",
             "https://a.test/0",
             json.dumps({"MAP_PARCEL": "FORM-2", "TOTALUNITS": 200})))
        conn.execute(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "record) VALUES (?,?,?,?,?,?)",
            ("Chesapeake", "VA", "Chesapeake", "assessor",
             "https://b.test/0",
             json.dumps({"MAP_PARCEL": "FORM-2", "UNITNUMBER": "0"})))
        conn.commit()
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        units, form = conn.execute(
            "SELECT units, r8_form FROM properties_8r").fetchone()
    assert units == 200
    assert form == "mid-rise"      # derive(None, 200) - not 'garden'


def test_gate_counts_by_units_when_units_are_known():
    """The gate and the comp pool share one rule: a known count decides."""
    assert phase0.is_mf_ten_plus("Multi Family", 2) is False
    assert phase0.is_mf_ten_plus("Multi Family", 48) is True
    assert phase0.is_mf_ten_plus("APARTMENT 20-49 UNITS", None) is True
    assert phase0.is_mf_ten_plus("OFFICE", None) is False


def test_scan_order_is_deterministic_regardless_of_pull_history(tmp_path):
    """COALESCE keeps the first non-NULL; re-pulls move a feed's rows to
    the end of rowid order. ORDER BY source_url makes the winner stable."""
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
            kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
        # Feed z inserted FIRST (older pull), feed a inserted second - the
        # a-feed must still win because it sorts first.
        conn.execute(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "record) VALUES (?,?,?,?,?,?)",
            ("Chesapeake", "VA", "Chesapeake", "assessor",
             "https://z.test/0",
             json.dumps({"MAP_PARCEL": "DET-1", "PROPCLASS": "Z CODE"})))
        conn.execute(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "record) VALUES (?,?,?,?,?,?)",
            ("Chesapeake", "VA", "Chesapeake", "assessor",
             "https://a.test/0",
             json.dumps({"MAP_PARCEL": "DET-1", "PROPCLASS": "A CODE"})))
        conn.commit()
    phase0.build_spine(db)
    with sqlite3.connect(db) as conn:
        use = conn.execute("SELECT use_code FROM properties_8r").fetchone()[0]
    assert use == "A CODE"


def test_socrata_location_dicts_become_coords_never_addresses():
    """Socrata serves coordinates as dicts; they must reach lat/lng and
    never be str()'d into a text field."""
    m = phase0.normalize_record("Norfolk", "VA", {
        "gpin": "123", "propertystreet": "500 Granby St",
        "location": {"latitude": "36.86", "longitude": "-76.29"}})
    assert m["lat"] == 36.86 and m["lng"] == -76.29
    assert m["address"] == "500 Granby St"
    # GeoJSON point flavor
    m2 = phase0.normalize_record("Norfolk", "VA", {
        "gpin": "9", "the_geom": {"type": "Point",
                                  "coordinates": [-76.29, 36.86]}})
    assert m2["lat"] == 36.86 and m2["lng"] == -76.29
    # A scalar latitude column beats the dict when both exist
    m3 = phase0.normalize_record("Norfolk", "VA", {
        "gpin": "9", "latitude": "36.90", "longitude": "-76.20",
        "location": {"latitude": "1.0", "longitude": "2.0"}})
    assert float(m3["lat"]) == 36.90


def test_coords_backfilled_from_sibling_feed_by_address(tmp_path):
    """Norfolk's assessor feed carries no geometry at all, leaving every
    multifamily row coordinate-blind (all its comp subjects get skipped).
    A permits row for the SAME address has verified coords - borrow them."""
    db = tmp_path / "workbench.db"
    _seed_muni(db, [
        _norfolk("1234567890"),
        ("Norfolk", "VA", "permits", {
            "address": "1234567890 Granby St",
            "lat": 36.8508, "lng": -76.2859}),
    ])
    report = phase0.build_spine(db)
    assert report.coords_backfilled == 1
    with sqlite3.connect(db) as conn:
        lat, lng = conn.execute(
            "SELECT lat, lng FROM properties_8r").fetchone()
    assert abs(lat - 36.8508) < 1e-6 and abs(lng - -76.2859) < 1e-6


def test_backfill_never_invents_coords_for_unknown_addresses(tmp_path):
    """No sibling record shares the address -> the row stays coordinate-
    free (a missing coordinate matches by address; a wrong one matches the
    wrong parcel)."""
    db = tmp_path / "workbench.db"
    _seed_muni(db, [
        _norfolk("1234567890"),
        ("Norfolk", "VA", "permits", {
            "address": "999 Different Ave",
            "lat": 36.8508, "lng": -76.2859}),
    ])
    report = phase0.build_spine(db)
    assert report.coords_backfilled == 0
    with sqlite3.connect(db) as conn:
        lat, lng = conn.execute(
            "SELECT lat, lng FROM properties_8r").fetchone()
    assert lat is None and lng is None


# ---------------------------------------------------------------------------
# Backbone prune (owner directive 2026-08-03: 10+ doors only)
# ---------------------------------------------------------------------------

def _prune_db(tmp_path):
    import sqlite3
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE properties_8r (
        property_id TEXT PRIMARY KEY, fips TEXT, apn TEXT, address TEXT,
        city TEXT, units INTEGER, use_code TEXT)""")
    conn.executemany(
        "INSERT INTO properties_8r VALUES (?,?,?,?,?,?,?)", [
            ("8R-A", "51710", "1-1", "1 Main St", "Norfolk", 48, "Apartment"),
            ("8R-B", "51710", "1-2", "2 Main St", "Norfolk", 4, "Duplex"),
            ("8R-C", "51710", "1-3", "3 Main St", "Norfolk", 1, "One Family"),
            # Portsmouth publishes NO unit counts - these are the learner's
            # anchors and next cycle's classification targets.
            ("8R-D", "51740", "2-1", "9 High St", "Portsmouth", None, "18"),
            ("8R-E", "51740", "2-2", "11 High St", "Portsmouth", None, "9"),
        ])
    conn.commit()
    return db, conn


def test_prune_drops_known_sub10_and_keeps_mf_and_unknown(tmp_path):
    from core.phase0 import prune_backbone

    db, conn = _prune_db(tmp_path)
    assert prune_backbone(conn) == 2                  # the 4- and 1-unit rows
    left = {r[0] for r in conn.execute(
        "SELECT property_id FROM properties_8r")}
    assert left == {"8R-A", "8R-D", "8R-E"}, (
        "units-NULL rows must survive - pruning the unknown freezes every "
        "blind city at zero forever")


def test_prune_preserves_the_full_roll_in_parcel_index(tmp_path):
    from core.phase0 import prune_backbone

    db, conn = _prune_db(tmp_path)
    prune_backbone(conn)
    n = conn.execute("SELECT COUNT(*) FROM parcel_index").fetchone()[0]
    assert n == 5, "parcel_index must hold every parcel, pruned or not"
    units = dict(conn.execute(
        "SELECT apn, units FROM parcel_index WHERE city='Norfolk'"))
    assert units["1-2"] == 4                          # the pruned rows' facts survive


def test_keep_all_escape_hatch(tmp_path, monkeypatch):
    from core.phase0 import prune_backbone

    monkeypatch.setenv("ER_SPINE_KEEP_ALL", "1")
    db, conn = _prune_db(tmp_path)
    assert prune_backbone(conn) == 0
    n = conn.execute("SELECT COUNT(*) FROM properties_8r").fetchone()[0]
    assert n == 5


def test_the_badge_still_refutes_against_a_pruned_backbone(tmp_path):
    """The blue check's power to say NO depends on the roll rows the prune
    removes: a user claiming 48 units on a parcel the city says is 4 must
    still fail, from parcel_index."""
    from core.phase0 import prune_backbone
    from core import user_properties as up
    import sqlite3

    db, conn = _prune_db(tmp_path)
    prune_backbone(conn)
    conn.commit()
    conn.close()
    row = up.submit_property(name="Phantom Lofts", address="2 Main Street",
                             city="Norfolk", units=48, db_path=db)
    res = up.validate_property(row["user_property_id"], db)
    assert res.status == up.FAILED
    assert "4" in res.reason and "48" in res.reason


def test_richmond_public_data_set_pid_joins_the_parcel_backbone():
    """2026-08-11 3AM review: 76,976 rva.gov Public Data Set rows landed as
    orphan properties because their parcel key (PID) had no alias - units
    and assessed values never reached the radar-visible parcels. PID must
    map to apn, PRIMARY_USE to use_code; an explicit ParcelID still wins."""
    from core.phase0 import normalize_record, build_row

    wb = {"PID": "N0001721039", "PRIMARY_USE": "Multi-Family",
          "TOTAL_VALUE": 512000, "YEAR_BUILT": 1955, "MAIL_ADDR": "x",
          "_file": "https://www.rva.gov/f/PublicDataSet.xlsx"}
    rep = phase0.CoverageReport()
    m = normalize_record("Richmond", "VA", wb, rep)
    assert m["apn"] == "N0001721039"
    assert m["use_code"] == "Multi-Family"
    assert m["assessed_value"] == 512000
    # Bookkeeping columns must not clutter the unmapped-keys report.
    assert not rep.unmapped_keys.get("Richmond")

    # Same PID as the COR/VDEM row -> same 8R id -> COALESCE merge.
    api = build_row("Richmond", "VA", {"ParcelID": "N0001721039"})
    assert build_row("Richmond", "VA", wb).property_id == api.property_id
    # An explicit parcel column always beats an internal PID.
    both = normalize_record("Richmond", "VA", {"ParcelID": "A1", "PID": "9"})
    assert both["apn"] == "A1"


def test_richmond_pin_format_beats_apn_column_priority():
    """2026-08-11 4:44 review: join health stayed 0/76,976 with the PID
    alias in place - the COR layer carries a column literally named APN
    (alias priority 0) holding a numeric id that matches nothing, while the
    real PIN sits in ParcelID. For cities with a known PIN shape, format
    wins over column priority."""
    from core.phase0 import normalize_record

    cor = normalize_record("Richmond", "VA",
                           {"APN": "405010001", "ParcelID": "N0001721039"})
    assert cor["apn"] == "N0001721039"
    # No PIN-shaped value anywhere -> keep the priority winner unchanged.
    numeric = normalize_record("Richmond", "VA",
                               {"APN": "405010001", "GPIN": "74807"})
    assert numeric["apn"] == "405010001"
    # Other cities keep pure column priority (no format table entry).
    nn = normalize_record("Newport News", "VA",
                          {"APN": "123456", "ParcelID": "NN-9"})
    assert nn["apn"] == "123456"


def test_richmond_workbook_assessment_columns_map_and_declutter():
    """The workbook's ASSSESS_TOTAL_VALUE_1 (sic - the file's own triple-S
    spelling) is the current assessment and must reach assessed_value; the
    LAND_ADJ_*/history columns saturated all 24 unmapped-report slots and
    hid whether a units column exists, so they stay out of the report."""
    from core.phase0 import normalize_record

    rep = phase0.CoverageReport()
    m = normalize_record("Richmond", "VA", {
        "PID": "C0010124002", "ASSSESS_TOTAL_VALUE_1": 412000,
        "ASSSESS_TOTAL_VALUE_2": 398000, "ASSSESS_LAND_VALUE_1": 90000,
        "ASSSESS_IMP_VALUE_1": 322000, "LAND_ADJ_1_CODE": "A",
        "LAND_ADJ_1_VAL": 5000, "LAND_ADJ_8_VAL": 1}, rep)
    assert m["apn"] == "C0010124002"
    assert m["assessed_value"] == 412000
    assert not rep.unmapped_keys.get("Richmond")
    # An explicit total-value column still beats the workbook spelling.
    both = normalize_record("Richmond", "VA",
                            {"TOTAL_VALUE": 1, "ASSSESS_TOTAL_VALUE_1": 2})
    assert both["assessed_value"] == 1
