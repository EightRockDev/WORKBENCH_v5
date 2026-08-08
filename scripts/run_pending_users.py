"""Autopilot step: list users awaiting admin approval (owner directive
2026-08-09: "tell me every morning if there are new users waiting").

Writes reports/pending-users-latest.txt each cycle so the count rides the
report stream Claude reads every morning. Fail-silent when Postgres isn't
reachable (dev boxes) — an approval queue we can't read is a notice, not a
crash.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import pg  # noqa: E402


def main() -> int:
    if not pg.is_reachable():
        print("pending-users: Postgres not reachable - cannot read the queue")
        return 0
    from core import user_admin
    try:
        users = user_admin.list_users()
    except Exception as exc:
        print(f"pending-users: query failed ({exc})")
        return 0
    pending = [u for u in users if u.is_pending]
    print(f"PENDING APPROVAL: {len(pending)}")
    for u in pending:
        name = u.display_name or "(no name)"
        print(f"  - {name} <{u.email}>  status={u.status}  role={u.platform_role}")
    if not pending:
        print("  (none - no users waiting)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
