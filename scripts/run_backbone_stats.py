"""Autopilot step: per-city multifamily unit stats from the backbone
(owner ask 2026-08-09: "smallest number of units on the Richmond 111?").

Reads properties_8r READ-ONLY every cycle (independent of phase0's rebuild
gating, so the numbers are always current) and writes min/median/max/count of
units per covered city to reports/backbone-stats-latest.txt. Answers unit-
distribution questions from the morning report stream without a manual query
against the host's 8GB workbench.db.
"""

from __future__ import annotations

import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0  # noqa: E402


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("backbone-stats: no workbench.db on this box - skipping")
        return 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT city, units FROM properties_8r "
            "WHERE units IS NOT NULL AND units >= ?", (phase0.MIN_MF_UNITS,)
        ).fetchall()
        totals = conn.execute(
            "SELECT COALESCE(r8_market, city) AS m, COUNT(*) AS n "
            "FROM properties_8r GROUP BY m ORDER BY n DESC").fetchall()
    except sqlite3.Error as exc:
        print(f"backbone-stats: query failed ({exc})")
        return 0
    finally:
        conn.close()

    by_city: dict[str, list[int]] = {}
    for r in rows:
        by_city.setdefault((r["city"] or "?"), []).append(int(r["units"]))

    print("Properties on the backbone, per metro/market:")
    print(f"{'market':<20} {'properties':>11}")
    grand = 0
    for r in totals:
        grand += r["n"]
        print(f"{(r['m'] or '?'):<20} {r['n']:>11,}")
    print(f"{'TOTAL':<20} {grand:>11,}\n")

    print(f"Multifamily unit stats (units >= {phase0.MIN_MF_UNITS}), per city:")
    print(f"{'city':<18} {'count':>6} {'min':>5} {'median':>7} {'max':>6}")
    for city in sorted(by_city, key=lambda c: -len(by_city[c])):
        u = by_city[city]
        print(f"{city:<18} {len(u):>6} {min(u):>5} "
              f"{int(statistics.median(u)):>7} {max(u):>6}")
    if not by_city:
        print("  (no unit-bearing multifamily rows found)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
