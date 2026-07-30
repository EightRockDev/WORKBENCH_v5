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


def test_wrong_city_layer_is_rejected_by_geo_sample():
    """A 'Hampton' candidate whose records sit in Chesapeake must be dropped
    (the real discovery run surfaced exactly this)."""
    def fake(url, params=None):
        if not url.startswith("https://x.test"):
            return {}
        if url.endswith("/rest/services"):
            return {"folders": [], "services": [
                {"name": "Parcels", "type": "FeatureServer"}]}
        if url.endswith("Parcels/FeatureServer"):
            return {"layers": [{"id": 0, "name": "Parcels"}]}
        if url.endswith("Parcels/FeatureServer/0"):
            return {"fields": [{"name": "GPIN"}, {"name": "LIVUNIT"},
                               {"name": "USECODE"}]}
        if url.endswith("/query"):
            # Chesapeake coordinates, far south of Hampton's box
            return {"features": [
                {"geometry": {"x": -76.30, "y": 36.70}} for _ in range(5)]}
        return {}
    from scripts.discover_feeds import discover
    found = discover(cities=("Hampton",),
                     extra_roots=("https://x.test/rest/services",), fetch=fake)
    assert found["Hampton"] == []


def test_in_city_layer_passes_geo_sample_and_units_rank_first():
    def fake(url, params=None):
        if not url.startswith("https://x.test"):
            return {}
        if url.endswith("/rest/services"):
            return {"folders": [], "services": [
                {"name": "AddrPts", "type": "FeatureServer"},
                {"name": "ParcelsNoUnits", "type": "FeatureServer"}]}
        if url.endswith("FeatureServer"):
            return {"layers": [{"id": 0, "name": url.split("/")[-2]}]}
        if url.endswith("AddrPts/FeatureServer/0"):
            return {"fields": [{"name": "GPIN"}, {"name": "UNITS"},
                               {"name": "USECODE"}, {"name": "ADDRESS"}]}
        if url.endswith("ParcelsNoUnits/FeatureServer/0"):
            return {"fields": [{"name": "GPIN"}, {"name": "USECODE"},
                               {"name": "YRBLT"}, {"name": "ADDRESS"},
                               {"name": "OWNER"}, {"name": "TOTALVALUE"}]}
        if url.endswith("/query"):
            # Chesapeake box coordinates
            return {"features": [
                {"geometry": {"x": -76.28, "y": 36.72}} for _ in range(5)]}
        return {}
    from scripts.discover_feeds import discover
    found = discover(cities=("Chesapeake",),
                     extra_roots=("https://x.test/rest/services",), fetch=fake)
    specs = found["Chesapeake"]
    assert len(specs) == 2
    assert "AddrPts" in specs[0]["url"]        # unit-bearing layer ranks first


# ---------------------------------------------------------------------------
# Socrata discovery (Norfolk's GIS is not ArcGIS - the walk found nothing)
# ---------------------------------------------------------------------------

from scripts.discover_feeds import (  # noqa: E402
    search_socrata, socrata_sample_in_city)


def _fake_soda(url, params=None):
    if url.endswith("/api/catalog/v1"):
        if (params or {}).get("q") != "parcel":
            return {"results": []}
        return {"results": [
            {"resource": {"id": "abcd-1234", "name": "Real Estate Parcels",
                          "columns_field_name": ["gpin", "street_address",
                                                 "location", "use_code"],
                          "columns_datatype": ["Text", "Text", "Location",
                                               "Text"]},
             "metadata": {"domain": "data.norfolk.gov"}},
            {"resource": {"id": "zzzz-9999", "name": "Trails",
                          "columns_field_name": ["trail", "miles"],
                          "columns_datatype": ["Text", "Number"]},
             "metadata": {"domain": "data.norfolk.gov"}},
            {"resource": {"id": "nyc0-0000",
                          "name": "Property Valuation and Assessment Data",
                          "columns_field_name": ["gpin", "units", "use_code"],
                          "columns_datatype": ["Text", "Number", "Text"]},
             "metadata": {"domain": "data.cityofnewyork.us"}},
        ]}
    if "abcd-1234" in url:
        return [{"gpin": "123", "street_address": "500 Granby St",
                 "location": {"latitude": "36.86", "longitude": "-76.29"}}]
    return []


def test_search_socrata_yields_coordinate_bearing_datasets():
    out = list(search_socrata("Norfolk", soda=_fake_soda))
    urls = [u for u, *_ in out]
    assert any("abcd-1234" in u for u in urls)
    (res_url, name, cols, has_coords) = next(
        t for t in out if "abcd-1234" in t[0])
    assert has_coords is True
    assert "gpin" in cols


def test_socrata_sample_geo_verifies_against_bbox():
    ok = socrata_sample_in_city(
        "https://data.norfolk.gov/resource/abcd-1234.json", "Norfolk",
        soda=_fake_soda)
    assert ok is True
    # Same rows claimed for Hampton (bbox further north) must be rejected.
    bad = socrata_sample_in_city(
        "https://data.norfolk.gov/resource/abcd-1234.json", "Hampton",
        soda=_fake_soda)
    assert bad is False


def test_discover_emits_socrata_spec_for_norfolk():
    found = discover(cities=("Norfolk",), fetch=lambda u, p=None: {},
                     soda=_fake_soda)
    specs = found["Norfolk"]
    assert any(s["platform"] == "socrata" and "abcd-1234" in s["url"]
               for s in specs)
    assert all("zzzz-9999" not in s["url"] for s in specs)  # no-APN dataset


def test_federated_foreign_domain_datasets_are_rejected():
    """Socrata catalogs federate: Norfolk's search returned NYC's assessment
    roll, whose id then 404s on data.norfolk.gov. Foreign-domain results
    never become feeds."""
    out = list(search_socrata("Norfolk", soda=_fake_soda))
    assert all("nyc0-0000" not in u for u, *_ in out)
    assert any("abcd-1234" in u for u, *_ in out)
