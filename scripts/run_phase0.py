"""Phase 0 runner (spec 7.3) - double-clicked via run-phase0.bat.

P0-1: builds `properties_8r` from muni_records and prints the coverage
report against the 95% gate. Read-only toward everything except the new
properties_8r table.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0  # noqa: E402


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None:
        print("No workbench.db with municipal data found.")
        print("Looked for data\\workbench.db (or set ER_WORKBENCH_DB=<full path>).")
        print()
        print("The 3.9M-row muni_records table lives in the v2.4.1 machine's")
        print("workbench.db - copy that file to this app's data\\ folder first,")
        print("or run etl_munidata.py here to pull fresh municipal data.")
        return 1

    n = phase0.has_muni_records(db)
    print(f"Database: {db}")
    print(f"Assessor records available: {n:,}")
    if n == 0:
        print()
        print("muni_records is empty here. Copy the v2.4.1 workbench.db into")
        print("data\\ (it holds the 3.9M municipal rows) or run etl_munidata.py.")
        return 1

    print()
    print("Building the Eight Rock property spine (properties_8r)...")
    report = phase0.build_spine(db)
    print()
    print(report.summary())
    print()
    if report.gate_passed:
        print("P0-1 GATE PASSED - next step is P0-2 shadow parity.")
    else:
        print("P0-1 gate not met yet. The 'attribute keys with no mapping'")
        print("list above is what to send back for tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
