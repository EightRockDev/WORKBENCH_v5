"""Alert sweep runner (spec 6.1) - 5th autopilot step, after phase0."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import alerts, phase0  # noqa: E402


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None:
        print("alerts: no workbench.db - nothing to sweep")
        return 0
    counts = alerts.run_sweep(db)
    # `counts` is what this cycle INSERTED; the list below is every OPEN
    # alert. Printing "0 new multifamily" above 25 [new_mf] lines read as a
    # contradiction on 2026-08-01 - both numbers were right and the report
    # never said they measured different things.
    print(f"alert sweep, NEW this cycle: {counts['new_mf']} multifamily, "
          f"{counts['units_jump']} unit-count moves, "
          f"{counts.get('owner_change', 0)} ownership changes")

    open_counts = alerts.count_open_alerts(db)
    total = open_counts.get("total", 0)
    shown = min(total, 25)
    by_kind = ", ".join(f"{k} {n}" for k, n in sorted(open_counts.items())
                        if k != "total")
    if total:
        print(f"OPEN alerts (carried forward): {total}"
              + (f" - {by_kind}" if by_kind else ""))
        print(f"  showing the {shown} most recent"
              + (f" of {total}" if total > shown else ""))
    else:
        print("OPEN alerts: none")
    for a in alerts.open_alerts(db, limit=25):
        print(f"  [{a['kind']}] {a['headline']}  ({a['detail']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
