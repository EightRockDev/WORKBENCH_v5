"""Public-data step (autopilot): the workbench feeds itself - HMDA loans
and HUD FMR pulled straight into the ETL db, freshness-gated so chained
cycles cost the federal APIs nothing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import public_data  # noqa: E402


def main() -> int:
    db = public_data.target_db()
    print(f"ETL db: {db}")
    public_data.pull_hmda(db)
    public_data.pull_hud_fmr(db)
    return 0   # a failed pull reports itself; it never fails the cycle


if __name__ == "__main__":
    raise SystemExit(main())
