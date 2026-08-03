"""User-added properties and the verified badge (spec §16).

The backbone will never be complete — new construction, conversions, the
sub-10-unit tail. A user can add a property in under a minute and use it
immediately; what they cannot do is make it LOOK confirmed. The blue check is
earned by validating the core data elements against records the user does not
control (the municipal assessor roll behind ``properties_8r``), and the
evidence for every decision is stored and shown (AC-16.3).

The bar (§16.2): address AND parcel exact, units within ±10%. The community
name is soft — municipal rolls do not carry marketing names, so a mismatch
annotates rather than fails.

Validation strength is a property of the MUNICIPALITY (§16.3): a city whose
roll we hold can verify or refute; a city with no feed (Suffolk today) can do
neither, so its submissions park as Pending and the nightly re-validation
picks them up when the city's data lands. Pending is not a failure state and
never becomes Verified by waiting.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from core import spine
from core.market_data import CITY_TO_COUNTY_FIPS_5

UNVERIFIED = "unverified"
PENDING = "pending"
VERIFIED = "verified"
FAILED = "failed"

UNIT_TOLERANCE = 0.10          # §16.2 — assessor unit counts are imperfect

# Address normalization for matching a user's typing against an assessor
# roll. Mirrors the inventory cross-ref rules: lowercase, strip punctuation,
# expand the abbreviations both sides actually use.
_ABBREV = {
    "ave": "avenue", "av": "avenue", "st": "street", "rd": "road",
    "blvd": "boulevard", "dr": "drive", "ln": "lane", "ct": "court",
    "cir": "circle", "pl": "place", "pkwy": "parkway", "ter": "terrace",
    "hwy": "highway", "sq": "square", "trl": "trail",
    "n": "north", "s": "south", "e": "east", "w": "west",
}


def norm_addr(addr: str | None) -> str:
    if not addr:
        return ""
    s = re.sub(r"[^\w\s]", " ", str(addr).lower())
    return " ".join(_ABBREV.get(t, t) for t in s.split())


def _norm_name(name: str | None) -> set[str]:
    """Marketing names compare as token sets ('The Landings at East Beach'
    vs 'Landings East Beach Apartments')."""
    stop = {"the", "at", "of", "apartments", "apartment", "apts", "homes",
            "community", "residences"}
    return {t for t in re.sub(r"[^\w\s]", " ", (name or "").lower()).split()
            if t and t not in stop}


_SCHEMA = """CREATE TABLE IF NOT EXISTS user_properties (
    user_property_id TEXT PRIMARY KEY,
    org_id           TEXT,
    name             TEXT NOT NULL,
    address          TEXT NOT NULL,
    city             TEXT NOT NULL,
    units            INTEGER NOT NULL,
    parcel_id        TEXT,
    website          TEXT,
    status           TEXT NOT NULL DEFAULT 'unverified',
    evidence         TEXT,
    matched_8r_id    TEXT,
    submitted_at     TEXT NOT NULL,
    validated_at     TEXT
)"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def _fips_for(city: str) -> str:
    # Metro expansion (§15) registers more cities here; an unknown city
    # still gets a stable id under the placeholder FIPS.
    return CITY_TO_COUNTY_FIPS_5.get(city, "00000")


def user_property_id(city: str, address: str, name: str) -> str:
    """``8R-{FIPS}-u{hash}`` (§16.1) — deterministic, so the same submission
    twice is the same row, not a duplicate."""
    payload = f"{city.lower()}|{norm_addr(address)}|{(name or '').lower()}"
    h = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"8R-{_fips_for(city)}-u{h}"


@dataclass
class VerificationResult:
    status: str
    reason: str = ""
    checks: dict = field(default_factory=dict)
    matched_8r_id: str | None = None

    def to_json(self) -> str:
        return json.dumps({"status": self.status, "reason": self.reason,
                           "checks": self.checks,
                           "matched_8r_id": self.matched_8r_id})


def submit_property(*, name: str, address: str, city: str, units: int,
                    parcel_id: str | None = None, website: str | None = None,
                    org_id: str | None = None,
                    db_path: Path | str) -> dict:
    """Create (or refresh) a user-added property. Returns the stored row.

    Idempotent on (city, address, name): resubmitting updates the mutable
    fields and resets a FAILED submission to unverified so the fix gets
    re-validated — it never mints a duplicate id (AC-16.1).
    """
    if not (name and address and city):
        raise ValueError("name, address and city are required")
    units = int(units)
    if units <= 0:
        raise ValueError("units must be a positive integer")
    pid = user_property_id(city, address, name)
    now = dt.datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        conn.execute(
            """INSERT INTO user_properties (user_property_id, org_id, name,
                    address, city, units, parcel_id, website, status,
                    submitted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_property_id) DO UPDATE SET
                    units=excluded.units, parcel_id=excluded.parcel_id,
                    website=excluded.website, status='unverified',
                    evidence=NULL, validated_at=NULL""",
            (pid, org_id, name, address, city, units, parcel_id, website,
             UNVERIFIED, now))
        row = conn.execute("SELECT * FROM user_properties WHERE "
                           "user_property_id=?", (pid,)).fetchone()
    cols = ("user_property_id", "org_id", "name", "address", "city", "units",
            "parcel_id", "website", "status", "evidence", "matched_8r_id",
            "submitted_at", "validated_at")
    return dict(zip(cols, row))


def city_capability(conn: sqlite3.Connection, city: str) -> str:
    """What this municipality can prove (§16.3), derived from the data we
    actually hold rather than a hand-maintained list that goes stale."""
    try:
        table = _roll_table(conn)
        with_apn, total = conn.execute(
            f"SELECT SUM(CASE WHEN apn IS NOT NULL AND apn != '' THEN 1 "
            f"ELSE 0 END), COUNT(*) FROM {table} WHERE city = ?",
            (city,)).fetchone()
    except sqlite3.Error:
        return "none"
    if not total:
        return "none"
    return "parcel_roll" if with_apn else "address_points"


def validate_property(user_property_id_: str, db_path: Path | str,
                      spine_db: Path | str | None = None) -> VerificationResult:
    """Run the §16.2 checks and persist the outcome + evidence (AC-16.3)."""
    spine_path = spine_db or db_path
    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        sub = conn.execute(
            "SELECT name, address, city, units, parcel_id "
            "  FROM user_properties WHERE user_property_id=?",
            (user_property_id_,)).fetchone()
    if sub is None:
        raise ValueError(f"no such user property: {user_property_id_}")
    name, address, city, units, parcel_id = sub

    with sqlite3.connect(spine_path) as sconn:
        cap = city_capability(sconn, city)
        if cap == "none":
            result = VerificationResult(
                PENDING, f"{city} has no municipal data on the backbone yet "
                         f"- queued; re-validated automatically when the "
                         f"city's feed lands", {"capability": cap})
            _persist(db_path, user_property_id_, result)
            return result
        cand = _find_candidate(sconn, city, address, parcel_id)

    if cand is None:
        result = VerificationResult(
            FAILED, f"address not found in the {city} assessor roll "
                    f"(checked {norm_addr(address)!r})",
            {"capability": cap, "address": {"ok": False,
                                            "entered": address}})
        _persist(db_path, user_property_id_, result)
        return result

    checks: dict = {"capability": cap}
    checks["address"] = {"ok": True, "entered": address,
                         "municipal": cand["address"]}

    # Parcel: exact after normalization. A submission without a parcel id
    # inherits the one the address lookup found (reverse lookup, §16.2).
    muni_apn = spine.normalize_apn(cand["apn"] or "")
    if parcel_id:
        ok = spine.normalize_apn(parcel_id) == muni_apn and bool(muni_apn)
        checks["parcel"] = {"ok": ok, "entered": parcel_id,
                            "municipal": cand["apn"]}
    else:
        checks["parcel"] = {"ok": bool(muni_apn), "entered": None,
                            "municipal": cand["apn"],
                            "note": "resolved by address lookup"}

    # Units: ±10%, against a count the municipality actually states.
    muni_units = cand["units"]
    if muni_units is None:
        checks["units"] = {"ok": None, "entered": units, "municipal": None}
        result = VerificationResult(
            PENDING, f"the {city} record for this parcel has no unit count "
                     f"- queued until one lands", checks,
            matched_8r_id=cand["property_id"])
        _persist(db_path, user_property_id_, result)
        return result
    ok_units = abs(units - muni_units) <= max(1, round(
        muni_units * UNIT_TOLERANCE))
    checks["units"] = {"ok": ok_units, "entered": units,
                       "municipal": muni_units}

    # Name: soft (§16.2). Municipal rolls carry no marketing names, so this
    # only annotates - here it records what was checked for the evidence
    # trail; the property-site corroboration runs host-side where the
    # network allows it.
    checks["name"] = {"ok": None, "entered": name, "soft": True}

    if checks["parcel"]["ok"] and ok_units:
        result = VerificationResult(VERIFIED, "core elements match the "
                                    f"{city} assessor roll", checks,
                                    matched_8r_id=cand["property_id"])
    elif not checks["parcel"]["ok"]:
        result = VerificationResult(
            FAILED, f"parcel mismatch: you entered {parcel_id!r}, the "
                    f"{city} roll has {cand['apn']!r} at this address",
            checks, matched_8r_id=cand["property_id"])
    else:
        result = VerificationResult(
            FAILED, f"unit count contradicts the {city} roll: you entered "
                    f"{units}, the municipal record says {muni_units}",
            checks, matched_8r_id=cand["property_id"])
    _persist(db_path, user_property_id_, result)
    return result


def _roll_table(sconn: sqlite3.Connection) -> str:
    """Where the FULL municipal roll lives. The backbone is pruned to 10+
    units (phase0.prune_backbone), but the badge must be able to check a
    submission against every parcel the municipality publishes — including
    the one that says 8 units and therefore FAILS the claim of 48. The
    compact `parcel_index` keeps that power; older databases predating the
    prune still hold the full roll in properties_8r."""
    row = sconn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='parcel_index'").fetchone()
    return "parcel_index" if row else "properties_8r"


def _find_candidate(sconn: sqlite3.Connection, city: str, address: str,
                    parcel_id: str | None) -> dict | None:
    """The municipal row this submission claims to be. Parcel wins over
    address when both are present and disagree - the parcel is the claim
    being verified."""
    table = _roll_table(sconn)
    pid_col = "NULL" if table == "parcel_index" else "property_id"
    if parcel_id:
        apn = spine.normalize_apn(parcel_id)
        for r in sconn.execute(
                f"SELECT {pid_col}, apn, address, units FROM {table}"
                " WHERE city = ? AND apn IS NOT NULL", (city,)):
            if spine.normalize_apn(r[1] or "") == apn:
                return dict(zip(("property_id", "apn", "address", "units"), r))
    want = norm_addr(address)
    if not want:
        return None
    for r in sconn.execute(
            f"SELECT {pid_col}, apn, address, units FROM {table} "
            " WHERE city = ?", (city,)):
        if norm_addr(r[2]) == want:
            return dict(zip(("property_id", "apn", "address", "units"), r))
    return None


def _persist(db_path, pid: str, result: VerificationResult) -> None:
    now = dt.datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE user_properties SET status=?, evidence=?, "
            " matched_8r_id=?, validated_at=? WHERE user_property_id=?",
            (result.status, result.to_json(), result.matched_8r_id, now, pid))


def revalidate_queue(db_path: Path | str,
                     spine_db: Path | str | None = None) -> dict:
    """Nightly sweep (§16.4): everything not currently VERIFIED gets another
    look - a Pending city may have gained its feed, a Failed submission may
    have been corrected, and a municipal refresh can revoke a badge."""
    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        pids = [r[0] for r in conn.execute(
            "SELECT user_property_id FROM user_properties")]
    counts: dict[str, int] = {}
    for pid in pids:
        res = validate_property(pid, db_path, spine_db)
        counts[res.status] = counts.get(res.status, 0) + 1
    return counts


def list_user_properties(db_path: Path | str,
                         org_id: str | None = None) -> list[dict]:
    cols = ("user_property_id", "org_id", "name", "address", "city", "units",
            "parcel_id", "website", "status", "evidence", "matched_8r_id",
            "submitted_at", "validated_at")
    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        if org_id:
            rows = conn.execute(
                "SELECT * FROM user_properties WHERE org_id=? "
                "ORDER BY submitted_at DESC", (org_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM user_properties "
                                "ORDER BY submitted_at DESC").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def comp_eligible_ids(db_path: Path | str) -> set[str]:
    """AC-16.2 — only blue-checked user properties may enter comp sets
    outside the submitting org. This is THE gate; comps callers use it."""
    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        return {r[0] for r in conn.execute(
            "SELECT user_property_id FROM user_properties WHERE status=?",
            (VERIFIED,))}
