"""One building must end up as one property, or not be touched at all.

Richmond's lots arrive twice — the COR feed carries unit counts on numeric
PINs, the rva.gov workbook carries assessed values on letter PINs, nothing
joins them and no Richmond source maps a usable address. Position is the
only bridge left, and matching buildings by position is dangerous: a
centroid a few metres off belongs to the lot next door, and a wrong unit
count becomes a comp, then an underwriting input, unquestioned.

An adversarial review of the first version (2026-08-31) confirmed three
ways it went wrong, and each has a test here that fails against it:

  * the grid was sized in degrees of latitude and applied to both axes, so
    the search window was ~19.8 m wide against a 25 m radius at Richmond's
    latitude — which not only missed true twins but HID the runner-up,
    letting the ambiguity rule pass a coin flip as certain;
  * nothing required the two rows to come from different feeds, so a
    marina handed its 92 slips to the house 18 m away;
  * it copied the count and left both rows, so one building became two
    multifamily entities two metres apart.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from core.geo_bridge import (
    AMBIGUITY_MARGIN_M,
    M_PER_DEG_LAT,
    GeoPoint,
    apn_shape,
    bridge_units,
    merge_duplicate_parcels,
    metres_between,
)

# Richmond, near the Fan.
LAT, LNG = 37.5407, -77.4360

# The two real id schemes — what makes a pair eligible at all.
COR_APN = "405010001"          # 9 digits
PIN_APN = "C0010124002"        # letter + 10 digits


def north(m: float) -> float:
    return LAT + m / M_PER_DEG_LAT


def east(m: float) -> float:
    return LNG + m / (111_320.0 * math.cos(math.radians(LAT)))


def cor(key: str, lat: float, lng: float, units: int | None = 48) -> GeoPoint:
    return GeoPoint(key, lat, lng, units, "Richmond", COR_APN)


def pin(key: str, lat: float, lng: float, units: int | None = None) -> GeoPoint:
    return GeoPoint(key, lat, lng, units, "Richmond", PIN_APN)


# ---------------------------------------------------------------------------
# Distance and id shape
# ---------------------------------------------------------------------------

def test_distance_is_metres_on_both_axes():
    assert 99 < metres_between(LAT, LNG, north(100), LNG) < 101
    assert 99 < metres_between(LAT, LNG, LAT, east(100)) < 101


@pytest.mark.parametrize("apn,shape", [
    ("405010001", "9d"),
    ("C0010124002", "1a10d"),
    ("N0001746010", "1a10d"),
    ("", ""),
    (None, ""),
])
def test_apn_shape_describes_the_scheme_not_the_number(apn, shape):
    assert apn_shape(apn) == shape


# ---------------------------------------------------------------------------
# The grid — the bug that made "unambiguous" a lie
# ---------------------------------------------------------------------------

def test_a_twin_due_east_inside_the_radius_is_found():
    """Regression: cells sized in degrees of latitude gave a ~19.8 m
    east/west window for a 25 m radius here, so this pair read as
    'too far'."""
    matches, rep = bridge_units([cor("COR-1", LAT, LNG)],
                                [pin("PIN-1", LAT, east(24.0))], radius_m=25)

    assert [m.target_key for m in matches] == ["PIN-1"], (
        f"a twin 24 m due east was not seen: {rep.lines()}")


def test_an_east_west_runner_up_is_not_invisible():
    """The dangerous half of the same bug. True twin 24 m west, a
    DIFFERENT lot 24 m east: the old window saw one and called it
    certain."""
    matches, rep = bridge_units(
        [cor("COR-1", LAT, LNG)],
        [pin("PIN-TWIN", LAT, east(-24.0)),
         pin("PIN-NEIGHBOUR", LAT, east(24.0))], radius_m=25)

    assert matches == [], "picked one of two equidistant lots"
    assert rep.rejected_ambiguous == 1


def test_the_search_window_covers_the_radius_in_every_direction():
    """Sweep the compass: nothing inside the radius may be unreachable."""
    misses = []
    for deg in range(0, 360, 15):
        r = math.radians(deg)
        matches, _ = bridge_units(
            [cor("COR-1", LAT, LNG)],
            [pin("PIN-1", north(24.0 * math.cos(r)), east(24.0 * math.sin(r)))],
            radius_m=25)
        if not matches:
            misses.append(deg)
    assert not misses, f"twins at 24 m were invisible at bearings {misses}"


# ---------------------------------------------------------------------------
# Rules 1 and 2 — same city, different id scheme
# ---------------------------------------------------------------------------

def test_two_parcels_from_the_same_feed_are_never_merged():
    """The marina case. Chesapeake's boat slips (92 'units') sat 18 m from
    a single-family house: same feed, same id scheme, two real lots."""
    marina = GeoPoint("8R-MARINA", LAT, LNG, 92, "Chesapeake", "3352001")
    house = GeoPoint("8R-HOUSE", north(18), LNG, None, "Chesapeake", "3352002")

    matches, rep = bridge_units([marina], [house])

    assert matches == [], "a marina handed its slips to the house next door"
    assert rep.rejected_same_scheme == 1


def test_parcels_in_different_cities_are_never_merged():
    a = GeoPoint("A", LAT, LNG, 48, "Richmond", COR_APN)
    b = GeoPoint("B", north(2), LNG, None, "Henrico", PIN_APN)
    assert bridge_units([a], [b])[0] == []


# ---------------------------------------------------------------------------
# Rules 3-5 — distance, ambiguity, mutual nearest
# ---------------------------------------------------------------------------

def test_a_parcel_beyond_tolerance_is_not_matched():
    matches, rep = bridge_units([cor("COR-1", LAT, LNG)],
                                [pin("PIN-1", north(120), LNG)], radius_m=25)
    assert matches == [] and rep.rejected_far == 1


def test_a_clear_winner_beats_a_distant_runner_up():
    matches, _ = bridge_units(
        [cor("COR-1", LAT, LNG)],
        [pin("PIN-NEAR", north(1), LNG), pin("PIN-FAR", north(22), LNG)])
    assert [m.target_key for m in matches] == ["PIN-NEAR"]


def test_near_identical_centroids_are_refused_not_split():
    """Stacked condo parcels share a centroid. The first version switched
    the ambiguity rule OFF below 3 m — exactly where they live."""
    matches, rep = bridge_units(
        [cor("COR-1", LAT, LNG)],
        [pin("PIN-A", north(0.4), LNG), pin("PIN-B", north(0.5), LNG)])

    assert matches == [], "split a pair of stacked parcels on a 10 cm margin"
    assert rep.rejected_ambiguous == 1


def test_the_ambiguity_margin_is_absolute_as_well_as_proportional():
    """1 m vs 3 m sails through a 2x ratio (3 >= 2) yet the two candidates
    are 2 m apart — indistinguishable for a parcel centroid. The absolute
    margin is what refuses it; the ratio alone would not."""
    near, far = 1.0, 3.0
    assert far >= near * 2.0, "fixture no longer clears the ratio test"
    assert far - near < AMBIGUITY_MARGIN_M, "fixture no longer trips the margin"
    matches, rep = bridge_units(
        [cor("COR-1", LAT, LNG)],
        [pin("PIN-A", north(near), LNG), pin("PIN-B", north(far), LNG)])
    assert matches == [] and rep.rejected_ambiguous == 1


def test_many_small_parcels_do_not_all_claim_one_big_neighbour():
    src = [cor("COR-1", north(3), LNG, 10), cor("COR-2", north(6), LNG, 20),
           cor("COR-3", north(9), LNG, 30)]
    matches, rep = bridge_units(src, [pin("PIN-BIG", north(4), LNG)])
    assert len(matches) <= 1, "one parcel absorbed several unit counts"
    assert rep.rejected_not_mutual >= 1 or rep.rejected_ambiguous >= 1


def test_the_result_does_not_depend_on_row_order():
    a, b = cor("COR-A", north(1), LNG, 100), cor("COR-B", north(40), LNG, 200)
    tgt = [pin("PIN-1", LAT, LNG)]
    first = bridge_units([a, b], tgt)[0]
    second = bridge_units([b, a], tgt)[0]
    assert [(m.target_key, m.units) for m in first] == \
           [(m.target_key, m.units) for m in second]


def test_sources_without_units_or_coordinates_are_ignored():
    assert bridge_units([cor("C", LAT, LNG, None)],
                        [pin("P", north(1), LNG)])[0] == []
    assert bridge_units([cor("C", LAT, LNG, 0)],
                        [pin("P", north(1), LNG)])[0] == []
    assert bridge_units([], [])[0] == []


def test_it_scales_to_a_city():
    """108k parcels against 2.4k unit-bearing points is the real shape."""
    import time
    n = 2_000
    src = [GeoPoint(f"COR-{i}", north(i * 40.0), LNG, 10 + i % 40,
                    "Richmond", COR_APN) for i in range(n)]
    tgt = [GeoPoint(f"PIN-{i}", north(i * 40.0 + 1.5), LNG, None,
                    "Richmond", PIN_APN) for i in range(n * 5)]

    t0 = time.perf_counter()
    _matches, rep = bridge_units(src, tgt)
    elapsed = time.perf_counter() - t0

    assert rep.matched == n, rep.lines()
    assert elapsed < 15.0, f"took {elapsed:.1f}s - the grid index is not working"


# ---------------------------------------------------------------------------
# The merge, through the backbone table
# ---------------------------------------------------------------------------

def _spine_db(tmp_path, rows):
    conn = sqlite3.connect(tmp_path / "wb.db")
    conn.execute("""CREATE TABLE properties_8r (
        property_id TEXT PRIMARY KEY, fips TEXT, apn TEXT, address TEXT,
        city TEXT, state TEXT, zip TEXT, units INTEGER, year_built INTEGER,
        sqft REAL, use_code TEXT, r8_form TEXT, r8_market TEXT,
        r8_submarket TEXT, assessed_value REAL, owner_name TEXT,
        owner_address TEXT, lat REAL, lng REAL, provenance TEXT,
        built_at TEXT)""")
    conn.executemany(
        "INSERT INTO properties_8r (property_id, fips, apn, city, units, "
        "assessed_value, owner_name, lat, lng) VALUES (?,?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    return conn


def test_one_building_ends_as_one_property(tmp_path):
    """The whole point. The COR row carries 48 units and no value; its
    letter-PIN twin carries the value and no units. Afterwards there is
    ONE row holding both — not two multifamily entities 2 m apart."""
    conn = _spine_db(tmp_path, [
        ("8R-COR", "51760", COR_APN, "Richmond", 48, None, "DOLLY LLC",
         LAT, LNG),
        ("8R-PIN", "51760", PIN_APN, "Richmond", None, 4_200_000, None,
         north(2), LNG),
    ])

    merged, rep, by_city = merge_duplicate_parcels(conn)

    assert merged == 1, rep.lines()
    assert by_city == {"Richmond": 1}
    rows = conn.execute(
        "SELECT property_id, units, assessed_value, owner_name "
        "  FROM properties_8r").fetchall()
    assert len(rows) == 1, "the duplicate survived - Richmond would double-count"
    pid, units, value, owner = rows[0]
    assert pid == "8R-PIN", "the value-bearing row should survive"
    assert units == 48, "the survivor did not absorb the unit count"
    assert value == 4_200_000, "the survivor lost its own assessed value"
    assert owner == "DOLLY LLC", "the owner name was not carried over"


def test_an_unmatched_parcel_is_left_exactly_as_it_was(tmp_path):
    conn = _spine_db(tmp_path, [
        ("8R-COR", "51760", COR_APN, "Richmond", 48, None, None, LAT, LNG),
        ("8R-FAR", "51760", PIN_APN, "Richmond", None, 1, None,
         north(900), LNG),
    ])
    merged, _rep, _by = merge_duplicate_parcels(conn)
    assert merged == 0
    assert dict(conn.execute(
        "SELECT property_id, units FROM properties_8r")) == {
            "8R-COR": 48, "8R-FAR": None}


def test_a_row_with_its_own_count_is_never_merged_away(tmp_path):
    """Two real counts means two real buildings, whatever the distance."""
    conn = _spine_db(tmp_path, [
        ("8R-A", "51760", COR_APN, "Richmond", 48, None, None, LAT, LNG),
        ("8R-B", "51760", PIN_APN, "Richmond", 12, None, None, north(2), LNG),
    ])
    merged, _rep, _by = merge_duplicate_parcels(conn)
    assert merged == 0
    assert conn.execute("SELECT count(*) FROM properties_8r").fetchone()[0] == 2


def test_the_merge_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ER_NO_GEO_BRIDGE", "1")
    conn = _spine_db(tmp_path, [
        ("8R-A", "51760", COR_APN, "Richmond", 48, None, None, LAT, LNG),
        ("8R-B", "51760", PIN_APN, "Richmond", None, None, None,
         north(2), LNG),
    ])
    assert merge_duplicate_parcels(conn)[0] == 0
    assert conn.execute("SELECT count(*) FROM properties_8r").fetchone()[0] == 2


def test_a_merged_sub_ten_parcel_is_pruned_like_any_other(tmp_path):
    """A 4-unit building is not multifamily however its count arrived —
    which only holds because the merge runs BEFORE prune_backbone."""
    import inspect

    from core import phase0

    lines = inspect.getsource(phase0.build_spine).splitlines()

    def call_line(needle: str) -> int:
        for i, ln in enumerate(lines):
            if needle in ln.split("#", 1)[0]:
                return i
        raise AssertionError(f"{needle} is not called in build_spine")

    assert call_line("merge_duplicate_parcels(conn)") < \
           call_line("prune_backbone(conn)"), (
        "the merge runs after the prune, so a merged sub-10 parcel would "
        "sit on the backbone as multifamily forever")

    conn = _spine_db(tmp_path, [
        ("8R-A", "51760", COR_APN, "Richmond", 4, None, None, LAT, LNG),
        ("8R-B", "51760", PIN_APN, "Richmond", None, None, None,
         north(2), LNG),
    ])
    merge_duplicate_parcels(conn)
    assert phase0.prune_backbone(conn) == 1
    assert conn.execute("SELECT count(*) FROM properties_8r").fetchone()[0] == 0


def test_the_spine_generation_was_bumped():
    """phase0 skips the rebuild when its inputs are unchanged, and that
    fingerprint includes a code generation. Without a bump the host keeps
    serving the pre-merge backbone and none of this ever runs."""
    from core import phase0
    assert phase0.SPINE_BUILD_GENERATION >= 2, (
        "SPINE_BUILD_GENERATION not bumped - the spine build is skipped as "
        "'inputs unchanged' and the merge never runs")


def test_a_city_with_only_one_id_scheme_is_never_loaded(tmp_path):
    """Memory. Loading the whole 2.3M-row backbone to solve one city's
    problem would put ~2.3M point objects in the office box's RAM; the
    merge only visits cities holding BOTH halves."""
    conn = _spine_db(tmp_path, [
        # Norfolk: every row already has units — no missing half, skip it.
        ("8R-N1", "51710", "2000-77", "Norfolk", 250, None, None, LAT, LNG),
        ("8R-N2", "51710", "2000-78", "Norfolk", 12, None, None,
         north(2), LNG),
        # Richmond: both halves present, so it is the only city visited.
        ("8R-R1", "51760", COR_APN, "Richmond", 48, None, None,
         north(500), LNG),
        ("8R-R2", "51760", PIN_APN, "Richmond", None, None, None,
         north(502), LNG),
    ])
    merged, rep, by_city = merge_duplicate_parcels(conn)

    assert merged == 1 and by_city == {"Richmond": 1}
    assert rep.sources == 1, (
        f"Norfolk's rows were loaded despite having no missing half: "
        f"{rep.lines()}")


def test_the_rejection_breakdown_is_never_hidden():
    """A run that merges nothing must still say WHICH rule refused every
    candidate — '0 merged' alone cannot tell 'the feeds do not overlap'
    from 'the coordinates are in the wrong projection' from 'it never
    ran'. The first version printed the breakdown only when sources > 0,
    suppressing it in exactly the case worth diagnosing."""
    from core.geo_bridge import BridgeReport
    from core.phase0 import CoverageReport

    rep = CoverageReport()
    rep.geo_bridge = BridgeReport(sources=0, targets=0)
    body = rep.summary()

    assert "Geometry unit bridge" in body, (
        "the diagnostic block vanishes exactly when nothing matched")


def test_each_city_reports_its_own_refusals(tmp_path):
    """Richmond merged 0 while Atlanta merged 92 (2026-09-01), and the
    global totals could not say why. A per-city line must name the rule
    that refused the city this was built for."""
    conn = _spine_db(tmp_path, [
        # Richmond: both halves, but 900 m apart — refused as too far.
        ("8R-R1", "51760", COR_APN, "Richmond", 48, None, None, LAT, LNG),
        ("8R-R2", "51760", PIN_APN, "Richmond", None, None, None,
         north(900), LNG),
        # Atlanta: a clean pair that merges.
        ("8R-A1", "13121", "14 0055 LL1234", "Atlanta", 60, None, None,
         north(5000), LNG),
        ("8R-A2", "13121", "17001500010", "Atlanta", None, None, None,
         north(5002), LNG),
    ])
    merged, rep, by_city = merge_duplicate_parcels(conn)

    assert merged == 1 and by_city == {"Atlanta": 1}
    lines = "\n".join(rep.city_lines())
    assert "Richmond" in lines and "Atlanta" in lines
    richmond = [ln for ln in rep.city_lines() if ln.startswith("Richmond")][0]
    assert "merged     0" in richmond or "merged      0" in richmond
    assert "far 1" in richmond, (
        f"Richmond's line does not name the rule that refused it: {richmond}")
