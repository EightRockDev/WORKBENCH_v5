"""Phase 0 step P0-2 (spec 7.3): shadow parity between the two spines.

Answers one question with numbers: does the self-sourced `properties_8r`
spine describe the same physical world as the legacy licensed `properties`
table well enough to cut over?

Three measurements, all deterministic:

  * **Parcel match** - every legacy multifamily row is matched to an 8R row
    by normalized address + city, falling back to lat/lng proximity
    (~120m). The match rate is the headline "did we find the same
    buildings" number.
  * **Field deltas** - on matched pairs: unit-count agreement (within 10% or
    2 units) and year-built agreement (within 2 years). Assessor and survey
    data legitimately disagree in small ways; big disagreement means a bad
    match or a bad feed.
  * **Comp-set replay** - the spec's gate. For a sample of legacy subjects,
    build the comp set (same radius/bucket logic as `core/comps.py`,
    distance + class + size) against BOTH spines and measure overlap by
    matched identity. Gate: overlap >= 90% average. Where both sides carry
    an average rent, the avg-rent delta is reported against the <=5% gate
    (the 8R spine grows rent signal via listings/HUD - absent values are
    reported, not failed).

Nothing here writes to either spine. The output is a report the cutover
decision (P0-3) is made on.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config

GATE_COMP_OVERLAP = 0.90     # spec 7.3 P0-2
GATE_RENT_DELTA = 0.05
MATCH_RADIUS_MILES = 0.075   # ~120 m - same parcel, different geocoders
PROXIMITY_RADIUS_MILES = 0.25  # last-resort: big complexes geocode far apart
UNIT_TOLERANCE_PCT = 0.10
UNIT_TOLERANCE_ABS = 2
YEAR_TOLERANCE = 2

_STREET_ABBREV = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
    "road": "rd", "court": "ct", "circle": "cir", "lane": "ln",
    "place": "pl", "parkway": "pkwy", "highway": "hwy", "terrace": "ter",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def normalize_address(address: str | None) -> str:
    """Lowercase, abbreviations collapsed, punctuation stripped - a stable
    join key across the two spines' address styles. A unit designator
    (apt/suite/unit/#) and everything after it is dropped: the PARCEL is the
    join key, not the individual unit."""
    text = (address or "").lower()
    # ALN-style street-number RANGES ("700-780 Granby") key on the first
    # number - the assessor's parcels start there.
    text = re.sub(r"^\s*(\d+)\s*-\s*\d+", r"\1", text)
    tokens = re.sub(r"[^a-z0-9# ]", " ", text).split()
    out: list[str] = []
    for t in tokens:
        if t in ("apt", "suite", "unit", "ste") or t.startswith("#"):
            break
        out.append(_STREET_ABBREV.get(t, t))
    return " ".join(t for t in out if t)


def _distance_miles(lat1, lng1, lat2, lng2) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (lat1, lng1, lat2, lng2))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * config.EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


@dataclass
class ParityReport:
    legacy_multifamily: int = 0
    matched: int = 0
    matched_by_address: int = 0
    matched_by_latlng: int = 0
    matched_by_proximity: int = 0
    unit_agreement: int = 0
    unit_disagreement: int = 0
    year_agreement: int = 0
    year_disagreement: int = 0
    comp_subjects: int = 0
    comp_overlap_sum: float = 0.0
    comp_overlaps: list[float] = field(default_factory=list)
    rent_pairs: int = 0
    rent_delta_sum: float = 0.0
    worst_unit_mismatches: list[str] = field(default_factory=list)
    legacy_by_city: dict = field(default_factory=dict)
    matched_by_city: dict = field(default_factory=dict)
    spine_mf_by_city: dict = field(default_factory=dict)
    footprint_recovered: int = 0    # unit disagreements resolved by summing
                                    # all 8R parcels within the complex radius

    @property
    def match_rate(self) -> float:
        return self.matched / self.legacy_multifamily if self.legacy_multifamily else 0.0

    @property
    def avg_comp_overlap(self) -> float:
        return (self.comp_overlap_sum / self.comp_subjects) if self.comp_subjects else 0.0

    @property
    def avg_rent_delta(self) -> float | None:
        return (self.rent_delta_sum / self.rent_pairs) if self.rent_pairs else None

    @property
    def gate_passed(self) -> bool:
        rent_ok = self.avg_rent_delta is None or self.avg_rent_delta <= GATE_RENT_DELTA
        return (self.comp_subjects > 0
                and self.avg_comp_overlap >= GATE_COMP_OVERLAP
                and rent_ok)

    @property
    def covered_match_rate(self) -> float | None:
        """Match rate restricted to cities where the spine actually has
        multifamily data - the number the parsing can influence. The blended
        rate stays the official gate; this one separates "parsing problem"
        from "feed missing"."""
        covered = [c for c, n in self.spine_mf_by_city.items() if n > 0]
        legacy = sum(self.legacy_by_city.get(c, 0) for c in covered)
        matched = sum(self.matched_by_city.get(c, 0) for c in covered)
        return (matched / legacy) if legacy else None

    def summary(self) -> str:
        rent = (f"{self.avg_rent_delta:.1%}" if self.avg_rent_delta is not None
                else "n/a (8R rent signal not populated yet)")
        unit_total = self.unit_agreement + self.unit_disagreement
        year_total = self.year_agreement + self.year_disagreement
        lines = [
            f"legacy multifamily rows:  {self.legacy_multifamily:,}",
            f"matched to the 8R spine:  {self.matched:,} ({self.match_rate:.1%})"
            f"  [{self.matched_by_address:,} by address, {self.matched_by_latlng:,}"
            f" by lat/lng, {self.matched_by_proximity:,} by proximity]",
            (f"unit counts agree:        {self.unit_agreement:,}/{unit_total:,}"
             if unit_total else "unit counts agree:        n/a"),
            (f"year built agrees:        {self.year_agreement:,}/{year_total:,}"
             if year_total else "year built agrees:        n/a"),
            "",
            f"comp-set replay subjects: {self.comp_subjects:,}",
            f"avg comp-set overlap:     {self.avg_comp_overlap:.1%}"
            f"  (gate >= {GATE_COMP_OVERLAP:.0%})",
            f"avg-rent delta:           {rent}  (gate <= {GATE_RENT_DELTA:.0%})",
            "",
            f"P0-2 GATE: {'PASSED - ready for P0-3 cutover' if self.gate_passed else 'not met yet'}",
        ]
        if self.footprint_recovered:
            lines.append(f"(+{self.footprint_recovered:,} unit disagreements "
                         "resolved by multi-parcel footprint totals)")
        if self.covered_match_rate is not None:
            lines.append(f"match rate, covered cities only: "
                         f"{self.covered_match_rate:.1%}  (cities whose feed "
                         "has multifamily data)")
        # Per-city truth: a city whose feed carries no unit data can never
        # match or replay - naming it turns a mystery into a to-do.
        lines.append("")
        lines.append("By city (legacy rows -> matched | spine MF entities):")
        for city in sorted(self.legacy_by_city, key=lambda c: -self.legacy_by_city[c]):
            n_leg = self.legacy_by_city.get(city, 0)
            n_match = self.matched_by_city.get(city, 0)
            n_mf = self.spine_mf_by_city.get(city, 0)
            note = "" if n_mf else "   <- feed has no usable multifamily data"
            lines.append(f"  {city:15} {n_leg:5,} -> {n_match:5,} | {n_mf:6,}{note}")
        if self.worst_unit_mismatches:
            lines.append("")
            lines.append("Largest unit-count disagreements (check these matches):")
            lines.extend(f"  {m}" for m in self.worst_unit_mismatches[:8])
        return "\n".join(lines)


def _load_legacy(conn: sqlite3.Connection, cities: tuple[str, ...]) -> list[dict]:
    marks = ",".join("?" for _ in cities)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""SELECT property_id, name, address, city, units, year_built,
                       avg_rent, asset_class, latitude, longitude
                  FROM properties
                 WHERE city IN ({marks}) AND units >= 10""",
            cities).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def _load_8r(conn: sqlite3.Connection, cities: tuple[str, ...]) -> list[dict]:
    marks = ",".join("?" for _ in cities)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""SELECT property_id, address, city, units, year_built,
                       lat, lng, use_code FROM properties_8r
                 WHERE city IN ({marks})""",
            cities).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def aggregate_8r_parcels(spine_8r: list[dict]) -> list[dict]:
    """Collapse per-parcel assessor rows into per-COMPLEX entities.

    A 258-unit community often sits on dozens of assessor parcels (condo
    regimes record every unit as its own 1-unit parcel at the same situs).
    The legacy spine is per-complex, so parity must compare like with like:
    rows sharing a normalized (address, city) merge into one entity whose
    units are SUMMED and whose coordinates are the centroid. The first
    parcel's 8R id represents the group.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    loose: list[dict] = []
    for row in spine_8r:
        addr = normalize_address(row.get("address"))
        # A PO box or blank situs is not a physical location - never a
        # grouping key (distinct properties often share the same junk value).
        if not addr or addr.startswith("po box"):
            loose.append(row)
            continue
        groups[(addr, (row.get("city") or "").lower())].append(row)

    out: list[dict] = []
    for members in groups.values():
        # Same address string but far-apart coordinates = different
        # properties mislabeled alike; split into proximity clusters
        # (~2x the match radius) before merging.
        clusters: list[list[dict]] = []
        for m in members:
            placed = False
            for cluster in clusters:
                anchor = cluster[0]
                if (m.get("lat") is None or anchor.get("lat") is None
                        or _distance_miles(m["lat"], m["lng"],
                                           anchor["lat"], anchor["lng"])
                        <= 2 * MATCH_RADIUS_MILES):
                    cluster.append(m)
                    placed = True
                    break
            if not placed:
                clusters.append([m])
        for cluster in clusters:
            head = dict(cluster[0])
            if len(cluster) > 1:
                units = [m.get("units") or 0 for m in cluster]
                head["units"] = int(sum(units)) if any(units) else head.get("units")
                lats = [m["lat"] for m in cluster if m.get("lat") is not None]
                lngs = [m["lng"] for m in cluster if m.get("lng") is not None]
                if lats and lngs:
                    head["lat"] = sum(lats) / len(lats)
                    head["lng"] = sum(lngs) / len(lngs)
                head["parcel_count"] = len(cluster)
            out.append(head)
    out.extend(loose)
    return out


def _is_mf_entity(row: dict) -> bool:
    from core.phase0 import MIN_MF_UNITS, is_multifamily
    return is_multifamily(row.get("use_code"), row.get("units")) or (
        (row.get("units") or 0) >= MIN_MF_UNITS)


def match_spines(legacy: list[dict], spine_8r: list[dict],
                 report: ParityReport) -> dict[str, str]:
    """legacy property_id -> 8R property_id. Address join first, then lat/lng."""
    by_addr: dict[tuple[str, str], dict] = {}
    for row in spine_8r:
        key = (normalize_address(row.get("address")), (row.get("city") or "").lower())
        if key[0]:
            by_addr.setdefault(key, row)

    with_coords = [r for r in spine_8r
                   if r.get("lat") is not None and r.get("lng") is not None]
    crosswalk: dict[str, str] = {}
    for row in legacy:
        report.legacy_multifamily += 1
        city = row.get("city") or "?"
        report.legacy_by_city[city] = report.legacy_by_city.get(city, 0) + 1
        key = (normalize_address(row.get("address")), (row.get("city") or "").lower())
        hit = by_addr.get(key) if key[0] else None
        via = "address"
        if hit is None and row.get("latitude") is not None and row.get("longitude") is not None:
            best, best_d = None, MATCH_RADIUS_MILES
            for cand in with_coords:
                d = _distance_miles(row["latitude"], row["longitude"],
                                    cand["lat"], cand["lng"])
                if d <= best_d:
                    best, best_d = cand, d
            hit, via = best, "latlng"
            if hit is None:
                # Last resort: a big complex's marketing pin and its parcel
                # centroid can sit hundreds of meters apart. Take the nearest
                # MULTIFAMILY entity within the wide radius - never a random
                # house.
                best, best_d = None, PROXIMITY_RADIUS_MILES
                for cand in with_coords:
                    if not _is_mf_entity(cand):
                        continue
                    d = _distance_miles(row["latitude"], row["longitude"],
                                        cand["lat"], cand["lng"])
                    if d <= best_d:
                        best, best_d = cand, d
                hit, via = best, "proximity"
        if hit is None:
            continue
        crosswalk[row["property_id"]] = hit["property_id"]
        report.matched += 1
        report.matched_by_city[city] = report.matched_by_city.get(city, 0) + 1
        if via == "address":
            report.matched_by_address += 1
        elif via == "latlng":
            report.matched_by_latlng += 1
        else:
            report.matched_by_proximity += 1
        _score_fields(row, hit, report)
    return crosswalk


def _score_fields(legacy: dict, r8: dict, report: ParityReport) -> None:
    lu, ru = legacy.get("units"), r8.get("units")
    if lu and ru:
        tolerance = max(UNIT_TOLERANCE_ABS, lu * UNIT_TOLERANCE_PCT)
        if abs(lu - ru) <= tolerance:
            report.unit_agreement += 1
        else:
            report.unit_disagreement += 1
            report.worst_unit_mismatches.append(
                f"{legacy.get('name') or legacy.get('address')}: legacy {lu} vs 8R {ru}")
    ly, ry = legacy.get("year_built"), r8.get("year_built")
    if ly and ry:
        if abs(ly - ry) <= YEAR_TOLERANCE:
            report.year_agreement += 1
        else:
            report.year_disagreement += 1




COMPLEX_RADIUS_MILES = 0.12   # ~200 m - the footprint of a garden community


def footprint_units(legacy_row: dict, spine_8r: list[dict],
                    radius: float = COMPLEX_RADIUS_MILES) -> int | None:
    """Total units of ALL 8R parcels within the complex radius of the legacy
    point. Large communities sit on many parcels with different street
    numbers (700/710/720 Acqua Dr) - address grouping alone cannot reassemble
    them, geography can."""
    lat, lng = legacy_row.get("latitude"), legacy_row.get("longitude")
    if lat is None or lng is None:
        return None
    total = 0
    for cand in spine_8r:
        c_lat, c_lng = cand.get("lat"), cand.get("lng")
        if c_lat is None or c_lng is None:
            continue
        if _distance_miles(lat, lng, c_lat, c_lng) <= radius:
            total += int(cand.get("units") or 0)
    return total or None


def _comp_set(subject: dict, pool: list[dict], lat_key: str, lng_key: str) -> list[str]:
    """Bucketed radius comp set mirroring core/comps.py distance logic."""
    s_lat, s_lng = subject.get(lat_key), subject.get(lng_key)
    if s_lat is None or s_lng is None:
        return []
    scored = []
    for cand in pool:
        if cand["property_id"] == subject["property_id"]:
            continue
        c_lat, c_lng = cand.get(lat_key), cand.get(lng_key)
        if c_lat is None or c_lng is None:
            continue
        d = _distance_miles(s_lat, s_lng, c_lat, c_lng)
        if d <= config.COMPS_BUCKET2_RADIUS_MILES:
            scored.append((d, cand["property_id"]))
    scored.sort()
    return [pid for _d, pid in scored[:config.COMPS_TOTAL_MAX]]


def replay_comps(legacy: list[dict], all_entities: list[dict],
                 mf_pool: list[dict], crosswalk: dict[str, str],
                 report: ParityReport, max_subjects: int = 50) -> None:
    """The 50-deal replay: comp sets from both spines, overlap by identity.

    `all_entities` is the full matched universe (subject lookup); `mf_pool`
    is the multifamily-only comp pool. A subject whose 8R match fell outside
    the multifamily pool (e.g. a Chesapeake parcel with no unit data) is
    skipped, never a KeyError.
    """
    r8_by_id = {r["property_id"]: r for r in all_entities}
    subjects = [r for r in legacy if r["property_id"] in crosswalk][:max_subjects]
    for subject in subjects:
        legacy_comps = _comp_set(subject, legacy, "latitude", "longitude")
        if not legacy_comps:
            continue
        r8_subject = r8_by_id.get(crosswalk[subject["property_id"]])
        if r8_subject is None:
            continue
        r8_comps = set(_comp_set(r8_subject, mf_pool, "lat", "lng"))
        translated = {crosswalk.get(pid) for pid in legacy_comps}
        translated.discard(None)
        if not translated:
            continue
        overlap = len(translated & r8_comps) / len(translated)
        report.comp_subjects += 1
        report.comp_overlap_sum += overlap
        report.comp_overlaps.append(overlap)


def run_parity(aln_db: Path, spine_db: Path,
               cities: tuple[str, ...] | None = None,
               max_subjects: int = 50) -> ParityReport:
    """P0-2 end to end. `aln_db` holds `properties`; `spine_db` holds
    `properties_8r` (they may be the same file)."""
    from core.market_data import HR_CITY_TO_COUNTY_FIPS_5
    cities = cities or tuple(HR_CITY_TO_COUNTY_FIPS_5)
    report = ParityReport()
    with sqlite3.connect(aln_db) as conn:
        legacy = _load_legacy(conn, cities)
    with sqlite3.connect(spine_db) as conn:
        spine_8r = _load_8r(conn, cities)
    if not legacy or not spine_8r:
        return report
    # Per-complex entities, so a condo-fragmented community compares as one
    # property instead of dozens of 1-unit parcels.
    entities = aggregate_8r_parcels(spine_8r)
    crosswalk = match_spines(legacy, entities, report)
    # Second-chance unit check: a matched complex whose entity units disagree
    # gets its TRUE footprint total (every parcel within ~200 m, any address).
    if report.unit_disagreement:
        entity_by_id = {e["property_id"]: e for e in entities}
        still_bad: list[str] = []
        recovered = 0
        for row in legacy:
            r8_id = crosswalk.get(row["property_id"])
            if r8_id is None:
                continue
            ent = entity_by_id.get(r8_id)
            lu, ru = row.get("units"), (ent or {}).get("units")
            if not lu or not ru:
                continue
            if abs(lu - ru) <= max(UNIT_TOLERANCE_ABS, lu * UNIT_TOLERANCE_PCT):
                continue
            tol = max(UNIT_TOLERANCE_ABS, lu * UNIT_TOLERANCE_PCT)
            fp_wide = footprint_units(row, spine_8r)
            fp_tight = footprint_units(row, spine_8r,
                                       radius=COMPLEX_RADIUS_MILES / 2)
            # Dense districts over-merge at the wide radius (neighboring
            # complexes double-counted); accept whichever radius agrees.
            if any(fp and abs(lu - fp) <= tol for fp in (fp_tight, fp_wide)):
                recovered += 1
            else:
                shown = fp_tight or fp_wide or ru
                still_bad.append(
                    f"{row.get('name') or row.get('address')}: legacy {lu} vs 8R {shown}")
        if recovered:
            report.unit_agreement += recovered
            report.unit_disagreement -= recovered
            report.footprint_recovered = recovered
            report.worst_unit_mismatches = still_bad
    # The comp pool must be multifamily on BOTH sides - the legacy load is
    # already units>=10; replaying against every parcel in the county would
    # bury the true comps in single-family noise.
    mf_entities = [e for e in entities if _is_mf_entity(e)]
    for e in mf_entities:
        city = e.get("city") or "?"
        report.spine_mf_by_city[city] = report.spine_mf_by_city.get(city, 0) + 1
    replay_comps(legacy, entities, mf_entities, crosswalk, report, max_subjects)
    return report
