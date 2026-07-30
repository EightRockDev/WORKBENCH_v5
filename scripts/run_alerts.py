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
    print(f"alert sweep: {counts['new_mf']} new multifamily, "
          f"{counts['units_jump']} unit-count moves")
    for a in alerts.open_alerts(db, limit=25):
        print(f"  [{a['kind']}] {a['headline']}  ({a['detail']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
