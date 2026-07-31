"""Copy the app's SQLite tables into Postgres, then prove the copy (P0.5).

The pilot's tenancy tables (users, orgs, deals, audit) already live in
Postgres via ``db/pilot_schema.sql``. The property spine and the calibration
tables do not — they are still SQLite in ``data/workbench.db``, which is a
single file that a blue-green service pair and the hourly autopilot all write
to. Moving them is the last item on build order 2.

This script is the BUILD-AND-PROVE half, deliberately not the cutover. It
creates the Postgres tables, copies every row, and then verifies the copy —
row counts, primary-key sets, and a column-by-column comparison of a sample.
It never drops or alters the SQLite side, and it does not change where the app
reads from: that stays ``config.SPINE_READ_SOURCE`` / ``ER_DB_BACKEND`` until
the verification passes on real data and the owner chooses to flip it. The
same order Phase 0 uses for the spine — build, prove parity, then cut over.

Usage:
    uv run python scripts/migrate_sqlite_to_pg.py            # migrate + verify
    uv run python scripts/migrate_sqlite_to_pg.py --verify   # verify only
    uv run python scripts/migrate_sqlite_to_pg.py --tables properties

Requires DATABASE_URL (or [postgres].url in Streamlit secrets).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import pg  # noqa: E402
from data.db import DB_PATH, get_connection  # noqa: E402

# Tables to move. The primary key is read from the SQLite schema rather than
# listed here: hard-coding it got calibration_current wrong ("metric" instead
# of "name") and the mistake only surfaced against a live Postgres.
TABLES: tuple[str, ...] = (
    "properties",
    "calibration_current",
    "calibration_history",
)

# SQLite is dynamically typed and these tables were created from a loader, not
# a strict schema, so the declared type is a hint rather than a guarantee.
# Postgres columns are widened accordingly: TEXT unless the values are
# reliably numeric, and no NOT NULL beyond the primary key.
_TYPE_MAP = {
    "INTEGER": "BIGINT",
    "REAL": "DOUBLE PRECISION",
    "NUMERIC": "DOUBLE PRECISION",
    "BOOLEAN": "BOOLEAN",
}


@dataclass
class TableReport:
    name: str
    sqlite_rows: int = 0
    pg_rows: int = 0
    missing_keys: list[str] = field(default_factory=list)
    extra_keys: list[str] = field(default_factory=list)
    value_mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.sqlite_rows == self.pg_rows
                and not self.missing_keys
                and not self.extra_keys
                and not self.value_mismatches)


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    return [(r[1], (r[2] or "TEXT").upper())
            for r in conn.execute(f"PRAGMA table_info({table})")]


def sqlite_primary_key(conn: sqlite3.Connection, table: str) -> str:
    """The declared primary key, from the schema itself.

    Composite keys are not supported here and none of these tables has one;
    raising is better than silently copying with no conflict target, which
    would let a re-run duplicate every row.
    """
    pks = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[5]]
    if len(pks) != 1:
        raise ValueError(
            f"{table}: expected exactly one primary-key column, found {pks!r}")
    return pks[0]


def _pg_type(sqlite_type: str) -> str:
    for key, pg_type in _TYPE_MAP.items():
        if sqlite_type.startswith(key):
            return pg_type
    return "TEXT"


def create_table(pg_conn, table: str, cols: list[tuple[str, str]], pk: str) -> None:
    defs = []
    for name, stype in cols:
        col = f'"{name}" {_pg_type(stype)}'
        if name == pk:
            col += " PRIMARY KEY"
        defs.append(col)
    ddl = f'CREATE TABLE IF NOT EXISTS "{table}" (\n  ' + ",\n  ".join(defs) + "\n)"
    with pg_conn.cursor() as cur:
        cur.execute(ddl)
    pg_conn.commit()


def copy_table(sq_conn: sqlite3.Connection, pg_conn, table: str,
               cols: list[tuple[str, str]], pk: str, batch: int = 500,
               sq_table: str | None = None) -> int:
    """Copy SQLite `sq_table` (default: same name) into Postgres `table`.

    The names are separate parameters only so the copy can be exercised
    against a scratch table; in the migration they are always the same.
    """
    sq_table = sq_table or table
    names = [c for c, _ in cols]
    quoted = ", ".join(f'"{c}"' for c in names)
    placeholders = ", ".join(["%s"] * len(names))
    # Upsert rather than insert: re-running after a partial failure must not
    # duplicate, and re-running after new rows land must not error.
    updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in names if c != pk)
    sql = (f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) '
           f'ON CONFLICT ("{pk}") DO UPDATE SET {updates}'
           if updates else
           f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) '
           f'ON CONFLICT ("{pk}") DO NOTHING')

    total = 0
    rows: list[tuple] = []
    with pg_conn.cursor() as cur:
        for row in sq_conn.execute(
                f'SELECT {quoted.replace(chr(34), "")} FROM "{sq_table}"'):
            rows.append(tuple(row[c] for c in names))
            if len(rows) >= batch:
                cur.executemany(sql, rows)
                total += len(rows)
                rows = []
        if rows:
            cur.executemany(sql, rows)
            total += len(rows)
    pg_conn.commit()
    return total


def verify_table(sq_conn: sqlite3.Connection, pg_conn, table: str,
                 cols: list[tuple[str, str]], pk: str,
                 sample: int = 200, sq_table: str | None = None) -> TableReport:
    """Counts, key sets, and a column-by-column sample comparison.

    Counts alone would pass a migration that copied the right number of wrong
    rows, so the keys and a sample of values are checked too.
    """
    sq_table = sq_table or table
    rep = TableReport(table)
    names = [c for c, _ in cols]

    rep.sqlite_rows = sq_conn.execute(
        f'SELECT COUNT(*) FROM "{sq_table}"').fetchone()[0]
    with pg_conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) AS n FROM "{table}"')
        rep.pg_rows = cur.fetchone()["n"]

        sq_keys = {str(r[0]) for r in
                   sq_conn.execute(f'SELECT "{pk}" FROM "{sq_table}"')}
        cur.execute(f'SELECT "{pk}" AS k FROM "{table}"')
        pg_keys = {str(r["k"]) for r in cur.fetchall()}
        rep.missing_keys = sorted(sq_keys - pg_keys)[:20]
        rep.extra_keys = sorted(pg_keys - sq_keys)[:20]

        quoted = ", ".join(f'"{c}"' for c in names)
        checked = 0
        for row in sq_conn.execute(
                f'SELECT {quoted.replace(chr(34), "")} FROM "{sq_table}" '
                f'ORDER BY "{pk}" LIMIT {sample}'):
            cur.execute(f'SELECT {quoted} FROM "{table}" WHERE "{pk}" = %s', (row[pk],))
            got = cur.fetchone()
            if got is None:
                rep.value_mismatches.append(f"{row[pk]}: absent in Postgres")
                continue
            for c in names:
                a, b = row[c], got[c]
                if a is None and b is None:
                    continue
                # SQLite stores ints/floats loosely; compare numerically when
                # both sides look like numbers, textually otherwise.
                try:
                    if abs(float(a) - float(b)) < 1e-6:
                        continue
                except (TypeError, ValueError):
                    pass
                if str(a) != str(b):
                    rep.value_mismatches.append(f"{row[pk]}.{c}: {a!r} != {b!r}")
            checked += 1
            if len(rep.value_mismatches) > 20:
                break
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="verify an existing copy without writing")
    ap.add_argument("--tables", nargs="*", default=None,
                    help="subset of tables (default: all)")
    ap.add_argument("--sqlite", type=Path, default=DB_PATH)
    args = ap.parse_args()

    if not pg.is_configured():
        print("Postgres is not configured (set DATABASE_URL or "
              "[postgres].url). Nothing done.")
        return 2
    if not Path(args.sqlite).is_file():
        print(f"No SQLite database at {args.sqlite}. Nothing done.")
        return 2

    wanted = args.tables or list(TABLES)
    unknown = [t for t in wanted if t not in TABLES]
    if unknown:
        print(f"Unknown table(s): {', '.join(unknown)}")
        return 2

    reports: list[TableReport] = []
    with get_connection(Path(args.sqlite)) as sq, pg.connection() as pgc:
        present = {r[0] for r in sq.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in wanted:
            if table not in present:
                print(f"[skip] {table}: not in this SQLite database")
                continue
            pk = sqlite_primary_key(sq, table)
            cols = sqlite_columns(sq, table)
            if not args.verify:
                create_table(pgc, table, cols, pk)
                n = copy_table(sq, pgc, table, cols, pk)
                print(f"[copy] {table}: {n:,} rows")
            rep = verify_table(sq, pgc, table, cols, pk)
            reports.append(rep)
            status = "OK" if rep.ok else "MISMATCH"
            print(f"[check] {table}: sqlite={rep.sqlite_rows:,} "
                  f"pg={rep.pg_rows:,} -> {status}")
            for label, items in (("missing in pg", rep.missing_keys),
                                 ("extra in pg", rep.extra_keys),
                                 ("value differs", rep.value_mismatches)):
                for item in items[:5]:
                    print(f"          {label}: {item}")

    bad = [r for r in reports if not r.ok]
    print()
    if bad:
        print(f"VERIFICATION FAILED for {len(bad)} table(s): "
              f"{', '.join(r.name for r in bad)}")
        print("The app still reads SQLite - nothing was cut over.")
        return 1
    print(f"All {len(reports)} table(s) verified identical in Postgres.")
    print("The app still reads SQLite. Cutover is a separate, deliberate step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
