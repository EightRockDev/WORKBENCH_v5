"""Comparable property selection: haversine + Bucket 1 / Bucket 2.

Bucket 1: ≤3 mi from subject AND same class, max 8 comps.
Bucket 2: everything else within ≤5 mi (any class), max 4 comps.
Total cap: 12.

Conventions locked by Brian on 2026-05-06 (see memory file
`feedback_underwriting_conventions.md`). Defaults sourced from `config.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import config


@dataclass(frozen=True)
class Comp:
    """One comparable property, decorated with distance and bucket assignment."""
    property_id: str
    name: str
    city: str | None
    units: int | None
    year_built: int | None
    avg_rent: float | None
    avg_sqft: float | None
    rent_per_sqft: float | None
    asset_class: str | None
    manager: str | None
    owner: str | None
    latitude: float
    longitude: float
    distance_miles: float
    bucket: int  # 1 or 2


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles. Earth radius = 3958.8 mi.

    Standard haversine formula. Returns 0 for identical points.
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2.0) ** 2
    )
    # Clamp 'a' to [0, 1] to guard against floating-point overshoot
    a = min(max(a, 0.0), 1.0)
    c = 2.0 * math.asin(math.sqrt(a))
    return config.EARTH_RADIUS_MILES * c


# ---------------------------------------------------------------------------
# Comp selection
# ---------------------------------------------------------------------------

def _make_comp(rec: dict[str, Any], distance: float, bucket: int) -> Comp:
    """Build a Comp dataclass from a SQLite row dict, tolerating missing fields."""
    return Comp(
        property_id=str(rec.get("property_id", "")),
        name=str(rec.get("name", "")),
        city=rec.get("city"),
        units=rec.get("units"),
        year_built=rec.get("year_built"),
        avg_rent=rec.get("avg_rent"),
        avg_sqft=rec.get("avg_sqft"),
        rent_per_sqft=rec.get("rent_per_sqft"),
        asset_class=rec.get("asset_class"),
        manager=rec.get("manager"),
        owner=rec.get("owner"),
        latitude=float(rec["latitude"]),
        longitude=float(rec["longitude"]),
        distance_miles=distance,
        bucket=bucket,
    )


def get_comps(
    *,
    subject_id: str,
    subject_lat: float,
    subject_lon: float,
    subject_class: str | None,
    candidates: list[dict[str, Any]],
) -> list[Comp]:
    """Return up to 12 comps split across Bucket 1 (8 max) and Bucket 2 (4 max).

    Bucket 1: ≤3 mi from subject AND same asset class.
    Bucket 2: everything else within ≤5 mi (any class), filling the slots
              that Bucket 1 didn't claim.

    Filtering rules:
      - Subject is excluded (matched by `property_id`).
      - Candidates without lat/lng are skipped.
      - Candidates beyond the Bucket-2 radius are skipped.

    Output is sorted bucket-then-distance: all Bucket-1 comps (closest first)
    followed by all Bucket-2 comps (closest first).
    """
    # 1. Compute distance for every candidate that has coords + isn't the subject
    candidates_with_dist: list[tuple[float, dict[str, Any]]] = []
    for rec in candidates:
        if rec.get("property_id") == subject_id:
            continue
        lat = rec.get("latitude")
        lng = rec.get("longitude")
        if lat is None or lng is None:
            continue
        try:
            dist = haversine_miles(subject_lat, subject_lon, float(lat), float(lng))
        except (TypeError, ValueError):
            continue
        if dist > config.COMPS_BUCKET2_RADIUS_MILES:
            continue
        candidates_with_dist.append((dist, rec))

    # 2. Sort by distance ascending so we pick nearest first
    candidates_with_dist.sort(key=lambda t: t[0])

    # 3. Bucket 1 — within bucket-1 radius AND same class (when same-class required)
    bucket1: list[Comp] = []
    bucket1_ids: set[str] = set()
    require_same_class = config.COMPS_BUCKET1_REQUIRE_SAME_CLASS
    for dist, rec in candidates_with_dist:
        if len(bucket1) >= config.COMPS_BUCKET1_MAX:
            break
        if dist > config.COMPS_BUCKET1_RADIUS_MILES:
            continue
        if require_same_class and rec.get("asset_class") != subject_class:
            continue
        comp = _make_comp(rec, dist, bucket=1)
        bucket1.append(comp)
        bucket1_ids.add(comp.property_id)

    # 4. Bucket 2 — everything else within bucket-2 radius, not already in B1
    bucket2: list[Comp] = []
    for dist, rec in candidates_with_dist:
        if len(bucket2) >= config.COMPS_BUCKET2_MAX:
            break
        prop_id = rec.get("property_id")
        if prop_id in bucket1_ids:
            continue
        bucket2.append(_make_comp(rec, dist, bucket=2))

    return bucket1 + bucket2
