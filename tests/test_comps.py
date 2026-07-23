"""Tests for core.comps — haversine + Bucket 1 / Bucket 2 selection."""

from __future__ import annotations

import math

import pytest

import config
from core.comps import get_comps, haversine_miles


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------


def test_haversine_zero_distance():
    """Same point → 0 miles."""
    assert haversine_miles(36.0, -76.0, 36.0, -76.0) == 0.0


def test_haversine_one_degree_latitude_about_69_miles():
    """1° latitude ≈ 69.1 statute miles (well-known reference)."""
    d = haversine_miles(36.0, -76.0, 37.0, -76.0)
    assert 69.0 < d < 69.5


def test_haversine_norfolk_to_va_beach():
    """Downtown Norfolk (36.85, -76.29) → VA Beach Town Center (36.85, -76.13).
    About 8.4-8.5 miles apart by haversine — sanity-check great-circle vs road distance."""
    d = haversine_miles(36.8508, -76.2859, 36.8529, -76.1331)
    assert 8.0 < d < 9.0


def test_haversine_symmetric():
    """Distance should be the same in either direction."""
    a = haversine_miles(36.0, -76.0, 37.0, -77.0)
    b = haversine_miles(37.0, -77.0, 36.0, -76.0)
    assert a == pytest.approx(b)


def test_haversine_clamps_floating_point_overshoot():
    """At antipodes, accumulated float error can push 'a' above 1.
    The clamp prevents math.sqrt of a negative."""
    # Antipode of (36, -76) is (-36, 104). Should give roughly half Earth circumference (~12,400 mi)
    d = haversine_miles(36.0, -76.0, -36.0, 104.0)
    expected = math.pi * config.EARTH_RADIUS_MILES
    assert d == pytest.approx(expected, rel=1e-3)


# ---------------------------------------------------------------------------
# Comp selection helpers
# ---------------------------------------------------------------------------


def _candidate(
    pid: str,
    name: str,
    asset_class: str | None,
    lat: float,
    lng: float,
    **extras,
) -> dict:
    """Build a candidate dict matching the schema row shape."""
    return {
        "property_id": pid,
        "name": name,
        "city": extras.get("city", "Norfolk"),
        "units": extras.get("units", 100),
        "year_built": extras.get("year_built", 1985),
        "avg_rent": extras.get("avg_rent", 1500.0),
        "avg_sqft": extras.get("avg_sqft", 850.0),
        "rent_per_sqft": extras.get("rent_per_sqft", 1.76),
        "asset_class": asset_class,
        "manager": extras.get("manager", "Manager Name"),
        "owner": extras.get("owner", "Owner LLC"),
        "latitude": lat,
        "longitude": lng,
    }


# Subject: Norfolk Class C at (36.85, -76.29)
SUBJECT = dict(
    subject_id="subject-1",
    subject_lat=36.85,
    subject_lon=-76.29,
    subject_class="C",
)


# ---------------------------------------------------------------------------
# Comp selection — exclusions
# ---------------------------------------------------------------------------


def test_comps_excludes_self():
    candidates = [
        _candidate("subject-1", "Subject", "C", 36.85, -76.29),
        _candidate("other-1",   "Other A", "C", 36.86, -76.29),
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    ids = [c.property_id for c in comps]
    assert "subject-1" not in ids
    assert ids == ["other-1"]


def test_comps_excludes_missing_latlng():
    candidates = [
        _candidate("nolat",   "Bad A", "C", None, None),  # type: ignore[arg-type]
        _candidate("good-1",  "Good A", "C", 36.86, -76.29),
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    ids = [c.property_id for c in comps]
    assert ids == ["good-1"]


def test_comps_excludes_beyond_bucket2_radius():
    """Candidate >5mi away is dropped entirely."""
    # ~10 miles north of subject — well outside the 5mi cap
    candidates = [
        _candidate("far-1", "Far Away", "C", 36.99, -76.29),
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    assert comps == []


# ---------------------------------------------------------------------------
# Bucket 1 mechanics
# ---------------------------------------------------------------------------


def test_bucket1_same_class_within_3mi():
    """Within 3mi + same class → Bucket 1."""
    candidates = [
        _candidate("close-c", "Close C", "C", 36.86, -76.29),  # ~0.7mi
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    assert len(comps) == 1
    assert comps[0].bucket == 1


def test_bucket1_excludes_different_class():
    """Within 3mi but DIFFERENT class → goes to Bucket 2 instead."""
    candidates = [
        _candidate("close-b", "Close B", "B", 36.86, -76.29),  # ~0.7mi, but B
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    assert len(comps) == 1
    assert comps[0].bucket == 2  # didn't qualify for B1, fell through to B2


def test_bucket1_max_8_capped():
    """Bucket 1 caps at 8 even if more same-class within 3mi."""
    # 10 candidates, all Class C, all close
    candidates = [
        _candidate(f"c-{i}", f"Class C {i}", "C", 36.85 + 0.001 * i, -76.29)
        for i in range(10)
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    bucket1 = [c for c in comps if c.bucket == 1]
    assert len(bucket1) == config.COMPS_BUCKET1_MAX  # 8
    # The two leftover Class C should land in Bucket 2
    bucket2 = [c for c in comps if c.bucket == 2]
    assert len(bucket2) == 2


def test_bucket1_excludes_beyond_3mi():
    """Same class but >3mi → Bucket 2."""
    # ~3.5mi north → past Bucket 1 radius, within Bucket 2
    candidates = [
        _candidate("far-c", "Far C", "C", 36.9 + 0.005, -76.29),
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    assert len(comps) == 1
    # Roughly check distance and bucket
    assert comps[0].distance_miles > 3.0
    assert comps[0].distance_miles < 5.0
    assert comps[0].bucket == 2


# ---------------------------------------------------------------------------
# Bucket 2 mechanics
# ---------------------------------------------------------------------------


def test_bucket2_max_4_capped():
    """Bucket 2 caps at 4 even with many candidates."""
    # 10 candidates, all Class B (not same class → all go to Bucket 2)
    candidates = [
        _candidate(f"b-{i}", f"Class B {i}", "B", 36.85 + 0.001 * i, -76.29)
        for i in range(10)
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    bucket2 = [c for c in comps if c.bucket == 2]
    assert len(bucket2) == config.COMPS_BUCKET2_MAX  # 4
    bucket1 = [c for c in comps if c.bucket == 1]
    assert len(bucket1) == 0  # no same-class candidates


def test_total_max_12_capped():
    """Total comps cap at 8 + 4 = 12 even with many candidates."""
    candidates = []
    # 10 Class C close
    for i in range(10):
        candidates.append(_candidate(f"c-{i}", f"C {i}", "C", 36.85 + 0.001 * i, -76.29))
    # 10 Class B close
    for i in range(10):
        candidates.append(_candidate(f"b-{i}", f"B {i}", "B", 36.85 + 0.001 * i, -76.29))
    comps = get_comps(candidates=candidates, **SUBJECT)
    assert len(comps) == config.COMPS_TOTAL_MAX  # 12


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------


def test_comps_sorted_within_each_bucket():
    """Within each bucket, comps should be sorted by distance ascending."""
    candidates = [
        _candidate("c-far",  "Far C",   "C", 36.85 + 0.02, -76.29),    # ~1.4mi
        _candidate("c-near", "Near C",  "C", 36.85 + 0.005, -76.29),   # ~0.35mi
        _candidate("c-mid",  "Mid C",   "C", 36.85 + 0.01, -76.29),    # ~0.7mi
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    assert [c.property_id for c in comps] == ["c-near", "c-mid", "c-far"]
    # All in Bucket 1 (same class, within 3mi)
    assert all(c.bucket == 1 for c in comps)
    # And distances strictly ascending
    for i in range(1, len(comps)):
        assert comps[i].distance_miles > comps[i - 1].distance_miles


def test_bucket1_listed_before_bucket2():
    """Output order: all of Bucket 1 first, then all of Bucket 2."""
    candidates = [
        _candidate("b-near",  "Near B",  "B", 36.851, -76.29),  # close, but Class B → B2
        _candidate("c-mid",   "Mid C",   "C", 36.86,  -76.29),  # Class C → B1
    ]
    comps = get_comps(candidates=candidates, **SUBJECT)
    # Even though "b-near" is closer, "c-mid" comes first because it's in Bucket 1
    assert comps[0].property_id == "c-mid"
    assert comps[0].bucket == 1
    assert comps[1].property_id == "b-near"
    assert comps[1].bucket == 2


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


def test_no_candidates_returns_empty():
    assert get_comps(candidates=[], **SUBJECT) == []


def test_subject_class_none_treats_all_as_different_class():
    """When subject_class is None, no candidates qualify for B1
    (require_same_class=True can't match None ≠ 'C')."""
    candidates = [
        _candidate("c-1", "C-1", "C", 36.86, -76.29),
    ]
    comps = get_comps(
        subject_id="subject", subject_lat=36.85, subject_lon=-76.29,
        subject_class=None, candidates=candidates,
    )
    assert len(comps) == 1
    assert comps[0].bucket == 2  # fell through to Bucket 2
