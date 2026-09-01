"""The numbers behind the Property Screener's metrics box.

Owner ask (2026-09-01): a box at the bottom of the screener that explains
what a "property" is, why the workbench collects them, and how many sit
in each market and submarket.

The count here MUST agree with the daily brief and phase0-latest.txt, or
the owner sees two different totals for the same word. So this reuses the
exact classifier the spine build uses (`is_mf_ten_plus_for_city`,
learned use codes included) instead of re-deriving "multifamily" with its
own SQL — the 2026-08-11 lesson, where a looser definition claimed
102,232 Richmond "MF properties" overnight.

No Streamlit here; the UI caches and paints, this module counts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from core.phase0 import MIN_MF_UNITS, find_workbench_db, is_mf_ten_plus_for_city


@dataclass
class MarketRow:
    market: str
    count: int = 0                 # properties (the number the owner tracks)
    with_units: int = 0            # of them, how many have a real unit count
    units_total: int = 0           # sum of known unit counts
    submarkets: dict = field(default_factory=dict)   # name -> count


@dataclass
class Breakdown:
    total: int = 0                 # apartment properties, all markets
    total_records: int = 0         # every parcel on the backbone
    curated: int = 0               # the owner's own property records
    markets: list = field(default_factory=list)      # [MarketRow] desc
    error: str = ""                # human-readable when data is absent


def market_breakdown(db_path: str | Path | None = None) -> Breakdown:
    """Property counts per market/submarket, on the spine's own definition."""
    out = Breakdown()
    path = Path(db_path) if db_path else find_workbench_db()
    if path is None or not Path(path).exists():
        out.error = "The property database is not on this machine yet."
        return out

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "properties_8r" not in tables:
            out.error = ("The county backbone has not been built on this "
                         "machine yet - the autopilot builds it.")
            return out

        from core.use_code_learn import load as _load_learned
        try:
            learned = _load_learned(conn)
        except Exception:
            learned = {}

        rows: dict[str, MarketRow] = {}
        try:
            cursor = conn.execute(
                "SELECT COALESCE(r8_market, city), r8_submarket, city, "
                "       use_code, units FROM properties_8r")
        except sqlite3.OperationalError as exc:
            # A drifted/partial backbone (missing column on an old build)
            # must read as "not ready", never crash the whole screen -
            # found live 2026-09-01 when a dev database without use_code
            # took the entire Property Screener page down with it.
            out.error = (f"The backbone on this machine is from an older "
                         f"build ({exc}) - the next update-workbench run "
                         f"rebuilds it.")
            return out
        for market, sub, city, use_code, units in cursor:
            out.total_records += 1
            if not is_mf_ten_plus_for_city(city, use_code, units, learned):
                continue
            m = rows.setdefault(market or "(unassigned)",
                                MarketRow(market or "(unassigned)"))
            out.total += 1
            m.count += 1
            if units is not None and units > 0:
                m.with_units += 1
                m.units_total += int(units)
            key = (sub or "").strip()
            if key:
                m.submarkets[key] = m.submarkets.get(key, 0) + 1

        if "properties" in tables:
            out.curated = conn.execute(
                "SELECT count(*) FROM properties").fetchone()[0]

        out.markets = sorted(rows.values(), key=lambda r: -r.count)
        return out
    finally:
        conn.close()


def definition_text() -> str:
    """Plain-English: what counts, and why the workbench collects them."""
    return (
        f"**What counts as a property here:** an apartment building with "
        f"**{MIN_MF_UNITS} or more rental units** on one parcel, according "
        f"to the county assessor. Houses, duplexes and small plexes are "
        f"kept in the full parcel roll but do not count toward this "
        f"number.\n\n"
        f"**Why we collect them:** Eight Rock buys 20-400 unit Class B/C "
        f"apartment communities. The workbench pulls every qualifying "
        f"building in our target markets straight from county records - "
        f"no data vendor - so we can find deals before they list, price "
        f"them against true neighbours, and reach their owners directly."
    )
