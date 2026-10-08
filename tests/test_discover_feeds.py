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

def test_discover_stops_on_its_own_budget_and_says_where(capsys,
                                                       monkeypatch):
    """From 2026-10-01 every run hit the autopilot's 900s kill and printed
    nothing. discover() must stop starting cities once its budget is spent
    and say which city it stopped before."""
    import scripts.discover_feeds as d
    now = {"t": 0.0}

    def city_takes_350s(city, fetch=None):
        now["t"] += 350
        return iter(())
    monkeypatch.setattr(d, "search_agol", city_takes_350s)
    found = d.discover(cities=[("A", "VA"), ("B", "VA"), ("C", "VA")],
                       fetch=lambda *a, **k: None,
                       soda=lambda *a, **k: None,
                       budget_s=600, clock=lambda: now["t"])
    # A ends at 350s (under budget, kept); B ends at 700s (over budget
    # mid-city, so its partial result is dropped and the run stops).
    assert found.cut_short_at == "B"
    assert "A" in found and "B" not in found and "C" not in found
    out = capsys.readouterr().out
    assert "probing A" in out and "probing B" in out
    assert "probing C" not in out
    assert "stopped during B" in out


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


# ------------------------- shared feed list (2026-10-04)

def test_each_run_replaces_only_its_own_markets():
    """VA and national discovery share feeds_extra.json. Each must keep
    the other's markets - wholesale overwrite meant the pull only ever
    saw whichever step ran last."""
    import scripts.discover_feeds as d
    existing = [{"market": "Atlanta", "url": "a"},
                {"market": "Hampton", "url": "h-old"},
                {"market": "Richmond", "url": "r"}]
    fresh = [{"market": "Hampton", "url": "h-new"},
             {"market": "Portsmouth", "url": "p"},
             {"market": "Richmond", "url": "r"}]
    merged, kept = d.merge_specs(
        existing, fresh, {"Hampton", "Portsmouth", "Richmond", "Norfolk"})
    urls = [x["url"] for x in merged]
    assert urls == ["h-new", "p", "r", "a"]
    assert kept == 1                       # Atlanta survived the VA run
    assert "h-old" not in urls             # covered market was replaced


def test_main_merges_with_the_file_on_disk(tmp_path, monkeypatch, capsys):
    import json
    import scripts.discover_feeds as d
    data = tmp_path / "data"
    data.mkdir()
    feeds = data / "feeds_extra.json"
    feeds.write_text(json.dumps([{"market": "Atlanta", "url": "a"}]))
    fake_script = tmp_path / "scripts" / "discover_feeds.py"
    monkeypatch.setattr(d, "__file__", str(fake_script))
    monkeypatch.setattr(d, "discover", lambda **kw: d.Partial(
        {"Hampton": [{"market": "Hampton", "url": "h", "note": "n"}]}))
    assert d.main(["--va"]) == 0
    merged = json.loads(feeds.read_text())
    assert {x["url"] for x in merged} == {"a", "h"}
    assert "1 kept from other markets" in capsys.readouterr().out


def test_a_single_slow_city_cannot_overrun_the_budget(capsys):
    """2026-10-06: Hampton alone ran past the cap - the budget was only
    checked between cities. Once the deadline passes mid-city, no further
    request may go out, and the half-probed city must not be saved."""
    import scripts.discover_feeds as d
    now = {"t": 0.0}
    calls = {"n": 0}

    def slow_fetch(url, params=None):
        calls["n"] += 1
        now["t"] += 100            # every request "takes" 100s
        return None

    found = d.discover(cities=[("Hampton", "VA"), ("Suffolk", "VA")],
                       extra_roots=["https://x/arcgis/rest/services"],
                       fetch=slow_fetch, soda=lambda *a, **k: None,
                       budget_s=250, clock=lambda: now["t"])
    assert found.cut_short_at == "Hampton"
    assert "Hampton" not in found and "Suffolk" not in found
    assert calls["n"] <= 3, "requests kept going after the deadline"
    assert "stopped during Hampton" in capsys.readouterr().out


def test_one_url_serving_several_cities_keeps_every_city():
    """2026-10-08: the VGIN statewide layer is one url for every VA city.
    A url-only dedupe collapsed four cities' entries into one and ~181K
    parcels fell out of the backbone."""
    import scripts.discover_feeds as d
    vgin = "https://vginmaps.vdem.virginia.gov/.../VA_Parcels/FeatureServer/0"
    fresh = [{"market": m, "url": vgin, "kind": "assessor"}
             for m in ("Chesapeake", "Hampton", "Portsmouth", "Richmond")]
    merged, _ = d.merge_specs([], fresh, {"Chesapeake", "Hampton",
                                          "Portsmouth", "Richmond"})
    assert sorted(x["market"] for x in merged) == [
        "Chesapeake", "Hampton", "Portsmouth", "Richmond"]


def test_national_run_never_touches_the_va_cities(tmp_path, monkeypatch):
    """Richmond is in both target lists; the runs kept replacing each
    other's Richmond feeds. The VA run owns VA cities outright."""
    import json
    import scripts.discover_feeds as d
    data = tmp_path / "data"
    data.mkdir()
    feeds = data / "feeds_extra.json"
    va_specs = [{"market": "Richmond", "url": "vgin", "kind": "assessor"},
                {"market": "Hampton", "url": "vgin", "kind": "assessor"}]
    feeds.write_text(json.dumps(va_specs))
    monkeypatch.setattr(d, "__file__",
                        str(tmp_path / "scripts" / "discover_feeds.py"))
    monkeypatch.setattr(d, "discover", lambda **kw: d.Partial({
        "Richmond": [{"market": "Richmond", "url": "other", "note": "n",
                      "kind": "assessor"}],
        "Atlanta": [{"market": "Atlanta", "url": "a", "note": "n",
                     "kind": "assessor"}]}))
    assert d.main([]) == 0                          # national mode
    merged = json.loads(feeds.read_text())
    got = sorted((x["market"], x["url"]) for x in merged)
    assert got == [("Atlanta", "a"), ("Hampton", "vgin"),
                   ("Richmond", "vgin")]
