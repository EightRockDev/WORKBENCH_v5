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
UNIT_RICH_MIN = 50   # entities at 10+ units before a city counts as
                     # "carries unit data" for comp-pool evidence rules
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
    # Provider-style street-number RANGES ("700-780 Granby") key on the first
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
    comp_subjects_no_coords: int = 0   # 8R side blind (city awaits coords)
    comp_overlap_sum: float = 0.0
    comp_overlaps: list[float] = field(default_factory=list)
    rent_pairs: int = 0
    rent_delta_sum: float = 0.0
    # SIGNED delta, kept alongside the absolute one. The gate scores
    # abs(), which cannot tell a systematic bias from symmetric scatter -
    # and those two have opposite fixes. If the signed mean is close to
    # -31%, the estimator is uniformly LOW and a calibration factor closes
    # it. If the signed mean is near zero while abs() is 31%, the error is
    # scatter and NO factor helps; the answer is better per-property rent,
    # not better arithmetic. Free to carry, decides the next move.
    rent_signed_sum: float = 0.0
    worst_unit_mismatches: list[str] = field(default_factory=list)
    legacy_by_city: dict = field(default_factory=dict)
    matched_by_city: dict = field(default_factory=dict)
    spine_mf_by_city: dict = field(default_factory=dict)
    spine_mf_geo_by_city: dict = field(default_factory=dict)  # ...with lat/lng
    pool_label_only_excluded: dict = field(default_factory=dict)
    footprint_recovered: int = 0    # unit disagreements resolved by summing
                                    # all 8R parcels within the complex radius
    # (legacy_id, r8_id, match_method, parcel_count) - persisted to the
    # property_crosswalk table so P0-3 cutover can migrate deal references.
    crosswalk_records: list = field(default_factory=list)

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
    def avg_rent_signed(self) -> float | None:
        """Mean (estimate - actual)/actual. Negative = estimate runs low."""
        return ((self.rent_signed_sum / self.rent_pairs)
                if self.rent_pairs else None)

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
        if self.avg_rent_signed is not None:
            sg = self.avg_rent_signed
            # Name which failure mode this is, in the report itself.
            if abs(sg) > 0.6 * (self.avg_rent_delta or 1):
                verdict = ("systematic - the estimate runs "
                           + ("LOW" if sg < 0 else "HIGH")
                           + "; a calibration factor is the fix")
            else:
                verdict = ("scatter, not bias - a calibration factor cannot "
                           "close this; better per-property rent can")
            rent += f"  [signed {sg:+.1%}: {verdict}]" 
        unit_total = self.unit_agreement + self.unit_disagreement
        year_total = self.year_agreement + self.year_disagreement
        lines = [
            f"legacy multifamily rows:  {self.legacy_multifamily:,}",
            f"matched to the 8R backbone: {self.matched:,} ({self.match_rate:.1%})"
            f"  [{self.matched_by_address:,} by address, {self.matched_by_latlng:,}"
            f" by lat/lng, {self.matched_by_proximity:,} by proximity]",
            (f"unit counts agree:        {self.unit_agreement:,}/{unit_total:,}"
             if unit_total else "unit counts agree:        n/a"),
            (f"year built agrees:        {self.year_agreement:,}/{year_total:,}"
             if year_total else "year built agrees:        n/a"),
            "",
            f"comp-set replay subjects: {self.comp_subjects:,}"
            + (f"  (+{self.comp_subjects_no_coords:,} skipped - city has no"
               " 8R coordinates yet)" if self.comp_subjects_no_coords else ""),
            f"avg comp-set overlap:     {self.avg_comp_overlap:.1%}"
            f"  (gate >= {GATE_COMP_OVERLAP:.0%})",
            f"avg-rent delta:           {rent}  (gate <= {GATE_RENT_DELTA:.0%})",
            "",
            f"P0-2 GATE: {'PASSED - ready for P0-3 cutover' if self.gate_passed else 'not met yet'}",
            f"crosswalk persisted:      {len(self.crosswalk_records):,} "
            "legacy->8R mappings (property_crosswalk)",
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
        lines.append("By city (legacy rows -> matched | backbone MF entities | w/ coords):")
        for city in sorted(self.legacy_by_city, key=lambda c: -self.legacy_by_city[c]):
            n_leg = self.legacy_by_city.get(city, 0)
            n_match = self.matched_by_city.get(city, 0)
            n_mf = self.spine_mf_by_city.get(city, 0)
            n_geo = self.spine_mf_geo_by_city.get(city, 0)
            if not n_mf:
                note = "   <- feed has no usable multifamily data"
            elif not n_geo:
                note = "   <- no coordinates in feed (address match only; re-pull)"
            else:
                note = ""
            lines.append(f"  {city:15} {n_leg:5,} -> {n_match:5,} | {n_mf:6,} | {n_geo:6,}{note}")
        if self.pool_label_only_excluded:
            excl = ", ".join(f"{c} {n:,}" for c, n in
                             sorted(self.pool_label_only_excluded.items(),
                                    key=lambda kv: -kv[1]))
            lines.append("")
            lines.append("Comp pool: label-only entities excluded in unit-rich "
                         f"cities: {excl}")
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
                       lat, lng, use_code, est_avg_rent FROM properties_8r
                 WHERE city IN ({marks})""",
            cities).fetchall()
    except sqlite3.Error:
        # Backbone predating the rent-signal columns.
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
                # Sum units across the cluster (condo regimes = many 1-unit
                # parcels), but count a LARGE unit value once when it looks
                # like the SAME building seen through parallel feeds
                # (Chesapeake has four overlapping layers - summing made
                # Allure at Edinburgh 1,420 units against a legacy 280).
                # Evidence for "same building": identical count AND
                # coordinates within ~30m (or missing). Distinct phase
                # buildings with identical counts sit on separate parcels
                # with separate centroids, so they still sum.
                from core.phase0 import MIN_MF_UNITS
                total = 0
                seen_large: list[dict] = []
                for m in cluster:
                    u = int(m.get("units") or 0)
                    if u >= MIN_MF_UNITS:
                        dup = False
                        for prev in seen_large:
                            if int(prev.get("units") or 0) != u:
                                continue
                            plat, plng = prev.get("lat"), prev.get("lng")
                            mlat, mlng = m.get("lat"), m.get("lng")
                            if (plat is None or mlat is None
                                    or _distance_miles(mlat, mlng, plat, plng)
                                    <= 0.02):
                                dup = True
                                break
                        if dup:
                            continue
                        seen_large.append(m)
                    total += u
                head["units"] = total if total else head.get("units")
                # Rent estimate survives aggregation: any stamped member
                # supplies it (FMR is county-wide, so members agree).
                head["est_avg_rent"] = next(
                    (m.get("est_avg_rent") for m in cluster
                     if m.get("est_avg_rent")), head.get("est_avg_rent"))
                head["member_units"] = sorted(
                    (int(m.get("units") or 0) for m in cluster), reverse=True)
                # Anchor experiments concluded 2026-07-30 (centroid 66.9%
                # overlap, largest-parcel 66.4%, address-parcel 66.4%):
                # the centroid is the measured best - restored here.
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
    """Comp-pool membership - the shared >= 10-unit product rule
    (phase0.is_mf_ten_plus), so the P0-1 gate and this pool can never
    silently disagree. Known trade-off: a complex whose only unit signal is
    2-3 building-card rows can be excluded despite an apartment code;
    letting codes override known counts re-admits VB's 15.7K "Multi
    Family"-labeled duplexes, which is the bigger error class."""
    from core.phase0 import is_mf_ten_plus
    return is_mf_ten_plus(row.get("use_code"), row.get("units"))


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
        report.crosswalk_records.append(
            (row["property_id"], hit["property_id"], via,
             int(hit.get("parcel_count") or 1)))
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


def persist_crosswalk(spine_db: Path, records: list) -> int:
    """Materialize the legacy->8R id mapping (spec 7.3 P0-3: 'migrate deal
    references via crosswalk'). Until now the mapping was built in memory
    every parity run and thrown away - nothing downstream could use it.
    Full refresh each run; the match cascade is deterministic, so the
    table is stable run to run. Destroyed in P0-4 after the 30-day soak."""
    with sqlite3.connect(spine_db, timeout=60) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS property_crosswalk (
            legacy_property_id TEXT PRIMARY KEY,
            r8_property_id     TEXT NOT NULL,
            match_method       TEXT NOT NULL,
            parcel_count       INTEGER NOT NULL DEFAULT 1,
            built_at           TEXT NOT NULL
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS ix_crosswalk_r8
                        ON property_crosswalk (r8_property_id)""")
        conn.execute("DELETE FROM property_crosswalk")
        import datetime as _dt
        now = _dt.datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            "INSERT OR REPLACE INTO property_crosswalk VALUES (?,?,?,?,?)",
            [(leg, r8, via, n, now) for leg, r8, via, n in records])
        conn.commit()
    return len(records)


def _score_fields(legacy: dict, r8: dict, report: ParityReport) -> None:
    # Rent gate input: property recorded avg rent vs the backbone's estimate.
    # Only pairs where BOTH sides have a value count - no value, no vote.
    lr, rr = legacy.get("avg_rent"), r8.get("est_avg_rent")
    if lr and rr:
        report.rent_pairs += 1
        report.rent_delta_sum += abs(lr - rr) / lr
        report.rent_signed_sum += (rr - lr) / lr
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
    # The comp pools must span the SAME universe on both sides. The 8R
    # backbone knows ~3x more real complexes than the 639-row legacy set;
    # ranking nearest-12 against the full backbone pool crowds out the
    # crosswalked entities and punishes the backbone for BETTER coverage
    # (overlap sat at 14% with clean data). Restrict the 8R pool to
    # entities the legacy spine also knows.
    shared_ids = set(crosswalk.values())
    shared_pool = [e for e in mf_pool if e["property_id"] in shared_ids]
    subjects = sorted((r for r in legacy if r["property_id"] in crosswalk),
                      key=lambda r: (r.get("city") or "", r["property_id"]))
    subjects = subjects[:max_subjects]
    for subject in subjects:
        legacy_comps = _comp_set(subject, legacy, "latitude", "longitude")
        if not legacy_comps:
            continue
        r8_subject = r8_by_id.get(crosswalk[subject["property_id"]])
        if r8_subject is None:
            continue
        r8_comps = set(_comp_set(r8_subject, shared_pool, "lat", "lng"))
        translated = {crosswalk.get(pid) for pid in legacy_comps}
        translated.discard(None)
        if not translated:
            continue
        if not r8_comps:
            # The 8R side is blind here (subject or its whole city has no
            # coordinates yet - Norfolk). That is a COORDINATE gap, not a
            # comp-quality signal; report it separately instead of dragging
            # the average to zero.
            report.comp_subjects_no_coords += 1
            continue
        overlap = len(translated & r8_comps) / len(translated)
        report.comp_subjects += 1
        report.comp_overlap_sum += overlap
        report.comp_overlaps.append(overlap)


def run_parity(legacy_db: Path, spine_db: Path,
               cities: tuple[str, ...] | None = None,
               max_subjects: int = 200) -> ParityReport:
    """P0-2 end to end. `legacy_db` holds `properties`; `spine_db` holds
    `properties_8r` (they may be the same file)."""
    from core.market_data import HR_CITY_TO_COUNTY_FIPS_5
    cities = cities or tuple(HR_CITY_TO_COUNTY_FIPS_5)
    report = ParityReport()
    with sqlite3.connect(legacy_db) as conn:
        legacy = _load_legacy(conn, cities)
    with sqlite3.connect(spine_db) as conn:
        spine_8r = _load_8r(conn, cities)
    if not legacy or not spine_8r:
        return report
    # Per-complex entities, so a condo-fragmented community compares as one
    # property instead of dozens of 1-unit parcels.
    entities = aggregate_8r_parcels(spine_8r)
    crosswalk = match_spines(legacy, entities, report)
    # Materialize the mapping for P0-3 (deal-reference migration + the
    # cutover read path both consume this table).
    persist_crosswalk(spine_db, report.crosswalk_records)
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
                # Show WHAT summed into the entity - an overcount explains
                # itself when the member unit values are visible (Allure at
                # Edinburgh read 1,310 vs a legacy 280 with no clue why).
                members = (ent or {}).get("member_units") or []
                comp = (" [" + "+".join(str(m) for m in members[:10])
                        + ("+..." if len(members) > 10 else "") + "]"
                        if members else "")
                still_bad.append(
                    f"{row.get('name') or row.get('address')}: legacy {lu} "
                    f"vs 8R {shown}{comp}")
        if recovered:
            report.unit_agreement += recovered
            report.unit_disagreement -= recovered
            report.footprint_recovered = recovered
            report.worst_unit_mismatches = still_bad
    # The comp pool must be multifamily on BOTH sides - the legacy load is
    # already units>=10; replaying against every parcel in the county would
    # bury the true comps in single-family noise.
    # Comp-pool membership is EVIDENCE-AWARE per city. In a city whose feed
    # proves it carries unit counts (>= UNIT_RICH_MIN entities at 10+), a
    # row with an MF-ish label but NO units is presumed small - VB labels
    # 15,482 unit-less duplexes "Multi Family", and admitting them via the
    # label held comp overlap at 14%. In a city with no unit signal at all
    # (Norfolk-style rolls), the label is the only evidence and still counts.
    units_rich: dict[str, int] = {}
    for e in entities:
        u = e.get("units")
        if u is not None and u >= 10:
            city = e.get("city") or "?"
            units_rich[city] = units_rich.get(city, 0) + 1
    rich = {c for c, n in units_rich.items() if n >= UNIT_RICH_MIN}
    mf_entities = []
    for e in entities:
        if not _is_mf_entity(e):
            continue
        if (e.get("units") is None and (e.get("city") or "?") in rich):
            city = e.get("city") or "?"
            report.pool_label_only_excluded[city] = (
                report.pool_label_only_excluded.get(city, 0) + 1)
            continue
        mf_entities.append(e)
    for e in mf_entities:
        city = e.get("city") or "?"
        report.spine_mf_by_city[city] = report.spine_mf_by_city.get(city, 0) + 1
        if e.get("lat") is not None and e.get("lng") is not None:
            report.spine_mf_geo_by_city[city] = (
                report.spine_mf_geo_by_city.get(city, 0) + 1)
    replay_comps(legacy, entities, mf_entities, crosswalk, report, max_subjects)
    return report
