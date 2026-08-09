"""PostgreSQL connection helper for the v5.0 pilot (Section 9.1).

The v2.4.1 app is SQLite-based (``data/db.py``); the V5-P0.5 cutover (Section
9.2) migrates the live store into PostgreSQL for true multi-user concurrency
(Section 9.3) and multi-tenant row-level security (Section 10.1). This module
is the connection spine that the new org/auth/POC layer (``db/pilot_schema.sql``)
runs on. It is intentionally small and dependency-light.

Connection string resolution order:
  1. st.secrets["postgres"]["url"]   (Streamlit deployment)
  2. $DATABASE_URL                    (systemd EnvironmentFile / .env)

RLS: every request that touches org-private tables must set the tenant context
with ``SET app.current_org_id`` so the policies in pilot_schema.sql filter rows
at the database layer (AC-10.1). Use :func:`org_connection` for that.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

try:  # psycopg 3 (added to pyproject for the pilot)
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - import guard for SQLite-only dev
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore


_ENV_LOADED = False


def _ensure_env_loaded() -> None:
    """Load .env once for headless callers (autopilot steps, cron scripts).

    The app process loads .env at startup, but bare scripts that import this
    module do NOT — so DATABASE_URL was invisible to them and Postgres read as
    "not reachable" (2026-08-09: run_pending_users reported an empty approval
    queue for exactly this reason; the same 2026-07-31 lesson, third time).
    Fixing it HERE means every current and future pg consumer inherits it,
    instead of each script having to remember. dotenv never overrides a var
    already set, so the app/tests are unaffected.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    with contextlib.suppress(Exception):
        from pathlib import Path

        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def database_url() -> str | None:
    """Resolve the Postgres URL from Streamlit secrets or the environment."""
    with contextlib.suppress(Exception):
        import streamlit as st  # local import: keep non-UI callers Streamlit-free

        if "postgres" in st.secrets and st.secrets["postgres"].get("url"):
            return str(st.secrets["postgres"]["url"])
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    _ensure_env_loaded()                       # headless fallback
    return os.environ.get("DATABASE_URL")


def is_configured() -> bool:
    """True when a Postgres URL is available and the driver is installed.

    When False the app stays on the deterministic SQLite path (Section 11:
    the core runs standalone), so the pilot can be brought up incrementally.
    """
    return psycopg is not None and bool(database_url())


_REACHABLE: bool | None = None


def is_reachable(timeout: float = 2.0) -> bool:
    """True when a Postgres URL is configured AND actually answers.

    `is_configured()` only says a URL exists. The pilot suites gate on that,
    so a machine with a URL pointing at a server that is down reported 76
    ERRORS rather than 76 skips - noise that buries a genuine failure and
    reads as "the tests are broken" rather than "the database is off".

    Cached: the answer cannot change usefully within one process, and probing
    per test would add a connection attempt to every one of them.
    """
    global _REACHABLE
    if _REACHABLE is not None:
        return _REACHABLE
    if not is_configured():
        _REACHABLE = False
        return _REACHABLE
    try:
        conn = psycopg.connect(database_url(), connect_timeout=int(timeout))
        conn.close()
        _REACHABLE = True
    except Exception:
        _REACHABLE = False
    return _REACHABLE


@contextlib.contextmanager
def connection() -> Iterator["psycopg.Connection[Any]"]:
    """A plain connection with dict rows. Caller manages the transaction."""
    if psycopg is None:
        raise RuntimeError("psycopg is not installed; `uv sync` to enable Postgres.")
    url = database_url()
    if not url:
        raise RuntimeError("No DATABASE_URL / [postgres].url configured.")
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def org_connection(org_id: str) -> Iterator["psycopg.Connection[Any]"]:
    """A connection scoped to one tenant for the life of the block.

    Sets ``app.current_org_id`` so the RLS policies in pilot_schema.sql make
    cross-org reads impossible at the DB layer (Section 10.1 / AC-10.1). Pass
    ``org_id`` as a parameter — never string-format it into SQL.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            # set_config() takes a bind parameter safely; plain `SET` does not.
            cur.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))
        yield conn


@contextlib.contextmanager
def user_connection(org_id: str, user_id: str) -> Iterator["psycopg.Connection[Any]"]:
    """A connection scoped to one tenant AND one user.

    Required for per-user-private tables (``inbox_messages``,
    ``mailbox_connections``): their RLS policies test BOTH
    ``app.current_org_id`` and ``app.current_user_id``, so a missing user
    context fails **closed** (zero rows) rather than leaking a colleague's mail.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))
            cur.execute("SELECT set_config('app.current_user_id', %s, false)", (user_id,))
        yield conn


def healthcheck() -> tuple[bool, str]:
    """Lightweight connectivity probe for the admin/status surface."""
    if not is_configured():
        return False, "Postgres not configured (running on SQLite)."
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("select version()")
            row = cur.fetchone()
            return True, str(row["version"]) if row else (True, "connected")
    except Exception as exc:  # pragma: no cover - surfaced in UI
        return False, f"Postgres error: {exc}"
