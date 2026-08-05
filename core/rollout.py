"""The 50-metro rollout registry and coverage math (spec §15).

One place answers "where are we live, and what's next": the Coverage page
renders this, and each wave's start is a one-line status flip here. Counts
come from `properties_8r` — the same backbone everything else reads — so the
page can never advertise coverage the comp engine doesn't have.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

# (state display name, metro display name, db city names it aggregates)
# Deployment order per spec §15: Hampton Roads home base, then waves 1-5.
# A metro is "live" the moment its db cities have 10+ door records on the
# backbone - derived, never hand-flagged.
ROLLOUT: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Home base
    ("Virginia", "Norfolk", ("Norfolk",)),
    ("Virginia", "Virginia Beach", ("Virginia Beach",)),
    ("Virginia", "Chesapeake", ("Chesapeake",)),
    ("Virginia", "Newport News", ("Newport News",)),
    ("Virginia", "Hampton", ("Hampton",)),
    ("Virginia", "Portsmouth", ("Portsmouth",)),
    ("Virginia", "Suffolk", ("Suffolk",)),
    # Wave 1 - Virginia adjacency
    ("Virginia", "Richmond", ("Richmond",)),
    ("Virginia", "Charlottesville", ("Charlottesville",)),
    ("Virginia", "Roanoke", ("Roanoke",)),
    ("Virginia", "Lynchburg", ("Lynchburg",)),
    ("Virginia", "Fredericksburg", ("Fredericksburg",)),
    # Wave 2 - Carolinas + DMV
    ("North Carolina", "Raleigh-Durham", ("Raleigh", "Durham")),
    ("North Carolina", "Charlotte", ("Charlotte",)),
    ("North Carolina", "Greensboro / Winston-Salem",
     ("Greensboro", "Winston-Salem")),
    ("North Carolina", "Fayetteville", ("Fayetteville",)),
    ("North Carolina", "Wilmington", ("Wilmington",)),
    ("South Carolina", "Columbia", ("Columbia",)),
    ("South Carolina", "Charleston", ("Charleston",)),
    ("South Carolina", "Greenville-Spartanburg",
     ("Greenville", "Spartanburg")),
    ("District of Columbia", "Washington DC / NoVA", ("Washington",)),
    ("Maryland", "Baltimore", ("Baltimore",)),
    # Wave 3 - Southeast
    ("Georgia", "Atlanta", ("Atlanta",)),
    ("Georgia", "Savannah", ("Savannah",)),
    ("Georgia", "Augusta", ("Augusta",)),
    ("Florida", "Jacksonville", ("Jacksonville",)),
    ("Florida", "Orlando", ("Orlando",)),
    ("Florida", "Tampa-St. Petersburg", ("Tampa", "St. Petersburg")),
    ("Alabama", "Birmingham", ("Birmingham",)),
    ("Alabama", "Huntsville", ("Huntsville",)),
    ("Tennessee", "Nashville", ("Nashville",)),
    ("Tennessee", "Knoxville", ("Knoxville",)),
    ("Tennessee", "Chattanooga", ("Chattanooga",)),
    ("Tennessee", "Memphis", ("Memphis",)),
    ("Kentucky", "Louisville", ("Louisville",)),
    ("Kentucky", "Lexington", ("Lexington",)),
    # Wave 4 - Texas + heartland
    ("Texas", "Dallas-Fort Worth", ("Dallas", "Fort Worth")),
    ("Texas", "Houston", ("Houston",)),
    ("Texas", "San Antonio", ("San Antonio",)),
    ("Texas", "Austin", ("Austin",)),
    ("Oklahoma", "Oklahoma City", ("Oklahoma City",)),
    ("Oklahoma", "Tulsa", ("Tulsa",)),
    ("Arkansas", "Little Rock", ("Little Rock",)),
    ("Missouri", "Kansas City", ("Kansas City",)),
    ("Missouri", "St. Louis", ("St. Louis",)),
    ("Indiana", "Indianapolis", ("Indianapolis",)),
    ("Ohio", "Columbus", ("Columbus",)),
    ("Ohio", "Cincinnati", ("Cincinnati",)),
    # Wave 5 - growth West + fill
    ("Arizona", "Phoenix", ("Phoenix",)),
    ("Arizona", "Tucson", ("Tucson",)),
    ("Nevada", "Las Vegas", ("Las Vegas",)),
    ("Colorado", "Denver", ("Denver",)),
    ("Colorado", "Colorado Springs", ("Colorado Springs",)),
    ("Utah", "Salt Lake City", ("Salt Lake City",)),
    ("Idaho", "Boise", ("Boise",)),
    ("New Mexico", "Albuquerque", ("Albuquerque",)),
    ("Pennsylvania", "Pittsburgh", ("Pittsburgh",)),
)

MIN_DOORS = 10          # "10 or more doors" - the page's stated floor


# A covered metro is "confident" only when its confirmed (units>=10) count is a
# real market number — not an artifact of a locality whose feed omits unit
# counts. Hampton (2 confirmed vs ~52K parcels) and Suffolk (17) trip this:
# their VGIN feed publishes no unit counts, so we can't confirm 10+ doors even
# though the parcels are on hand. Showing "Hampton: 2" would read as the whole
# market; "feed incomplete" is the honest label (owner ask 2026-08-05).
_CONFIDENT_MIN_RECORDS = 25       # this many confirmed MF = a real number
_INCOMPLETE_MIN_PARCELS = 3000    # a locality this big with ~none confirmed


@dataclass(frozen=True)
class MetroCoverage:
    state: str
    metro: str
    records: int         # backbone properties with >= MIN_DOORS units
    doors: int           # total units across those properties
    parcels: int = 0     # total parcels on the full roll (feed presence signal)

    @property
    def live(self) -> bool:
        return self.records > 0

    @property
    def confident(self) -> bool:
        """The confirmed count is a real market number, not a feed artifact."""
        if self.records >= _CONFIDENT_MIN_RECORDS:
            return True
        return self.records > 0 and self.parcels < _INCOMPLETE_MIN_PARCELS

    @property
    def feed_incomplete(self) -> bool:
        """Parcels are on hand but MF can't be confirmed (feed omits units)."""
        return self.parcels > 0 and not self.confident


def _roll_table(conn: sqlite3.Connection) -> str:
    """The full-roll table: parcel_index after a prune, else properties_8r."""
    try:
        conn.execute("SELECT 1 FROM parcel_index LIMIT 1").fetchone()
        return "parcel_index"
    except sqlite3.Error:
        return "properties_8r"


def coverage(db_path: Path | str) -> list[MetroCoverage]:
    """One row per §15 metro, in deployment order, counted from the
    backbone. A metro with no parcels renders as Coming soon; a metro with
    parcels but no confirmable MF renders as feed-incomplete - the page
    cannot say more than the data does."""
    counts: dict[str, tuple[int, int]] = {}
    parcels: dict[str, int] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            for city, n, doors in conn.execute(
                    "SELECT city, COUNT(*), COALESCE(SUM(units), 0) "
                    "  FROM properties_8r WHERE units >= ? GROUP BY city",
                    (MIN_DOORS,)):
                counts[str(city or "")] = (int(n), int(doors))
            roll = _roll_table(conn)
            for city, n in conn.execute(
                    f"SELECT city, COUNT(*) FROM {roll} GROUP BY city"):
                parcels[str(city or "")] = int(n)
    except sqlite3.Error:
        counts, parcels = {}, {}
    out = []
    for state, metro, cities in ROLLOUT:
        n = sum(counts.get(c, (0, 0))[0] for c in cities)
        doors = sum(counts.get(c, (0, 0))[1] for c in cities)
        pc = sum(parcels.get(c, 0) for c in cities)
        out.append(MetroCoverage(state, metro, n, doors, pc))
    return out


def by_state(rows: list[MetroCoverage]) -> list[tuple[str, int, int,
                                                      list[MetroCoverage]]]:
    """[(state, state_doors, state_records, metros-in-rollout-order)],
    states ordered by first appearance in the rollout (deployment order)."""
    order: list[str] = []
    grouped: dict[str, list[MetroCoverage]] = {}
    for r in rows:
        if r.state not in grouped:
            grouped[r.state] = []
            order.append(r.state)
        grouped[r.state].append(r)
    return [(st, sum(m.doors for m in grouped[st]),
             sum(m.records for m in grouped[st]), grouped[st])
            for st in order]
