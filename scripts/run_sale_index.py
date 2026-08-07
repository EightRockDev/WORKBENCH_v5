"""Autopilot step: build the sale-history index (owner report 2026-08-09:
"Streamlit is running too slow" — render-time muni scans replaced by an
indexed table; see core/sale_index.py). Skips when muni_records is
unchanged since the last build."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import phase0, sale_index  # noqa: E402


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None or not pathlib.Path(db).exists():
        print("[saleindex] no workbench.db on this box - nothing to index")
        return 0
    stats = sale_index.build(pathlib.Path(db))
    if stats.get("skipped"):
        print(f"[saleindex] fresh (muni stamp {stats['stamp']}) - skipping")
    else:
        print(f"[saleindex] scanned {stats['scanned']:,} muni rows -> "
              f"{stats['sales']:,} sale records indexed "
              f"(stamp {stats['stamp']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
