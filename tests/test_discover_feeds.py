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


# ------------------------------------------------ time budget (2026-10-01)

def test_discover_stops_on_its_own_budget_and_says_where(capsys):
    """From 2026-10-01 every run hit the autopilot's 900s kill and printed
    nothing. discover() must stop starting cities once its budget is spent
    and say which city it stopped before."""
    import scripts.discover_feeds as d
    ticks = iter([0, 0, 100, 900, 900])     # start, city1, city2, city3...
    found = d.discover(cities=[("A", "VA"), ("B", "VA"), ("C", "VA")],
                       fetch=lambda *a, **k: None,
                       soda=lambda *a, **k: None,
                       budget_s=600, clock=lambda: next(ticks))
    assert found.cut_short_at == "C"
    out = capsys.readouterr().out
    assert "probing A" in out and "probing B" in out
    assert "stopping before C" in out


def test_partial_run_leaves_the_feed_list_untouched(tmp_path, monkeypatch,
                                                    capsys):
    """Overwriting feeds_extra.json with a partial result would silently
    drop every feed for the cities never reached."""
    import scripts.discover_feeds as d

    def partial(**kw):
        r = d.Partial({"Norfolk": [{"url": "u", "note": "n"}]})
        r.cut_short_at = "Richmond"
        return r
    monkeypatch.setattr(d, "discover", partial)
    written = []
    monkeypatch.setattr(d.Path, "write_text",
                        lambda self, *a, **k: written.append(self))
    assert d.main(["--va"]) == 0
    out = capsys.readouterr().out
    assert "stopped early at Richmond" in out
    assert "UNCHANGED" in out
    assert not any(p.name == "feeds_extra.json" for p in written)


def test_budget_fits_inside_the_autopilot_step_cap():
    import importlib.util
    import sys
    from pathlib import Path
    import scripts.discover_feeds as d
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "autopilot_run_b", root / "scripts" / "autopilot_run.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["autopilot_run_b"] = m
    spec.loader.exec_module(m)
    # leave room for the last city in flight plus printing results
    assert d.BUDGET_S <= m.STEP_TIMEOUTS["discover"] - 120
