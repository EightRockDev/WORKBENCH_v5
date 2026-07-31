"""Learn which numeric use codes mean "apartments", per city.

`core.phase0.is_multifamily` recognises use codes by TEXT — "Apartment",
"Multi Family", "MF". That works for Norfolk ("Apartment"), Virginia Beach
("Multi Family") and Chesapeake ("Apartments"), and is useless against a roll
that publishes bare integers. Portsmouth is exactly that case: 36,464 parcels
ingested, use codes ``9``, ``18``, ``7``, ``5``, ``3`` — and consequently ZERO
multifamily found in a city with 45 known apartment properties.

Guessing what Portsmouth's ``18`` means would be inventing data. Instead the
mapping is LEARNED from properties already known to be multifamily: take the
reference set, find the assessor parcels they matched to, and see which codes
those parcels actually carry. Empirical, reproducible from the operator's own
database, and auditable — every accepted code is reported with the evidence
that earned it.

Deliberately conservative. A code is accepted only with enough supporting
parcels AND enough concentration, because the failure mode is severe and
asymmetric: "Residential" swept in as multifamily would bury 30,000 houses in
the comp pool, which is the same class of mistake that VB zoning "R-40" caused
when it substring-matched "r-4" (see phase0._MF_USE_TOKENS).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

# A code must appear on at least this many known-multifamily parcels before it
# is trusted. Below this a single mis-matched address could mint a rule.
MIN_SUPPORT = 3

# Of all known-MF parcels in the city, this share must carry the code for it to
# read as "the apartment code" rather than an incidental one.
MIN_SHARE = 0.25

# A code carried by a large share of the ENTIRE city cannot be multifamily-
# specific — apartments are a small minority of any parcel roll. This is the
# guard that keeps a generic "Residential" out.
MAX_CITYWIDE_SHARE = 0.10

_NUMERIC = re.compile(r"^\s*\d+(\.\d+)?\s*$")


@dataclass
class CodeEvidence:
    """Why one use code was accepted or rejected."""
    code: str
    mf_parcels: int = 0
    citywide_parcels: int = 0
    citywide_total: int = 0
    accepted: bool = False
    reason: str = ""

    @property
    def citywide_share(self) -> float:
        return (self.citywide_parcels / self.citywide_total) if self.citywide_total else 0.0


@dataclass
class CityLearning:
    city: str
    known_mf_parcels: int = 0
    evidence: list[CodeEvidence] = field(default_factory=list)

    @property
    def accepted_codes(self) -> list[str]:
        return sorted(e.code for e in self.evidence if e.accepted)

    def describe(self) -> list[str]:
        out = [f"{self.city}: {self.known_mf_parcels} known-MF parcels matched"]
        if not self.evidence:
            out.append("    no candidate codes (nothing to learn from)")
        for e in sorted(self.evidence, key=lambda x: -x.mf_parcels):
            mark = "ACCEPT" if e.accepted else "reject"
            out.append(
                f"    [{mark}] code {e.code!r}: on {e.mf_parcels} MF parcels, "
                f"{e.citywide_parcels:,}/{e.citywide_total:,} citywide "
                f"({e.citywide_share:.1%}) - {e.reason}")
        return out


def is_opaque(use_code: str | None) -> bool:
    """True when a code carries no words for the text rules to match.

    Bare numbers and one/two-character codes. If a roll publishes readable
    text, `phase0.is_multifamily` already handles it and learning would only
    add risk.
    """
    text = (use_code or "").strip()
    if not text:
        return False
    if _NUMERIC.match(text):
        return True
    return len(text) <= 2 and not text.isalpha()


def learn_city(
    city: str,
    mf_codes: Iterable[str],
    citywide_codes: Counter,
    min_support: int = MIN_SUPPORT,
    min_share: float = MIN_SHARE,
    max_citywide_share: float = MAX_CITYWIDE_SHARE,
) -> CityLearning:
    """Decide which codes to trust for one city.

    `mf_codes` is one entry per known-multifamily parcel (repeats expected);
    `citywide_codes` is the tally across the whole roll, which is what makes
    the "too common to be apartments" check possible.
    """
    mf_tally = Counter(str(c).strip() for c in mf_codes if str(c or "").strip())
    total_mf = sum(mf_tally.values())
    citywide_total = sum(citywide_codes.values())
    result = CityLearning(city=city, known_mf_parcels=total_mf)

    for code, support in mf_tally.most_common():
        ev = CodeEvidence(
            code=code,
            mf_parcels=support,
            citywide_parcels=citywide_codes.get(code, 0),
            citywide_total=citywide_total,
        )
        share_of_mf = support / total_mf if total_mf else 0.0

        if support < min_support:
            ev.reason = f"only {support} supporting parcel(s), need {min_support}"
        elif share_of_mf < min_share:
            ev.reason = (f"only {share_of_mf:.0%} of this city's known MF, "
                         f"need {min_share:.0%}")
        elif ev.citywide_share > max_citywide_share:
            ev.reason = (f"{ev.citywide_share:.0%} of the whole roll carries it "
                         f"- too common to mean apartments")
        else:
            ev.accepted = True
            ev.reason = (f"{share_of_mf:.0%} of known MF, only "
                         f"{ev.citywide_share:.1%} of the roll")
        result.evidence.append(ev)
    return result


# ---------------------------------------------------------------------------
# Persistence — the learned map is data, not code
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_mf_use_codes (
    city        TEXT NOT NULL,
    use_code    TEXT NOT NULL,
    mf_parcels  INTEGER NOT NULL,
    evidence    TEXT,
    learned_at  TEXT NOT NULL,
    PRIMARY KEY (city, use_code)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def save(conn: sqlite3.Connection, learning: CityLearning, now: str) -> int:
    """Persist accepted codes for a city, replacing any earlier learning."""
    ensure_schema(conn)
    conn.execute("DELETE FROM learned_mf_use_codes WHERE city = ?", (learning.city,))
    rows = [
        (learning.city, e.code, e.mf_parcels,
         json.dumps({"citywide_parcels": e.citywide_parcels,
                     "citywide_total": e.citywide_total,
                     "reason": e.reason}),
         now)
        for e in learning.evidence if e.accepted
    ]
    conn.executemany(
        "INSERT INTO learned_mf_use_codes "
        "(city, use_code, mf_parcels, evidence, learned_at) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()
    return len(rows)


def load(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """city -> accepted codes. Empty when nothing has been learned yet."""
    try:
        ensure_schema(conn)
        out: dict[str, set[str]] = defaultdict(set)
        for city, code in conn.execute(
                "SELECT city, use_code FROM learned_mf_use_codes"):
            out[str(city)].add(str(code))
        return dict(out)
    except sqlite3.Error:
        return {}


def is_multifamily_learned(city: str, use_code: Any,
                           learned: dict[str, set[str]]) -> bool:
    """Does this city's learned map mark this code as multifamily?

    Scoped per city on purpose: Portsmouth's "18" has nothing to do with
    Suffolk's "18". A global map would leak one roll's meaning into another.
    """
    codes = learned.get(city)
    if not codes:
        return False
    return str(use_code or "").strip() in codes
