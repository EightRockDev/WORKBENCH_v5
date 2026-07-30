"""P0-3 cutover helpers (spec 7.3): migrate deal references legacy -> 8R.

The pilot's `deals.property_id` (and `outreach_touches.property_id`) are
bare text columns holding LEGACY vendor UUIDs today; `poc_records` is
already 8R-keyed. At flip time every legacy reference must rewrite to
its 8R backbone id via the persisted `property_crosswalk` table.

Rules, in order of importance:
  * NEVER guess. An id the crosswalk cannot map is counted and left
    untouched - a deal pointing at a legacy id still resolves through
    the read seam's crosswalk join, so unmapped is degraded, not broken.
  * Idempotent. `8R-`-prefixed values are recognized and skipped, so
    running twice (or after a partial failure) is safe.
  * Driver-agnostic. Works on sqlite3 and psycopg connections via the
    `placeholder` argument ("?" vs "%s"). The caller owns the commit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# (table, column) pairs that reference property ids in the pilot schema.
DEFAULT_REFERENCE_COLUMNS = (
    ("deals", "property_id"),
    ("outreach_touches", "property_id"),
)


def load_crosswalk(spine_db: Path) -> dict[str, str]:
    """legacy property id -> 8R id, from the table parity persists."""
    try:
        with sqlite3.connect(spine_db) as conn:
            return dict(conn.execute(
                "SELECT legacy_property_id, r8_property_id "
                "  FROM property_crosswalk"))
    except sqlite3.Error:
        return {}


@dataclass
class MigrationResult:
    updated: dict[str, int] = field(default_factory=dict)
    already_8r: dict[str, int] = field(default_factory=dict)
    unmapped: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> str:
        lines = []
        for table in self.updated:
            miss = self.unmapped.get(table, [])
            lines.append(
                f"{table}: {self.updated[table]} migrated, "
                f"{self.already_8r[table]} already 8R, "
                f"{len(miss)} unmapped (left as-is)")
            lines.extend(f"    unmapped: {m}" for m in miss[:10])
        return "\n".join(lines) or "no reference tables found"


def migrate_deal_references(
    conn,
    crosswalk: dict[str, str],
    tables: tuple = DEFAULT_REFERENCE_COLUMNS,
    placeholder: str = "?",
    dry_run: bool = False,
) -> MigrationResult:
    """Rewrite legacy property ids in place. Caller commits (or rolls
    back a dry run). Missing tables are skipped silently so the same
    call works against SQLite pilots and the Postgres pilot schema."""
    result = MigrationResult()
    for table, col in tables:
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {col} FROM {table} "
                f"WHERE {col} IS NOT NULL").fetchall()
        except Exception:
            continue   # table absent in this database
        ids = [r[0] for r in rows if r[0]]
        result.updated[table] = 0
        result.already_8r[table] = 0
        result.unmapped[table] = []
        for pid in ids:
            if str(pid).startswith("8R-"):
                result.already_8r[table] += 1
                continue
            r8_id = crosswalk.get(pid)
            if r8_id is None:
                result.unmapped[table].append(str(pid))
                continue
            if not dry_run:
                conn.execute(
                    f"UPDATE {table} SET {col} = {placeholder} "
                    f"WHERE {col} = {placeholder}", (r8_id, pid))
            result.updated[table] += 1
    return result
