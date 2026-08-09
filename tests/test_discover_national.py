"""National feed discovery (owner directive 2026-08-09): the discovery engine
must stamp the CORRECT state on found feeds and only run VA's VGIN fallback
for VA cities. All network is stubbed."""

from __future__ import annotations

import importlib


def _reload():
    import scripts.discover_feeds as d
    importlib.reload(d)
    return d


def test_discover_accepts_city_state_tuples_and_stamps_state(monkeypatch):
    d = _reload()
    # One qualifying ArcGIS layer for a non-VA city, stubbed at the helper
    # seam (not raw HTTP) so we isolate the state-stamping behavior.
    monkeypatch.setattr(d, "search_agol", lambda city, fetch: [
        ("https://x/FeatureServer/0", "Travis Parcels",
         ["APN", "UNITS", "SITUS", "YRBLT"])])
    monkeypatch.setattr(d, "search_socrata", lambda city, soda: [])
    monkeypatch.setattr(d, "named_for_other_city", lambda *a, **k: False)
    monkeypatch.setattr(d, "sample_in_city", lambda *a, **k: True)
    monkeypatch.setattr(d, "layer_record_count", lambda *a, **k: 400_000)
    out = d.discover(cities=[("Austin", "TX")], fetch=lambda *a, **k: {},
                     soda=lambda *a, **k: [])
    specs = out.get("Austin") or []
    assert specs, "should find at least one candidate"
    assert all(s["state"] == "TX" for s in specs), "state must be TX, not VA"


def test_vgin_fallback_only_runs_for_virginia(monkeypatch):
    d = _reload()
    calls = {"n": 0}
    monkeypatch.setattr(d, "vgin_fallback",
                        lambda city, fetch: calls.__setitem__("n", calls["n"] + 1) or None)
    # non-VA city with no real rolls -> vgin must NOT be consulted
    d.discover(cities=[("Dallas", "TX")], fetch=lambda *a, **k: {},
               soda=lambda *a, **k: [])
    assert calls["n"] == 0, "VGIN fallback must not run outside Virginia"
    # VA city with no real rolls -> vgin IS consulted
    d.discover(cities=[("Suffolk", "VA")], fetch=lambda *a, **k: {},
               soda=lambda *a, **k: [])
    assert calls["n"] == 1


def test_national_metro_list_is_broad_and_well_formed():
    d = _reload()
    assert len(d.TARGET_METROS) >= 40
    assert ("Atlanta", "GA") in d.TARGET_METROS
    assert all(isinstance(t, tuple) and len(t) == 2 for t in d.TARGET_METROS)
