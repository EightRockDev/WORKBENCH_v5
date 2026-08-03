"""User-property validation sweep (spec §16.4) - runs after phase0.

Re-validates every non-verified submission each cycle: a Pending city may
have gained its municipal feed this very cycle (phase0 runs just before us),
a Failed submission may have been corrected, and a municipal refresh can
revoke a badge whose core elements drifted.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0, user_properties  # noqa: E402


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None:
        print("validate: no workbench.db - nothing to validate")
        return 0
    counts = user_properties.revalidate_queue(db)
    if not counts:
        print("validate: no user-added properties yet")
        return 0
    total = sum(counts.values())
    parts = ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
    print(f"validate: {total} user-added properties swept - {parts}")
    for row in user_properties.list_user_properties(db):
        print(f"  [{row['status']:<10}] {row['name']} - {row['address']}, "
              f"{row['city']} ({row['units']} units)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
