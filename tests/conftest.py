"""Pytest bootstrap — load .env so DATABASE_URL is available to the pilot tests.

The app loads .env at startup via app.py; pytest does not run app.py, so we load
it here. This lets `uv run pytest` find the Postgres connection written by
deploy/windows/setup-db.ps1 (or deploy/install.sh) without exporting env vars by
hand. Harmless when no .env exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv always present in this project, but be safe
    pass


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

    if not pg.is_configured():
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
