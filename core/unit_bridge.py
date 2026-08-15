"""Attach unit counts to backbone rows by LOCATION when ids will not join.

The problem this exists for (owner, 2026-08-15: "This has always been a
priority"): the backbone knows about 1.28M properties and can size only ~4,000
of them, because unit counts live on a different layer than the parcels they
describe and the two layers key on different id schemes. Richmond is the worst
case - 108,033 parcels, 140 unit counts, while a separate ownership layer holds
2,365 of them.

Three id-based routes are closed by evidence, in order:
  1. apn alias (the PTM_ID fix) - merged the workbook to VDEM, not to COR.
  2. address crosswalk - the 2026-08-15 review proved NO Richmond assessor
     source maps a usable address at all.
  3. brute-force field-vs-field scan - found no shared identifier once Esri
     row counters were excluded (its first run proposed OBJECTID, which is an
     auto-increment and would have attached units to unrelated parcels).

What both layers DO carry is geometry. Since the round-6 fix pulls WGS84
coordinates, a unit count and the parcel it belongs to can be matched by
standing in the same place. That is this module.

Two rules keep a spatial join from inventing data:
  * NEAREST ONLY, AND UNAMBIGUOUS. If two candidate parcels sit within the
    radius and are not clearly ranked, the row is SKIPPED, not guessed. A
    wrong unit count is worse than a missing one - it flows into rent per
    unit, price per unit and the comp set, and nothing downstream can tell it
    was fabricated.
  * NEVER OVERWRITE. A row that already has a unit count keeps it; assessor
    units outrank an inferred neighbour every time.

Dry-run by default. `apply=True` writes, and only then.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ~30 m. Parcel centroids for the SAME parcel published by two agencies差 by a
# few metres; a neighbouring building is typically 40 m+ away. Tightened from
# a first guess of 100 m after the distance histogram showed the real matches
# clustered under 10 m - see report_lines().
DEFAULT_RADIUS_M = 30.0

# Two candidates inside the radius are only usable if the nearest is clearly
# nearer than the runner-up. Otherwise we cannot tell which building the unit
# count belongs to.
AMBIGUITY_RATIO = 0.5

_M_PER_DEG_LAT = 111_320.0


@dataclass
class BridgeStats:
    market: str = ""
    targets: int = 0            # backbone rows missing units but having coords
    sources: int = 0            # source rows carrying BOTH units and coords
    matched: int = 0            # unambiguous matches within the radius
    ambiguous: int = 0          # two plausible parcels - skipped on purpose
    unmatched: int = 0          # no parcel within the radius
    applied: int = 0
    distances: list[float] = field(default_factory=list)

    def report_lines(self) -> list[str]:
        out = [
            f"market: {self.market}",
            f"  backbone rows needing units (with coords): {self.targets:,}",
            f"  source rows carrying units + coords:       {self.sources:,}",
            f"  matched (unambiguous, in radius):          {self.matched:,}",
            f"  skipped as ambiguous:                      {self.ambiguous:,}",
            f"  no parcel within radius:                   {self.unmatched:,}",
        ]
        if self.distances:
            d = sorted(self.distances)
            out.append(
                f"  match distance m: min={d[0]:.1f} "
                f"median={d[len(d) // 2]:.1f} max={d[-1]:.1f}")
            # If the median is metres, these are the same parcel seen twice.
            # If it is tens of metres, we are matching NEIGHBOURS - which is
            # the failure mode that silently poisons per-unit economics.
            if d[len(d) // 2] > 15:
                out.append("  !! median distance is high - these may be "
                           "NEIGHBOURING parcels, not the same one. Do not "
                           "apply until this is understood.")
        out.append(f"  applied: {self.applied:,}")
        return out


def _cell(lat: float, lng: float, size: float) -> tuple[int, int]:
    return (int(lat / size), int(lng / size))


def _metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Equirectangular approximation - exact enough at parcel scale."""
    mid = math.radians((lat1 + lat2) / 2.0)
    dx = (lng2 - lng1) * _M_PER_DEG_LAT * math.cos(mid)
    dy = (lat2 - lat1) * _M_PER_DEG_LAT
    return math.hypot(dx, dy)


def bridge_units(
    db_path: Path,
    market: str,
    *,
    radius_m: float = DEFAULT_RADIUS_M,
    apply: bool = False,
    source_like: str | None = None,
) -> BridgeStats:
    """Match unit counts onto backbone rows by proximity. Dry-run unless
    `apply`. Returns stats either way, so the decision is made on evidence."""
    from core import phase0

    st = BridgeStats(market=market)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        targets = conn.execute(
            "SELECT property_id, lat, lng FROM properties_8r "
            " WHERE COALESCE(r8_market, city) = ? "
            "   AND (units IS NULL OR units = 0) "
            "   AND lat IS NOT NULL AND lng IS NOT NULL", (market,)).fetchall()
        st.targets = len(targets)
        if not targets:
            return st

        size = radius_m / _M_PER_DEG_LAT
        grid: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
        for t in targets:
            grid.setdefault(_cell(t["lat"], t["lng"], size), []).append(
                (t["lat"], t["lng"], t["property_id"]))

        sql = ("SELECT record FROM muni_records WHERE market = ? "
               "AND kind LIKE 'assessor%'")
        params: list[Any] = [market]
        if source_like:
            sql += " AND source_url LIKE ?"
            params.append(source_like)

        updates: list[tuple[int, str]] = []
        for (rec,) in conn.execute(sql, params):
            raw = phase0._decode_muni_record(rec)
            if not isinstance(raw, dict):
                continue
            m = phase0.normalize_record(market, "VA", raw)
            units = m.get("units")
            lat, lng = m.get("lat"), m.get("lng")
            try:
                units = int(float(units)) if units is not None else None
                lat = float(lat) if lat is not None else None
                lng = float(lng) if lng is not None else None
            except (TypeError, ValueError):
                continue
            if not units or units <= 0 or lat is None or lng is None:
                continue
            st.sources += 1

            cx, cy = _cell(lat, lng, size)
            near: list[tuple[float, str]] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (tlat, tlng, pid) in grid.get((cx + dx, cy + dy), ()):
                        d = _metres(lat, lng, tlat, tlng)
                        if d <= radius_m:
                            near.append((d, pid))
            if not near:
                st.unmatched += 1
                continue
            near.sort()
            if len(near) > 1 and near[0][0] > near[1][0] * AMBIGUITY_RATIO:
                # Two parcels about equally close - we cannot tell which
                # building these units describe, so we decline to guess.
                st.ambiguous += 1
                continue
            st.matched += 1
            st.distances.append(near[0][0])
            updates.append((units, near[0][1]))

        if apply and updates:
            with conn:
                conn.executemany(
                    "UPDATE properties_8r SET units = ? "
                    " WHERE property_id = ? AND (units IS NULL OR units = 0)",
                    updates)
            st.applied = len(updates)
        return st
    finally:
        conn.close()
