"""Refuse to run destructive Postgres tests against a real database.

2026-08-18, found on 2026-08-27: the pilot suites (`test_pilot_admin`,
`test_multitenancy`, `test_inbox`, `test_skiptrace`, `test_outreach`,
`test_compliance`) open each test with

    TRUNCATE users, organizations, audit_log RESTART IDENTITY CASCADE

to get a clean slate. `tests/conftest.py` loads `.env` so the suite can find
Postgres — and on the OWNER'S SERVER `.env` holds the LIVE connection. So
running `uv run pytest` there emptied production: 5 user accounts, both
organizations, 3 deals, 37 CRM contacts, 41 paid skip-trace records, 81
inbox messages, 53 activity rows and 17 audit entries, gone in one command.
The owner's own account was re-created by his next login, which is why the
loss looked like "the users disappeared" rather than a wipe.

The one guard that existed — the superuser check in conftest — waved it
through, because production correctly connects as a NON-superuser. It was
built to stop RLS false failures, not data loss, and read like safety.

So: name the database. A destructive suite may only run against a database
whose name says it is disposable. Anything else (`workbench` above all) is
treated as live, and the suites skip rather than truncate. `ER_ALLOW_LIVE_DB_TESTS=1`
exists for the one case that needs it — a deliberate, typed-out override on
a machine whose data you are willing to lose.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

# A database is disposable only if its NAME says so. Substring matching is
# deliberate ("wb_test_2", "test_pilot"), but `workbench` matches none of
# these — which is the entire point.
_SCRATCH_PATTERNS = (
    r"^wb_test",          # the name CLAUDE.md/conftest tell developers to use
    r"^test",             # test, test_pilot, testdb
    r"_test$",            # pilot_test, workbench_test
    r"_test_",
    r"^scratch",
    r"^wb_recover",       # the restore-verification database
)

_OVERRIDE_ENV = "ER_ALLOW_LIVE_DB_TESTS"


def database_name(url: str | None) -> str:
    """The database name from a Postgres URL ('' when there isn't one)."""
    if not url:
        return ""
    try:
        return (urlparse(url).path or "").lstrip("/").strip()
    except ValueError:
        return ""


def is_scratch_db(url: str | None) -> bool:
    """True when this URL names a database that is safe to TRUNCATE."""
    if os.environ.get(_OVERRIDE_ENV, "").strip() == "1":
        return True
    name = database_name(url).lower()
    if not name:
        return False
    return any(re.search(p, name) for p in _SCRATCH_PATTERNS)


def scratch_db_refusal(url: str | None) -> str:
    """The message shown when a destructive suite meets a live database."""
    name = database_name(url) or "(none)"
    return (
        f"REFUSING to run destructive Postgres tests against database "
        f"{name!r}: the name does not mark it as disposable. These suites "
        f"TRUNCATE users, organizations and audit_log — against the live "
        f"database that destroys real accounts and deal data (it did, on "
        f"2026-08-18). Point DATABASE_URL at a scratch database instead:\n"
        f"    CREATE DATABASE wb_test OWNER workbench;\n"
        f"    psql -d <wb_test url> -f db/pilot_schema.sql\n"
        f"or unset DATABASE_URL to skip the Postgres suites entirely. "
        f"{_OVERRIDE_ENV}=1 overrides this, and will destroy whatever it "
        f"is pointed at."
    )


def assert_scratch_db() -> None:
    """Last line of defence, called by every fixture that truncates.

    conftest already neutralizes a live DATABASE_URL at collection time, so
    reaching here means something bypassed that — a fixture run directly, a
    URL set mid-session, a new test file. Raise; never truncate.
    """
    from data import pg

    url = pg.database_url()
    if not is_scratch_db(url):
        raise RuntimeError(scratch_db_refusal(url))
