"""Reunite one parcel that arrived twice under two id schemes.

Richmond is the case this exists for. Its feeds describe the same lots and
cannot be joined to each other:

  * rva.gov Public Data Set  - 76,976 parcels, assessed values, use codes,
    letter PINs ("C0010124002"), and NO unit counts.
  * VDEM/VGIN parcel layer   - 76,739 parcels, same letter PINs, no units.
  * COR_Parcel_Own (Esri)    - 32,907 parcels, numeric PINs ("405010001"),
    2,365 of which DO carry unit counts.

No attribute bridges the two id schemes, and (checked 2026-08-27) not one
Richmond source maps a usable street address, which kills the address
crosswalk that works elsewhere. What every feed carries is a coordinate.

So this is a DE-DUPLICATION, not a copy. The COR row and the letter-PIN
row are one building; both are already on the backbone as separate
properties. Copying the unit count from one to the other would leave two
multifamily entities two metres apart - inflating the property count and
putting a building's perfect twin in its own comp set. Instead the pair is
merged: the value-bearing row absorbs what only the COR row knows, and the
duplicate is removed. properties_8r is rebuilt from muni_records on every
spine build, so a merge is never destructive - the next cycle re-derives
both halves from the raw feeds and merges them again.

Matching a building by position is exactly as dangerous as it sounds: a
centroid a few metres off belongs to the lot NEXT DOOR, and a wrong unit
count becomes a comp, then an underwriting input, and nothing downstream
would question it. Five rules must ALL hold before two rows are treated as
one property:

  1. **Same city.** Positions are only compared inside one market.
  2. **Different id scheme.** The two APNs must have different SHAPES
     (``405010001`` is 9 digits, ``C0010124002`` is a letter and 10
     digits). Two rows from the same feed are two different lots by
     definition, however close they sit - this is what stops a marina
     handing its 92 slips to the house next door.
  3. **Within tolerance.** Beyond ``radius_m`` there is no match.
  4. **Mutually nearest.** Each must be the other's closest candidate.
  5. **Unambiguous.** If the runner-up is nearly as close, refuse. Stacked
     condo parcels share a centroid; a coin flip between them is not a
     match, and this module does not flip coins.

The matching itself is pure and coordinate-only, so it can be tested
exactly as it runs.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

# Two surveys of the same lot put its centroid metres apart, not tens of
# metres. Generous for that, still well inside a city block.
DEFAULT_RADIUS_M = float(os.environ.get("ER_BRIDGE_RADIUS_M", "25"))

# The runner-up must be clearly further away than the winner: BOTH a
# ratio and an absolute margin, because either alone fails at one end of
# the scale. A pure ratio calls 0.4 m vs 0.5 m ambiguous (survey noise,
# not a decision) and a pure margin calls 12 m vs 20 m unambiguous.
DEFAULT_AMBIGUITY_RATIO = float(
    os.environ.get("ER_BRIDGE_AMBIGUITY_RATIO", "2.0"))
AMBIGUITY_MARGIN_M = float(os.environ.get("ER_BRIDGE_AMBIGUITY_MARGIN_M", "4"))

# Metres per degree. Latitude is near-constant; longitude shrinks with
# latitude, which is the whole reason the grid needs two cell sizes.
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LNG_EQUATOR = 111_320.0

_EARTH_R = 6_371_000.0

# Fields the surviving row absorbs from its duplicate, when it lacks them.
MERGE_FIELDS = ("units", "owner_name", "owner_address", "year_built",
                "sqft", "use_code", "assessed_value", "zip", "address")


@dataclass(frozen=True)
class GeoPoint:
    key: str
    lat: float
    lng: float
    units: int | None = None
    city: str = ""
    apn: str = ""


@dataclass(frozen=True)
class Match:
    source_key: str     # the row holding the unit count (removed on merge)
    target_key: str     # the row that survives and absorbs it
    units: int
    metres: float

    def __str__(self) -> str:
        return (f"{self.source_key} -> {self.target_key}: "
                f"{self.units} units ({self.metres:.1f} m)")


@dataclass
class BridgeReport:
    sources: int = 0
    targets: int = 0
    matched: int = 0
    rejected_far: int = 0
    rejected_not_mutual: int = 0
    rejected_ambiguous: int = 0
    rejected_same_scheme: int = 0
    merged_by_address: int = 0
    rejected_addr_ambiguous: int = 0
    rejected_addr_conflict: int = 0
    by_city: dict = field(default_factory=dict)   # city -> its own report

    def lines(self) -> list[str]:
        return [
            f"unit-bearing parcels   : {self.sources:,}",
            f"candidate parcels      : {self.targets:,}",
            f"merged                 : {self.matched:,}",
            f"rejected (too far)     : {self.rejected_far:,}",
            f"rejected (same id form): {self.rejected_same_scheme:,}",
            f"rejected (not mutual)  : {self.rejected_not_mutual:,}",
            f"rejected (ambiguous)   : {self.rejected_ambiguous:,}",
            f"merged by address      : {self.merged_by_address:,}",
            f"addr rejected (shared) : {self.rejected_addr_ambiguous:,}",
            f"addr rejected (coords) : {self.rejected_addr_conflict:,}",
        ]

    def city_lines(self) -> list[str]:
        """One line per city: what it had, what it got, what refused it."""
        out = []
        for city, r in sorted(self.by_city.items(),
                              key=lambda kv: -kv[1].matched):
            out.append(
                f"{city:<16} sources {r.sources:>7,}  targets {r.targets:>7,}"
                f"  merged {r.matched:>5,}   refused: far {r.rejected_far:,}"
                f" / same-id {r.rejected_same_scheme:,}"
                f" / not-mutual {r.rejected_not_mutual:,}"
                f" / ambiguous {r.rejected_ambiguous:,}"
                f"   by-address {r.merged_by_address:,}"
                f" (shared {r.rejected_addr_ambiguous:,}"
                f", coords {r.rejected_addr_conflict:,})")
        return out


def metres_between(a_lat: float, a_lng: float,
                   b_lat: float, b_lng: float) -> float:
    """Equirectangular distance - exact enough at parcel scale, and cheap
    (this runs millions of times against a 108k-parcel city)."""
    lat1, lat2 = math.radians(a_lat), math.radians(b_lat)
    x = math.radians(b_lng - a_lng) * math.cos((lat1 + lat2) / 2.0)
    y = lat2 - lat1
    return math.hypot(x, y) * _EARTH_R


_RUNS = re.compile(r"(\d+|[A-Za-z]+)")


def apn_shape(apn: str | None) -> str:
    """A parcel id's SHAPE, ignoring its actual digits.

    ``405010001`` -> ``9d``; ``C0010124002`` -> ``1a10d``. Two ids of the
    same shape came from the same feed's numbering scheme, so the rows are
    two different lots and must never be merged, however close they are.
    """
    s = str(apn or "").strip()
    if not s:
        return ""
    out = []
    for run in _RUNS.findall(s):
        out.append(f"{len(run)}{'d' if run[0].isdigit() else 'a'}")
    return "".join(out)


def _cell_sizes(points: list[GeoPoint], radius_m: float) -> tuple[float, float]:
    """Cell size in degrees per axis, each spanning AT LEAST radius_m.

    A single degree-based cell size (the 2026-08-31 bug) makes cells
    ~19.8 m wide at Richmond's latitude for a 25 m radius, so the 3x3
    neighbourhood silently misses candidates that are well inside the
    radius - which does not merely lose matches, it hides the runner-up
    that the ambiguity rule exists to catch, and reports the survivor as
    unambiguous. Size the longitude cell at the WORST latitude present so
    the guarantee holds for every point in the set.
    """
    lat_cell = radius_m / M_PER_DEG_LAT
    worst = max((abs(p.lat) for p in points), default=0.0)
    worst = min(worst, 89.0)
    m_per_deg_lng = M_PER_DEG_LNG_EQUATOR * math.cos(math.radians(worst))
    lng_cell = radius_m / max(m_per_deg_lng, 1.0)
    return lat_cell, lng_cell


def _index(points: list[GeoPoint], lat_cell: float, lng_cell: float) -> dict:
    grid: dict[tuple[int, int], list[GeoPoint]] = defaultdict(list)
    for p in points:
        grid[(math.floor(p.lat / lat_cell),
              math.floor(p.lng / lng_cell))].append(p)
    return grid


def _nearby(grid: dict, lat_cell: float, lng_cell: float,
            lat: float, lng: float) -> list[GeoPoint]:
    ci, cj = math.floor(lat / lat_cell), math.floor(lng / lng_cell)
    out: list[GeoPoint] = []
    for i in (ci - 1, ci, ci + 1):
        for j in (cj - 1, cj, cj + 1):
            out.extend(grid.get((i, j), ()))
    return out


def _eligible(a: GeoPoint, b: GeoPoint) -> bool:
    """Rules 1 and 2: same city, different parcel-id scheme."""
    if (a.city or "").lower() != (b.city or "").lower():
        return False
    sa, sb = apn_shape(a.apn), apn_shape(b.apn)
    return bool(sa) and bool(sb) and sa != sb


def _two_nearest(point: GeoPoint, candidates: list[GeoPoint],
                 radius_m: float) -> tuple[tuple[GeoPoint, float] | None,
                                           float | None]:
    """(nearest, distance) and the runner-up distance, over ELIGIBLE
    candidates only. Ties break on the key so the result never depends on
    the order sqlite handed the rows over."""
    best: tuple[GeoPoint, float] | None = None
    second: float | None = None
    for c in candidates:
        if c.key == point.key or not _eligible(point, c):
            continue
        d = metres_between(point.lat, point.lng, c.lat, c.lng)
        if d > radius_m:
            continue
        if best is None or d < best[1] or (d == best[1] and c.key < best[0].key):
            if best is not None and (second is None or best[1] < second):
                second = best[1]
            best = (c, d)
        elif second is None or d < second:
            second = d
    return best, second


def _ambiguous(nearest_m: float, runner_up_m: float | None) -> bool:
    if runner_up_m is None:
        return False
    return (runner_up_m < nearest_m * DEFAULT_AMBIGUITY_RATIO
            or runner_up_m - nearest_m < AMBIGUITY_MARGIN_M)


def bridge_units(sources: list[GeoPoint], targets: list[GeoPoint], *,
                 radius_m: float = DEFAULT_RADIUS_M,
                 ) -> tuple[list[Match], BridgeReport]:
    """Pair unit-bearing parcels with their twin under the other id scheme.

    ``sources`` hold unit counts; ``targets`` are the rows missing them.
    Only pairs passing all five rules are returned.
    """
    report = BridgeReport()
    usable_sources = [s for s in sources
                      if s.units and s.lat is not None and s.lng is not None]
    usable_targets = [t for t in targets
                      if t.lat is not None and t.lng is not None]
    report.sources = len(usable_sources)
    report.targets = len(usable_targets)
    if not usable_sources or not usable_targets:
        return [], report

    lat_cell, lng_cell = _cell_sizes(usable_sources + usable_targets, radius_m)
    target_grid = _index(usable_targets, lat_cell, lng_cell)
    source_grid = _index(usable_sources, lat_cell, lng_cell)

    matches: list[Match] = []
    claimed: dict[str, tuple[str, float]] = {}

    for s in sorted(usable_sources, key=lambda p: p.key):
        pool = _nearby(target_grid, lat_cell, lng_cell, s.lat, s.lng)
        if pool and not any(_eligible(s, c) for c in pool):
            report.rejected_same_scheme += 1
            continue
        best, second = _two_nearest(s, pool, radius_m)
        if best is None:
            report.rejected_far += 1
            continue
        target, d = best

        if _ambiguous(d, second):
            report.rejected_ambiguous += 1
            continue

        back, _ = _two_nearest(
            target, _nearby(source_grid, lat_cell, lng_cell,
                            target.lat, target.lng), radius_m)
        if back is None or back[0].key != s.key:
            report.rejected_not_mutual += 1
            continue

        prior = claimed.get(target.key)
        if prior is not None:
            if prior[1] <= d:
                report.rejected_not_mutual += 1
                continue
            matches = [m for m in matches if m.target_key != target.key]
            report.matched -= 1
        claimed[target.key] = (s.key, d)
        matches.append(Match(s.key, target.key, int(s.units), d))
        report.matched += 1

    matches.sort(key=lambda m: (m.target_key, m.source_key))
    return matches, report


# ---------------------------------------------------------------------------
# Backbone glue
# ---------------------------------------------------------------------------

def merge_duplicate_parcels(conn) -> tuple[int, BridgeReport, dict[str, int]]:
    """Merge each unit-bearing parcel into its twin under the other scheme.

    The surviving row is the one that was MISSING units - in Richmond that
    is the rva.gov row carrying the assessed value and use code, which is
    the record the rest of the workbench already reasons about. It absorbs
    every field it lacks from its duplicate, and the duplicate is deleted
    so one building is one property.

    Runs CITY BY CITY, and only for cities that actually have both halves.
    Loading the whole backbone at once would mean ~2.3M point objects in
    memory on the owner's office box for the sake of one city's problem;
    per-city keeps the working set to Richmond's ~108k rows and gives each
    grid a tighter latitude span as a bonus.

    Returns (rows_merged, report, per-city counts).
    """
    if os.environ.get("ER_NO_GEO_BRIDGE") == "1":
        return 0, BridgeReport(), {}

    # No lat requirement HERE: the address pass exists precisely for
    # unit-bearing feeds with no geometry (Richmond COR, coords=0 on all
    # 32,907 rows). Requiring lat in the city gate re-created the very
    # blindness being fixed - Richmond had 0 eligible sources and never
    # entered the loop.
    cities = [r[0] for r in conn.execute(
        "SELECT city FROM properties_8r "
        " WHERE city IS NOT NULL AND city <> '' "
        "   AND apn IS NOT NULL AND apn <> '' "
        " GROUP BY city "
        "HAVING sum(CASE WHEN units > 0 THEN 1 ELSE 0 END) > 0 "
        "   AND sum(CASE WHEN units IS NULL THEN 1 ELSE 0 END) > 0")]

    total = BridgeReport()
    merged = 0
    per_city: dict[str, int] = defaultdict(int)
    cols = ", ".join(MERGE_FIELDS)
    # Per-city, because a global total cannot answer the only question
    # worth asking after a run: "why did the city this was built for get
    # nothing?" (Richmond merged 0 while Atlanta merged 92, 2026-09-01).
    total.by_city = {}

    for city in cities:
        def _points(where: str) -> list[GeoPoint]:
            return [GeoPoint(str(r[0]), r[1], r[2],
                             int(r[3]) if r[3] is not None else None,
                             r[4] or "", r[5] or "")
                    for r in conn.execute(
                        "SELECT property_id, lat, lng, units, city, apn "
                        "  FROM properties_8r "
                        f" WHERE city = ? AND {where} "
                        "   AND lat IS NOT NULL AND lng IS NOT NULL "
                        "   AND apn IS NOT NULL AND apn <> ''", (city,))]

        addr_merged = _merge_by_address(conn, city, cols)
        report_addr_merged = addr_merged[0]
        merged += report_addr_merged
        if report_addr_merged:          # a bare += 0 still creates the key,
            per_city[city] += report_addr_merged   # polluting by-city output

        matches, report = bridge_units(_points("units IS NOT NULL AND units > 0"),
                                       _points("units IS NULL"))
        report.merged_by_address = report_addr_merged
        report.rejected_addr_ambiguous = addr_merged[1]
        report.rejected_addr_conflict = addr_merged[2]
        total.merged_by_address += report_addr_merged
        total.rejected_addr_ambiguous += addr_merged[1]
        total.rejected_addr_conflict += addr_merged[2]
        total.sources += report.sources
        total.targets += report.targets
        total.matched += report.matched
        total.rejected_far += report.rejected_far
        total.rejected_not_mutual += report.rejected_not_mutual
        total.rejected_ambiguous += report.rejected_ambiguous
        total.rejected_same_scheme += report.rejected_same_scheme
        total.by_city[city] = report

        for m in matches:
            src = conn.execute(
                f"SELECT {cols} FROM properties_8r WHERE property_id = ?",
                (m.source_key,)).fetchone()
            tgt = conn.execute(
                f"SELECT {cols} FROM properties_8r WHERE property_id = ?",
                (m.target_key,)).fetchone()
            if src is None or tgt is None:
                continue
            sets, vals = [], []
            for i, field_name in enumerate(MERGE_FIELDS):
                if tgt[i] is None and src[i] is not None:
                    sets.append(f"{field_name} = ?")
                    vals.append(src[i])
            if not sets:
                continue
            conn.execute(
                f"UPDATE properties_8r SET {', '.join(sets)} "
                " WHERE property_id = ?", (*vals, m.target_key))
            conn.execute("DELETE FROM properties_8r WHERE property_id = ?",
                         (m.source_key,))
            merged += 1
            per_city[city] += 1

    return merged, total, dict(per_city)


# ---------------------------------------------------------------------------
# Address-equality merge - for unit-bearing feeds that carry NO geometry
# ---------------------------------------------------------------------------

# Two rows claiming the same situs address can still be different lots if
# both carry coordinates that disagree wildly (a data-entry collision).
ADDR_COORD_CONFLICT_M = 150.0


def _merge_by_address(conn, city: str, cols: str) -> tuple[int, int, int]:
    """Merge unit-bearing rows onto their twin by EXACT situs address.

    Richmond's COR ownership table is a geometry-less table (coords=0 on
    every row, 2026-09-01), so the position bridge can never reach it. But
    it carries the situs street address, and so does the rva.gov workbook
    once PARCEL_LOCATION is aliased. An exact normalized-address equality
    is stronger identity evidence than proximity - buildings do not share
    a street number - PROVIDED it is one-to-one both ways:

      * the address maps to exactly ONE unit-bearing row and exactly ONE
        unit-less row in the city (a shared address on either side is a
        condo stack or a data smear - refuse);
      * the apn shapes differ (same-feed rows are different lots);
      * if BOTH rows do carry coordinates, they must not contradict the
        claimed identity by more than ADDR_COORD_CONFLICT_M.

    Returns (merged, rejected_shared_address, rejected_coord_conflict).
    """
    from core.phase0_parity import normalize_address

    def _rows(where: str):
        return conn.execute(
            "SELECT property_id, address, apn, lat, lng, units "
            "  FROM properties_8r "
            f" WHERE city = ? AND {where} "
            "   AND address IS NOT NULL AND address <> '' "
            "   AND apn IS NOT NULL AND apn <> ''", (city,)).fetchall()

    def _by_addr(rows):
        table: dict[str, list] = {}
        for r in rows:
            a = normalize_address(str(r[1]))
            # Require a street number: "MAIN ST" alone matches half a city.
            if not a or not any(ch.isdigit() for ch in a):
                continue
            table.setdefault(a, []).append(r)
        return table

    sources = _by_addr(_rows("units IS NOT NULL AND units > 0"))
    targets = _by_addr(_rows("units IS NULL"))

    merged = shared = conflict = 0
    for addr, srcs in sorted(sources.items()):
        tgts = targets.get(addr)
        if not tgts:
            continue
        if len(srcs) > 1 or len(tgts) > 1:
            shared += 1
            continue
        (s_id, _sa, s_apn, s_lat, s_lng, _su) = srcs[0]
        (t_id, _ta, t_apn, t_lat, t_lng, _tu) = tgts[0]
        sa, ta = apn_shape(s_apn), apn_shape(t_apn)
        if not sa or not ta or sa == ta:
            continue
        if (s_lat is not None and s_lng is not None
                and t_lat is not None and t_lng is not None
                and metres_between(s_lat, s_lng, t_lat, t_lng)
                > ADDR_COORD_CONFLICT_M):
            conflict += 1
            continue
        src = conn.execute(
            f"SELECT {cols} FROM properties_8r WHERE property_id = ?",
            (s_id,)).fetchone()
        tgt = conn.execute(
            f"SELECT {cols} FROM properties_8r WHERE property_id = ?",
            (t_id,)).fetchone()
        if src is None or tgt is None:
            continue
        sets, vals = [], []
        for i, field_name in enumerate(MERGE_FIELDS):
            if tgt[i] is None and src[i] is not None:
                sets.append(f"{field_name} = ?")
                vals.append(src[i])
        if sets:
            conn.execute(
                f"UPDATE properties_8r SET {', '.join(sets)} "
                " WHERE property_id = ?", (*vals, t_id))
        conn.execute("DELETE FROM properties_8r WHERE property_id = ?",
                     (s_id,))
        merged += 1
    return merged, shared, conflict
