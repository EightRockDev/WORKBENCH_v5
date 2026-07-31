"""Feed discovery: a candidate layer must be big enough to be a parcel roll.

Hampton's discovered feed carried 716 records against a ~50,000-parcel city.
Nothing about its FIELDS was wrong — address, apn, assessed_value, use_code,
year_built, lat/lng all present, score 12 — so field scoring accepted it and
Phase 0 then reported the city as having no multifamily data at all. It was a
coastal-zone study extract, not the assessor roll.

Size is the signal that separates the two, and it is one cheap query
(`returnCountOnly`) per candidate.
"""

from __future__ import annotations

from scripts.discover_feeds import (
    PLAUSIBLE_ROLL_MIN,
    layer_record_count,
    size_adjustment,
)


def _fetch_returning(count):
    def fetch(url, params=None):
        assert params and params.get("returnCountOnly") == "true"
        assert url.endswith("/query")
        return {"count": count} if count is not None else {}
    return fetch


def test_reads_the_record_count():
    assert layer_record_count("http://x/0", _fetch_returning(36_464)) == 36_464


def test_missing_count_is_none_not_zero():
    """A server that won't answer must not look like an empty layer."""
    assert layer_record_count("http://x/0", _fetch_returning(None)) is None
    assert layer_record_count("http://x/0", lambda u, p=None: None) is None


def test_a_full_roll_is_promoted():
    delta, note = size_adjustment(36_464)
    assert delta > 0
    assert "36,464 records" in note


def test_a_study_extract_is_demoted_and_explained():
    """The Hampton case: kept as a candidate, but ranked below a real roll and
    labelled so the operator can see what happened."""
    delta, note = size_adjustment(716)
    assert delta < 0
    assert "716" in note
    assert "too small to be a full parcel roll" in note


def test_unknown_size_neither_helps_nor_hurts():
    delta, note = size_adjustment(None)
    assert delta == 0
    assert "size unknown" in note


def test_a_real_roll_outranks_a_subset_with_identical_fields():
    """The whole point: same fields, same score, size decides."""
    base = 12
    roll = base + size_adjustment(36_464)[0]
    subset = base + size_adjustment(716)[0]
    assert roll > subset


def test_the_threshold_is_below_the_smallest_hampton_roads_city():
    """Suffolk, the smallest, has roughly 30K parcels — the floor must not
    exclude a legitimate city roll."""
    assert PLAUSIBLE_ROLL_MIN < 30_000
    assert size_adjustment(30_000)[0] > 0
