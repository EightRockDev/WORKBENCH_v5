"""Optimistic concurrency + presence soft-locks over PostgreSQL (Section 9.3).

Moving to Postgres removes the SQLite single-writer limit, but a naive save is
still "last write wins" — if User A saves, then User B saves the copy they
opened earlier, A's edits are silently lost. This module adds the explicit
functionality the spec requires so that never happens:

  FR-9.3.1  Optimistic concurrency — every editable record carries a
            ``row_version``; saves compare-and-set on it. A stale save updates
            zero rows and is reported as a conflict instead of applied.
  FR-9.3.2  Conflict resolution — on mismatch we return who changed the record
            and when, so the UI can show Reload / Review / Overwrite.
  FR-9.3.3  Presence & soft locks — a short-lived, heartbeat-refreshed advisory
            lock (TTL ~5 min) drives the "🔒 Jane is editing" banner. Read-only
            viewing is never blocked.

``row_version`` is auto-incremented by the ``bump_row_version()`` trigger in
db/pilot_schema.sql, so callers never set it themselves — they only pass the
version they loaded, as the guard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Editable tables must be registered here so we never interpolate an arbitrary
# identifier into SQL. Column names are validated against this same rule.
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class SaveResult:
    ok: bool
    new_version: int | None = None
    # Populated only on conflict (ok=False), for the FR-9.3.2 dialog:
    current_version: int | None = None
    changed_by: str | None = None
    changed_at: Any | None = None


def optimistic_update(conn, table: str, record_id: str, expected_version: int,
                      values: dict[str, Any]) -> SaveResult:
    """Compare-and-set save (FR-9.3.1). Returns a conflict instead of clobbering.

    ``conn`` is a caller-managed connection; for RLS-protected tables the caller
    must already have set ``app.current_org_id`` (use data.pg.org_connection).
    """
    if not _IDENT.match(table):
        raise ValueError(f"illegal table identifier: {table!r}")
    cols = list(values)
    for c in cols:
        if not _IDENT.match(c):
            raise ValueError(f"illegal column identifier: {c!r}")
    if not cols:
        raise ValueError("no columns to update")

    set_clause = ", ".join(f"{c} = %s" for c in cols)
    params = [values[c] for c in cols] + [record_id, expected_version]
    with conn.cursor() as cur:
        # The bump_row_version() trigger increments row_version + updated_at.
        cur.execute(
            f"""UPDATE {table} SET {set_clause}
                 WHERE id = %s AND row_version = %s
             RETURNING row_version""",
            params,
        )
        row = cur.fetchone()
        if row is not None:
            conn.commit()
            return SaveResult(ok=True, new_version=row["row_version"])

        # Zero rows updated: either the record vanished or the version moved.
        cur.execute(
            f"SELECT row_version, updated_at FROM {table} WHERE id = %s", (record_id,))
        cur2 = cur.fetchone()
        conn.rollback()
        if cur2 is None:
            return SaveResult(ok=False, current_version=None, changed_by="(deleted)")
        return SaveResult(
            ok=False,
            current_version=cur2["row_version"],
            changed_at=cur2.get("updated_at"),
        )


# ---------------------------------------------------------------------------
# Presence / advisory soft locks (FR-9.3.3). edit_locks is RLS-protected, so
# these open their own tenant-scoped connection via data.pg.org_connection.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LockState:
    held: bool
    by_user_id: str | None = None
    since: Any | None = None
    mine: bool = False


def acquire_or_refresh_lock(org_id: str, kind: str, record_id: str, user_id: str,
                            ttl_minutes: int = 5) -> LockState:
    """Take the soft lock, or refresh it if we already hold it.

    If another *unexpired* user holds it, we do NOT steal it — we report who has
    it so the UI can show the presence banner. Expired locks are reclaimable.
    """
    from data import pg

    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO edit_locks (org_id, record_kind, record_id, user_id,
                                       heartbeat_at, expires_at)
               VALUES (%s, %s, %s, %s, now(), now() + make_interval(mins => %s))
               ON CONFLICT (org_id, record_kind, record_id) DO UPDATE
                 SET user_id      = EXCLUDED.user_id,
                     heartbeat_at = now(),
                     expires_at   = now() + make_interval(mins => %s)
                 WHERE edit_locks.user_id = EXCLUDED.user_id     -- refresh my own
                    OR edit_locks.expires_at < now()             -- or reclaim expired
            RETURNING user_id, acquired_at""",
            (org_id, kind, record_id, user_id, ttl_minutes, ttl_minutes),
        )
        row = cur.fetchone()
        if row is not None:
            conn.commit()
            return LockState(held=True, by_user_id=str(row["user_id"]),
                             since=row["acquired_at"], mine=str(row["user_id"]) == user_id)
        # Someone else holds an unexpired lock — report them, don't block reads.
        cur.execute(
            """SELECT user_id, acquired_at FROM edit_locks
                WHERE org_id=%s AND record_kind=%s AND record_id=%s""",
            (org_id, kind, record_id))
        holder = cur.fetchone()
        conn.rollback()
        if holder is None:
            return LockState(held=False)
        return LockState(held=True, by_user_id=str(holder["user_id"]),
                         since=holder["acquired_at"], mine=False)


def release_lock(org_id: str, kind: str, record_id: str, user_id: str) -> None:
    from data import pg

    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """DELETE FROM edit_locks
                WHERE org_id=%s AND record_kind=%s AND record_id=%s AND user_id=%s""",
            (org_id, kind, record_id, user_id))
        conn.commit()


def current_holder(org_id: str, kind: str, record_id: str) -> LockState:
    """Who, if anyone, is currently editing (for the presence banner)."""
    from data import pg

    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT user_id, acquired_at FROM edit_locks
                WHERE org_id=%s AND record_kind=%s AND record_id=%s AND expires_at > now()""",
            (org_id, kind, record_id))
        row = cur.fetchone()
        if row is None:
            return LockState(held=False)
        return LockState(held=True, by_user_id=str(row["user_id"]), since=row["acquired_at"])
