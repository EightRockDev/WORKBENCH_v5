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
