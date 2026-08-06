"""Automatic schema migration on startup — eliminates code/schema drift.

`db/pilot_schema.sql` is fully idempotent, so the safe, simple contract is:

    if the live database is missing anything the current code expects,
    apply the schema file once, then continue.

This removes an entire class of failure for a non-technical operator: pulling
new code that expects a new column can no longer produce a raw
``UndefinedColumn`` traceback. The check is a handful of catalog lookups
(microseconds) and runs once per process.

Safety:
  * Only ever runs `db/pilot_schema.sql`, which is CREATE IF NOT EXISTS /
    ADD COLUMN IF NOT EXISTS / idempotent DO-blocks. It never drops user data.
  * If migration fails, the error is returned to the caller so the app can show
    an actionable banner instead of crashing.
"""

from __future__ import annotations

import pathlib
import threading

from data import pg

_LOCK = threading.Lock()
_CHECKED = False

SCHEMA_FILE = pathlib.Path(__file__).resolve().parent.parent / "db" / "pilot_schema.sql"

# (table, column) pairs the CURRENT code requires. Add to this list whenever a
# migration introduces a column the app reads or writes.
REQUIRED_COLUMNS: list[tuple[str, str]] = [
    ("inbox_messages", "owner_user_id"),
    ("poc_records", "portfolio_id"),
    ("outreach_touches", "rule_trace"),
    ("deals", "asking_price"),
]
REQUIRED_TABLES: list[str] = [
    "organizations", "users", "memberships", "role_presets", "poc_records",
    "consent_records", "revocations", "internal_dnc", "dnc_scrubs",
    "outreach_touches", "campaigns", "relationship_edges",
    "inbox_messages", "deals", "term_sheets", "crm_contacts",
    "mailbox_connections", "user_property_overrides",
]
# Indexes that ENFORCE a correctness rule, not just speed one up. Missing ones
# are real drift: `ux_term_sheets_message` is what stops a repeated Sync from
# duplicating term sheets, and `ux_inbox_owner_msg` is the per-user idempotency
# key. A dropped-or-never-created index here is silent data corruption.
REQUIRED_INDEXES: list[str] = [
    "ux_term_sheets_message",
    "ux_inbox_owner_msg",
]


def schema_is_current() -> tuple[bool, list[str]]:
    """(ok, missing) — what the live database lacks versus what the code needs."""
    missing: list[str] = []
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT table_name FROM information_schema.tables
                        WHERE table_schema='public'""")
        have_tables = {r["table_name"] for r in cur.fetchall()}
        for t in REQUIRED_TABLES:
            if t not in have_tables:
                missing.append(f"table {t}")
        cur.execute("""SELECT table_name, column_name FROM information_schema.columns
                        WHERE table_schema='public'""")
        have_cols = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
        for t, c in REQUIRED_COLUMNS:
            if t in have_tables and (t, c) not in have_cols:
                missing.append(f"{t}.{c}")
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        have_idx = {r["indexname"] for r in cur.fetchall()}
        for ix in REQUIRED_INDEXES:
            if ix not in have_idx:
                missing.append(f"index {ix}")
    return (not missing), missing


def apply_schema() -> None:
    """Run the idempotent schema file against the live database."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    with pg.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def ensure_schema(force: bool = False) -> tuple[bool, str]:
    """Migrate if the schema is behind. Returns (ok, message).

    Runs at most once per process unless ``force``. Never raises: a failure is
    returned so the caller can surface it as a banner.
    """
    global _CHECKED
    if not pg.is_configured():
        return True, "Postgres not configured; nothing to migrate."
    with _LOCK:
        if _CHECKED and not force:
            return True, "already checked this process"
        try:
            ok, missing = schema_is_current()
            if ok:
                _CHECKED = True
                return True, "schema current"
            apply_schema()
            ok2, still = schema_is_current()
            _CHECKED = ok2
            if ok2:
                return True, f"schema migrated automatically ({len(missing)} item(s) added)"
            return False, ("Automatic migration ran but the schema is still behind: "
                           + ", ".join(still))
        except Exception as exc:      # pragma: no cover - surfaced in the UI
            return False, (f"Automatic migration failed: {exc}\n\n"
                           "Run deploy\\windows\\migrate-db.ps1 manually.")
