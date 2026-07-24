"""Phase 0 — Eight Rock native identifiers & taxonomy (spec §7.2).

Executes the replacement taxonomy that removes every ALN-derived identifier and
classification, so nothing ALN-sourced remains discernible (§7 directive):

  * **Property ID** ``8R-{FIPS}-{parcel-hash}`` - deterministic: 5-digit county
    FIPS + first 12 hex of SHA-256 over the normalized APN. Regenerable from
    public records alone, provably non-ALN, stable across refreshes.
    Properties without an APN get ``8R-{FIPS}-X{geohash9}`` until parcel match,
    then migrate with an alias record.
  * **``8r_class``** replaces ALN's price class - computed from Eight Rock's own
    codified criteria (vintage band, rent position vs. submarket, condition
    signals from permits/assessor). This converts a licensing liability into
    buy-box IP.
  * **``8r_form``** replaces ALN building style - re-derived from assessor use
    codes + unit counts.
  * **``8r_market`` / ``8r_submarket``** keyed to Census CBSA + Eight Rock
    submarket definitions.

Deterministic and offline: every value is reproducible from public inputs, which
is what makes AC-P0-2 ("no field derivable only from ALN") provable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"   # geohash alphabet


# ---------------------------------------------------------------------------
# §7.2 — Property identity
# ---------------------------------------------------------------------------

def normalize_apn(apn: str) -> str:
    """Uppercase, punctuation stripped — the normalization the ID hash is over."""
    return re.sub(r"[^A-Z0-9]", "", (apn or "").upper())


def property_id(fips: str | int, apn: str) -> str:
    """``8R-{FIPS}-{first 12 hex of SHA-256(normalized APN)}`` (§7.2)."""
    f = str(fips).zfill(5)
    if not re.fullmatch(r"\d{5}", f):
        raise ValueError(f"FIPS must be 5 digits, got {fips!r}")
    norm = normalize_apn(apn)
    if not norm:
        raise ValueError("APN is empty after normalization")
    h = hashlib.sha256(norm.encode()).hexdigest()[:12]
    return f"8R-{f}-{h}"


def geohash(lat: float, lng: float, precision: int = 9) -> str:
    """Standard geohash — used for provisional IDs before parcel match."""
    lat_r, lng_r = [-90.0, 90.0], [-180.0, 180.0]
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = sum(lng_r) / 2
            if lng > mid:
                ch = (ch << 1) | 1; lng_r[0] = mid
            else:
                ch <<= 1; lng_r[1] = mid
        else:
            mid = sum(lat_r) / 2
            if lat > mid:
                ch = (ch << 1) | 1; lat_r[0] = mid
            else:
                ch <<= 1; lat_r[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_B32[ch]); bit = ch = 0
    return "".join(out)


def provisional_property_id(fips: str | int, lat: float, lng: float) -> str:
    """``8R-{FIPS}-X{geohash9}`` for stock without an APN yet (§7.2)."""
    f = str(fips).zfill(5)
    return f"8R-{f}-X{geohash(lat, lng, 9)}"


def is_provisional(pid: str) -> bool:
    parts = (pid or "").split("-")
    return len(parts) == 3 and parts[2].startswith("X")


def parse_property_id(pid: str) -> tuple[str, str, bool]:
    """(fips, suffix, provisional). Raises on a non-8R id."""
    m = re.fullmatch(r"8R-(\d{5})-(X?[0-9a-z]+)", pid or "")
    if not m:
        raise ValueError(f"not an Eight Rock property id: {pid!r}")
    return m.group(1), m.group(2), m.group(2).startswith("X")


@dataclass(frozen=True)
class Alias:
    """Crosswalk row used during cutover; destroyed after the 30-day soak (§7.3)."""

    legacy_id: str
    r8_id: str
    note: str = ""


# ---------------------------------------------------------------------------
# §7.2 — 8r_class (Eight Rock's own criteria, replacing ALN price class)
# ---------------------------------------------------------------------------

def vintage_band(year_built: int | None) -> str:
    if not year_built:
        return "unknown"
    if year_built >= 2010:
        return "modern"
    if year_built >= 1990:
        return "recent"
    if year_built >= 1975:
        return "mature"
    return "vintage"


def classify_8r_class(*, year_built: int | None, rent_percentile: float | None,
                      permits_last_5y: int = 0,
                      condition_flags: int = 0) -> tuple[str, list[str]]:
    """Return ``(8r_class, rationale)`` — A/B/C/D from Eight Rock's own inputs.

    ``rent_percentile`` is the property's rent position within its submarket
    (0-1). ``condition_flags`` counts adverse assessor/permit signals.
    """
    why: list[str] = []
    score = 0.0

    vb = vintage_band(year_built)
    vscore = {"modern": 3.0, "recent": 2.0, "mature": 1.0, "vintage": 0.0, "unknown": 1.0}[vb]
    score += vscore
    why.append(f"vintage band '{vb}'" + (f" ({year_built})" if year_built else ""))

    if rent_percentile is not None:
        rp = max(0.0, min(1.0, rent_percentile))
        score += rp * 3.0
        why.append(f"rent at the {rp*100:.0f}th percentile of its submarket")
    else:
        score += 1.5
        why.append("rent position unknown - neutral")

    if permits_last_5y >= 3:
        score += 1.0
        why.append(f"{permits_last_5y} permits in 5 years - reinvested")
    elif permits_last_5y == 0:
        score -= 0.5
        why.append("no permits in 5 years")

    if condition_flags:
        score -= min(1.5, 0.5 * condition_flags)
        why.append(f"{condition_flags} adverse condition signal(s)")

    if score >= 5.5:
        cls = "A"
    elif score >= 3.75:
        cls = "B"
    elif score >= 2.0:
        cls = "C"
    else:
        cls = "D"
    why.append(f"composite {score:.2f} -> class {cls}")
    return cls, why


# ---------------------------------------------------------------------------
# §7.2 — 8r_form (replaces ALN building style), from assessor use code + units
# ---------------------------------------------------------------------------

_USE_CODE_HINTS = {
    "townhouse": "townhome", "townhome": "townhome", "rowhouse": "townhome",
    "garden": "garden", "walkup": "garden", "walk-up": "garden",
    "midrise": "mid-rise", "mid-rise": "mid-rise", "elevator": "mid-rise",
    "highrise": "high-rise", "high-rise": "high-rise", "tower": "high-rise",
    "duplex": "small-plex", "triplex": "small-plex", "fourplex": "small-plex",
    "quadplex": "small-plex",
}


def derive_8r_form(use_code: str | None, units: int | None,
                   stories: int | None = None) -> str:
    """Re-derive building form from public assessor data (never ALN)."""
    text = (use_code or "").lower()
    for token, form in _USE_CODE_HINTS.items():
        if token in text:
            return form
    if units is not None and units <= 4:
        return "small-plex"
    if stories:
        if stories >= 8:
            return "high-rise"
        if stories >= 4:
            return "mid-rise"
        return "garden"
    if units is not None and units >= 150:
        return "mid-rise"
    return "garden"


# ---------------------------------------------------------------------------
# §7.4 AC-P0-1 — "not discernible" verification sweep
# ---------------------------------------------------------------------------

_ALN_TOKEN = re.compile(r"\baln\b|aln_", re.IGNORECASE)

# Words that legitimately contain "aln" and must not trip the sweep.
_ALLOWED_SUBSTRING = re.compile(r"[a-z]aln|aln[a-z]", re.IGNORECASE)


def scan_text_for_aln(text: str) -> list[str]:
    """Return offending snippets — used by the AC-P0-1 zero-hit verification."""
    hits: list[str] = []
    for line in (text or "").splitlines():
        for m in _ALN_TOKEN.finditer(line):
            frag = line[max(0, m.start() - 20):m.end() + 20].strip()
            if _ALLOWED_SUBSTRING.fullmatch(m.group(0)):
                continue
            hits.append(frag)
    return hits


def record_is_clean(record: dict) -> tuple[bool, list[str]]:
    """AC-P0-1/AC-P0-2 check for one stored record: no ALN keys or values, and a
    native 8R identifier."""
    problems: list[str] = []
    for k, v in (record or {}).items():
        if _ALN_TOKEN.search(str(k)):
            problems.append(f"field name '{k}' references ALN")
        if isinstance(v, str) and _ALN_TOKEN.search(v):
            problems.append(f"value of '{k}' references ALN")
    pid = str(record.get("property_id", ""))
    if pid and not pid.startswith("8R-"):
        problems.append(f"property_id '{pid}' is not an Eight Rock native id")
    return (not problems), problems
