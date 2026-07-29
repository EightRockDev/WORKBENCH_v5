"""ArcGIS geometry capture (P0-1 round 6).

The parity report proved the failure mode: pulls with returnGeometry=false
stored no coordinates, so cities whose layers carry no lat/lng ATTRIBUTE
(Portsmouth) could never lat/lng- or proximity-match - 0/45. The puller now
probes centroid -> geometry -> none on the first page and stamps geo_lat /
geo_lng onto each record; core.phase0 maps those at top alias priority and
converts stray Web Mercator meters.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etl_munidata as etl  # noqa: E402
from core import phase0  # noqa: E402


class _FakePuller(etl.ArcGISPuller):
    """ArcGISPuller with a scripted server: `modes` maps the geometry mode
    implied by the request params -> the response to return."""

    def __init__(self, responses):
        super().__init__("https://example.test/FeatureServer/0", page=100)
        self._responses = responses
        self.requests_seen = []

    def _meta(self):
        return {"maxRecordCount": 100, "objectIdField": "OBJECTID"}

    @staticmethod
    def _mode_of(params):
        if params.get("returnCentroid") == "true":
            return "centroid"
        if params.get("returnGeometry") == "true":
            return "geometry"
        return "none"

    def _get(self, params):
        mode = self._mode_of(params)
        self.requests_seen.append(mode)
        resp = self._responses[mode]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _feat(attrs, **extra):
    return {"attributes": attrs, **extra}


def test_centroid_mode_stamps_geo_fields():
    puller = _FakePuller({
        "centroid": {"features": [
            _feat({"GPIN": "1"}, centroid={"x": -76.3, "y": 36.8})]},
    })
    rows = list(puller.iter_records())
    assert rows == [{"GPIN": "1", "geo_lat": 36.8, "geo_lng": -76.3}]


def test_falls_back_to_geometry_when_no_centroid():
    puller = _FakePuller({
        "centroid": {"features": [_feat({"GPIN": "1"})]},  # no centroid key
        "geometry": {"features": [
            _feat({"GPIN": "1"}, geometry={"x": -76.31, "y": 36.81})]},
    })
    rows = list(puller.iter_records())
    assert rows[0]["geo_lng"] == -76.31
    assert puller.requests_seen[:2] == ["centroid", "geometry"]


def test_polygon_rings_average_to_centroid():
    ring = [[-76.30, 36.80], [-76.32, 36.80], [-76.32, 36.82], [-76.30, 36.82]]
    puller = _FakePuller({
        "centroid": {"features": [_feat({"GPIN": "1"})]},
        "geometry": {"features": [
            _feat({"GPIN": "1"}, geometry={"rings": [ring]})]},
    })
    (row,) = puller.iter_records()
    assert abs(row["geo_lng"] - (-76.31)) < 1e-9
    assert abs(row["geo_lat"] - 36.81) < 1e-9


def test_survives_servers_that_choke_on_geometry_params():
    # Old on-prem servers may error on returnCentroid AND returnGeometry
    # requests - the legacy no-geometry pull must still work.
    puller = _FakePuller({
        "centroid": RuntimeError("400"),
        "geometry": RuntimeError("400"),
        "none": {"features": [_feat({"GPIN": "7"})]},
    })
    rows = list(puller.iter_records())
    assert rows == [{"GPIN": "7"}]


def test_geo_fields_flow_into_spine_row_with_mercator_conversion():
    # A server that ignores outSR hands back Web Mercator meters; the spine
    # must land on real degrees.
    x = math.radians(-76.28) * 6378137
    y = 6378137 * math.log(math.tan(math.pi / 4 + math.radians(36.85) / 2))
    row = phase0.build_row("Portsmouth", "VA",
                           {"GPIN": "1234", "geo_lat": y, "geo_lng": x})
    assert row is not None
    assert abs(row.lat - 36.85) < 1e-6
    assert abs(row.lng - (-76.28)) < 1e-6


def test_state_plane_feet_are_dropped_not_poisoned():
    # Virginia state-plane FEET would "convert" to a longitude outside the
    # US - the row keeps its identity but carries no coordinate.
    row = phase0.build_row("Portsmouth", "VA",
                           {"GPIN": "1234", "geo_lat": 3_400_000,
                            "geo_lng": 12_100_000})
    assert row is not None
    assert row.lat is None and row.lng is None


# ---------------------------------------------------------------------------
# Transient-error retry (a 502 killed a VB pull at offset 48,000)
# ---------------------------------------------------------------------------

class _HTTPError(Exception):
    def __init__(self, status):
        self.response = type("R", (), {"status_code": status})()


class _FakeRequests:
    """Scripted stand-in for the requests module: pops one response per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        payload = item

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return payload
        return _Resp()


def test_get_retries_transient_5xx(monkeypatch):
    fake = _FakeRequests([_HTTPError(502), _HTTPError(503),
                          {"features": []}])
    monkeypatch.setattr(etl, "requests", fake)
    monkeypatch.setattr(etl.time, "sleep", lambda s: None)
    puller = etl.ArcGISPuller("https://example.test/FeatureServer/0")
    assert puller._get({"f": "json"}) == {"features": []}
    assert fake.calls == 3


def test_get_does_not_retry_4xx(monkeypatch):
    fake = _FakeRequests([_HTTPError(400), {"features": []}])
    monkeypatch.setattr(etl, "requests", fake)
    monkeypatch.setattr(etl.time, "sleep", lambda s: None)
    puller = etl.ArcGISPuller("https://example.test/FeatureServer/0")
    try:
        puller._get({"f": "json"})
        assert False, "4xx must raise immediately"
    except _HTTPError:
        pass
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# Cross-city layer names (VB's org serves Chesapeake_Norfolk_Streets_Parcels)
# ---------------------------------------------------------------------------

def test_layer_named_for_other_city_is_disqualified():
    url = ("https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/"
           "services/Chesapeake_Norfolk_Streets_Parcels/FeatureServer/1")
    assert etl.named_for_other_city(url, "Virginia Beach") is True
    # ...but the same layer claimed for Norfolk names Norfolk - allowed.
    assert etl.named_for_other_city(url, "Norfolk") is False
    # A city's own portal URL mentions itself - never a rejection.
    assert etl.named_for_other_city(
        "https://gis.cityofchesapeake.net/mapping/rest/services/Accela/"
        "Accela_base_map_pro/MapServer/0", "Chesapeake") is False
    # "Hampton Roads" is the region, not the city of Hampton.
    assert etl.named_for_other_city(
        "https://x.test/Hampton_Roads_Parcels/FeatureServer/0",
        "Norfolk") is False


def test_stale_extra_feeds_file_cannot_poison_a_pull(tmp_path, monkeypatch, capsys):
    import json as _json
    (tmp_path / "feeds_extra.json").write_text(_json.dumps([
        {"market": "Virginia Beach", "state": "VA", "county": "Virginia Beach",
         "kind": "assessor", "platform": "arcgis", "status": "live",
         "url": "https://x.test/Chesapeake_Norfolk_Streets_Parcels/FeatureServer/1"},
        {"market": "Portsmouth", "state": "VA", "county": "Portsmouth",
         "kind": "assessor", "platform": "arcgis", "status": "live",
         "url": "https://gis.portsmouthva.gov/arcgis/rest/services/Parcels/FeatureServer/3"},
    ]))
    monkeypatch.setattr(etl, "_DATA_DIR", tmp_path)
    out = etl._extra_feeds()
    assert [f.market for f in out] == ["Portsmouth"]
    assert "named for another city" in capsys.readouterr().out
