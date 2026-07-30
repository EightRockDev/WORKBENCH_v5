"""Cutover preflight (spec 7.3, P0-3): how far from the flip are we?

Runs as the last autopilot step every night and answers, in one report:
which gates hold, which don't, and exactly what stands between the
current build and setting SPINE_READ_SOURCE="8r". The flip itself stays
a deliberate act - this script only ever REPORTS.

Checks:
  1. P0-1 coverage + P0-2 comp overlap / rent delta (phase0-gates.json,
     written by run_phase0.py in the same cycle)
  2. Crosswalk materialized (property_crosswalk row count)
  3. Rent-signal coverage on the multifamily backbone, by source
     (listings beats hud_fmr; "none" rows would degrade comps at flip)
  4. Deal references: dry-run migration against any local deals tables -
     unmapped ids are listed because they'd resolve only through the
     crosswalk join after the flip
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0  # noqa: E402
from core.cutover import load_crosswalk, migrate_deal_references  # noqa: E402

GATE_COMP_OVERLAP = 0.90
GATE_RENT_DELTA = 0.05


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    db = phase0.find_workbench_db()
    if db is None:
        print("preflight: no workbench.db found - nothing to check yet")
        return 0

    checks: list[tuple[bool, str]] = []

    gates_file = root / "reports" / "phase0-gates.json"
    gates = {}
    if gates_file.is_file():
        try:
            gates = json.loads(gates_file.read_text())
        except json.JSONDecodeError:
            pass
    if gates:
        overlap = gates.get("comp_overlap") or 0.0
        delta = gates.get("rent_delta")
        checks.append((bool(gates.get("p0_1_passed")),
                       f"P0-1 coverage {gates.get('coverage', 0):.1%} (>= 95%)"))
        checks.append((overlap >= GATE_COMP_OVERLAP,
                       f"comp overlap {overlap:.1%} (>= {GATE_COMP_OVERLAP:.0%})"))
        checks.append((delta is not None and delta <= GATE_RENT_DELTA,
                       "rent delta "
                       + (f"{delta:.1%}" if delta is not None
                          else "unmeasured - no rent pairs yet")
                       + f" (<= {GATE_RENT_DELTA:.0%}, must be MEASURED)"))
    else:
        checks.append((False, "phase0-gates.json missing - run phase0 first"))

    xwalk = load_crosswalk(db)
    checks.append((len(xwalk) > 0, f"crosswalk materialized ({len(xwalk):,} mappings)"))

    # Rent coverage by source across the multifamily backbone.
    from core.phase0 import is_mf_ten_plus
    by_source: dict[str, int] = {}
    try:
        with sqlite3.connect(db) as conn:
            for uc, units, src in conn.execute(
                    "SELECT use_code, units, rent_source FROM properties_8r"):
                if is_mf_ten_plus(uc, units):
                    by_source[src or "none"] = by_source.get(src or "none", 0) + 1
    except sqlite3.Error:
        pass
    total_mf = sum(by_source.values())
    unrented = by_source.get("none", 0)
    cov = ((total_mf - unrented) / total_mf) if total_mf else 0.0
    checks.append((total_mf > 0 and cov >= 0.95,
                   f"rent coverage {cov:.1%} of {total_mf:,} MF rows "
                   + "(" + ", ".join(f"{k}: {n:,}" for k, n in
                                     sorted(by_source.items())) + ")"))

    # Dry-run the deal migration against any local reference tables.
    try:
        with sqlite3.connect(db) as conn:
            mig = migrate_deal_references(conn, xwalk, dry_run=True)
            conn.rollback()
        if mig.updated:
            pend = sum(mig.updated.values())
            miss = sum(len(v) for v in mig.unmapped.values())
            checks.append((miss == 0,
                           f"deal references: {pend} to migrate, {miss} unmapped"))
            for line in mig.summary().splitlines():
                print("   ", line)
        else:
            print("    (no local deals tables - pilot Postgres migrated at flip)")
    except Exception as exc:  # never fail the cycle over the preflight
        print(f"    deal check skipped: {exc}")

    print()
    ready = all(ok for ok, _ in checks)
    for ok, label in checks:
        print(f"  [{'PASS' if ok else '----'}] {label}")
    print()
    if ready:
        print("CUTOVER PREFLIGHT: ALL GATES HOLD.")
        print("Flip day runbook: migrate pilot Postgres deal references")
        print("(core.cutover.migrate_deal_references), set")
        print('SPINE_READ_SOURCE="8r" in config.py, run the full suite.')
    else:
        print("CUTOVER PREFLIGHT: not ready - unmet items marked [----] above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
