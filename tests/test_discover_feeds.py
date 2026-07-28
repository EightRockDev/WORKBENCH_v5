"""Feed discovery for the uncovered HR cities (offline, stubbed portals)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.discover_feeds import MIN_SCORE, discover, score_fields, walk_root


def test_scoring_requires_a_parcel_id():
    score, mapped = score_fields(["LIVUNIT", "USECODE", "YRBLT"])
    assert score == 0                      # no APN -> no deterministic id
    score2, mapped2 = score_fields(["GPIN", "LIVUNIT", "USECODE", "YRBLT"])
    assert score2 >= MIN_SCORE
    assert mapped2["apn"] == "GPIN" and mapped2["units"] == "LIVUNIT"


def test_sales_only_layer_scores_below_threshold():
    """Virginia Beach's current sales layer must NOT qualify."""
    score, _ = score_fields(["GPIN", "Sale_Price", "Sale_Date", "Land_Value"])
    assert score < MIN_SCORE


def _fake_portal(url, params=None):
    """A one-folder ArcGIS directory with a parcels service (good fields)
    and a trails service (junk). Answers ONLY for the x.test root so the
    cities' real known roots stay silent in tests."""
    if not url.startswith("https://x.test"):
        return {}
    if url.endswith("/rest/services"):
        return {"folders": [], "services": [
            {"name": "Parcels", "type": "FeatureServer"},
            {"name": "Trails", "type": "FeatureServer"}]}
    if url.endswith("Parcels/FeatureServer"):
        return {"layers": [{"id": 0, "name": "Parcels"}]}
    if url.endswith("Parcels/FeatureServer/0"):
        return {"fields": [{"name": "GPIN"}, {"name": "LIVUNIT"},
                           {"name": "USECODE"}, {"name": "YRBLT"},
                           {"name": "SITUSADDRESS"}]}
    if url.endswith("Trails/FeatureServer"):
        return {"layers": [{"id": 0, "name": "Trails"}]}
    if url.endswith("Trails/FeatureServer/0"):
        return {"fields": [{"name": "TRAIL_NAME"}, {"name": "MILES"}]}
    return {}   # AGOL search etc. -> nothing


def test_walk_and_discover_pick_only_qualifying_layers():
    layers = list(walk_root("https://x.test/rest/services", fetch=_fake_portal))
    assert len(layers) == 2
    found = discover(cities=("Hampton",), fetch=_fake_portal)
    # Hampton's known root won't answer the fake, but extra path exercises AGOL
    found2 = discover(cities=("Virginia Beach",), fetch=_fake_portal)
    assert found == {"Hampton": []} or found["Hampton"] == []
    # Patch the known root through extra_roots instead:
    found3 = discover(cities=("Hampton",),
                      extra_roots=("https://x.test/rest/services",),
                      fetch=_fake_portal)
    specs = found3["Hampton"]
    assert len(specs) == 1
    assert specs[0]["url"].endswith("Parcels/FeatureServer/0")
    assert specs[0]["market"] == "Hampton"


def test_offline_portal_yields_empty_not_crash():
    found = discover(cities=("Suffolk",), fetch=lambda *a, **k: None)
    assert found == {"Suffolk": []}
