"""Pytest bootstrap — load .env so DATABASE_URL is available to the pilot tests.

The app loads .env at startup via app.py; pytest does not run app.py, so we load
it here. This lets `uv run pytest` find the Postgres connection written by
deploy/windows/setup-db.ps1 (or deploy/install.sh) without exporting env vars by
hand. Harmless when no .env exists.

That convenience had a second, unintended half. On the owner's SERVER the
`.env` this loads holds the LIVE database, and the pilot suites TRUNCATE
users/organizations/audit_log to get a clean slate — so `uv run pytest`
there emptied production (2026-08-18; diagnosed 2026-08-27). The block
below closes that: a DATABASE_URL that does not name a disposable database
is neutralized for the whole session, so every Postgres suite SKIPS instead
of truncating. See tests/pgguard.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv always present in this project, but be safe
    pass


# ---------------------------------------------------------------------------
# Guard: destructive suites must never meet a live database
# ---------------------------------------------------------------------------
# Runs at IMPORT time, before any fixture or `pg.is_reachable()` cache can
# see the live URL. Clearing the env var is what makes every pg-gated suite
# skip; the fixtures also call assert_scratch_db() as a second line.
_LIVE_DB_NEUTRALIZED: str | None = None

def _neutralize_live_database_url() -> None:
    global _LIVE_DB_NEUTRALIZED
    from tests.pgguard import database_name, is_scratch_db

    url = os.environ.get("DATABASE_URL", "").strip()
    if url and not is_scratch_db(url):
        _LIVE_DB_NEUTRALIZED = database_name(url) or "(unnamed)"
        os.environ["DATABASE_URL"] = ""
        os.environ["ER_BACKUP_DATABASE_URL"] = ""
        # The visible announcement happens in pytest_configure below, where
        # it survives -q and pytest's capture.


try:                                   # never let the guard itself break collection
    _neutralize_live_database_url()
except Exception:                      # pragma: no cover - defensive
    os.environ["DATABASE_URL"] = ""    # fail CLOSED: no URL, no truncation


def _guard_banner() -> str:
    return (f"[db-guard] Postgres suites SKIPPED: DATABASE_URL named "
            f"{_LIVE_DB_NEUTRALIZED!r}, which is not a disposable test "
            f"database. Nothing in it was touched. (tests/pgguard.py)")


def pytest_configure(config):
    """Say it out loud, through the terminal reporter.

    Not `pytest_report_header`: pyproject sets addopts = "-q", which
    suppresses headers, and an import-time print is swallowed by capture.
    The reporter's own writer survives both — a guard nobody can see is how
    the previous one (the superuser check) read like safety while the live
    database was being emptied.
    """
    if not _LIVE_DB_NEUTRALIZED:
        return
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(_guard_banner(), red=True, bold=True)
    else:                                        # pragma: no cover
        print(_guard_banner(), file=sys.stderr)


# ---------------------------------------------------------------------------
# Guard: RLS is silently bypassed for superusers
# ---------------------------------------------------------------------------
# Every tenant- and user-isolation guarantee in this codebase is enforced by
# Postgres row-level security, not by WHERE clauses -- `list_messages`, for
# one, deliberately has no filter at all. PostgreSQL exempts superusers from
# RLS *even on tables declared FORCE ROW LEVEL SECURITY*, so pointing the
# suite at a superuser connection makes those tests fail for a reason that
# has nothing to do with the code. That cost real time on 2026-07-31: four
# isolation tests failed against a scratch database connected as `postgres`,
# and read exactly like a privacy hole until the role was swapped for a
# non-superuser owner, whereupon all 32 passed.
#
# Fail loudly at collection instead. The dangerous direction is not the false
# red -- it is someone "fixing" working isolation code to satisfy it.
#
# Production connects as the non-superuser `workbench` role that owns the
# database (deploy/windows/install.ps1). Mirror that locally:
#     CREATE ROLE wbapp LOGIN PASSWORD '...';
#     CREATE DATABASE wb_test OWNER wbapp;
#     psql -U wbapp -d wb_test -f db/pilot_schema.sql

def pytest_collection_modifyitems(config, items):
    from data import pg

    if not pg.is_reachable():
        return
    try:
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            row = cur.fetchone()
            is_super = bool(row and (row.get("rolsuper") if isinstance(row, dict)
                                     else row[0]))
    except Exception:
        return          # connection problems are the individual tests' business

    if is_super:
        raise pytest.UsageError(
            "DATABASE_URL points at a SUPERUSER connection. PostgreSQL exempts "
            "superusers from row-level security even with FORCE ROW LEVEL "
            "SECURITY, so every org/user isolation test would fail for the "
            "wrong reason. Point DATABASE_URL at a non-superuser role that "
            "owns the database (production uses the `workbench` role), or "
            "unset it to skip the Postgres suites."
        )
