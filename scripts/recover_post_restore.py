"""Merge back the rows the 2026-08-27 restore overwrote.

Sequence of events this exists to finish cleaning up:

  * 2026-08-18  `uv run pytest` on the host truncated the live pilot tables.
  * 2026-08-18..27  the app and the autopilot kept working against the
    emptied database, so NEW rows accumulated on top of the damage -
    inbox mail synced, contacts and activity were written.
  * 2026-08-27  the Aug 18 backup was restored in place with
    `pg_restore --clean`, which recreated each table and therefore
    DISCARDED that post-wipe work (inbox_messages went 83 -> 81).

So the good data is split across two artifacts: the Aug 18 accounts and
deals now live in the database, and the post-wipe rows live only in the
pre-restore snapshot the restore script took first. This reunites them.

Read-only by default: it prints exactly what it would insert and why, and
writes nothing until `--apply`.

Two things make this more than a diff:

1. **The ids moved.** The post-wipe rows point at the accounts and
   organizations that existed AFTER the wipe - Brian's re-bootstrapped
   user id, the org created on Aug 18 at 11:44. Those ids are gone; the
   restore brought back the ORIGINAL ones. Inserting the rows unchanged
   would fail every foreign key. So user and org references are remapped
   by EMAIL and by NAME, which are stable across the re-creation.
2. **Only genuinely-new rows.** A row whose primary key already exists is
   skipped, never overwritten - the restored Aug 18 version wins. This
   adds what was lost; it does not re-litigate what was recovered.

Usage (on the host, from C:\\WORKBENCH_V5):

    uv run python scripts/recover_post_restore.py --snapshot D:\\Backup\\8rw\\pre-restore-<stamp>.dump
    uv run python scripts/recover_post_restore.py --snapshot ... --apply
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# `python scripts/foo.py` puts scripts/ (not the repo root) on sys.path, so
# `from data import pg` fails on the host. Every autopilot script carries
# this bootstrap; this one shipped without it (owner hit it 2026-09-01).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Order matters: parents before children, so an inserted row's references
# already exist. organizations/users themselves are NOT merged - the
# restored Aug 18 identities are authoritative and the remap points at them.
MERGE_TABLES = (
    "deals",
    "crm_contacts",
    "poc_records",
    "inbox_messages",
    "outreach_touches",
    "user_property_overrides",
    "property_activity",
    "term_sheets",
    "mailbox_connections",
    "relationship_edges",
    "campaigns",
    "api_keys",
)

USER_FK_COLUMNS = ("user_id", "owner_user_id", "actor_user_id",
                   "created_by", "invited_by")
ORG_FK_COLUMNS = ("org_id",)


def find_pg_restore() -> str | None:
    hit = shutil.which("pg_restore")
    if hit:
        return hit
    for ver in ("17", "16", "15", "14"):
        cand = Path(f"C:\\Program Files\\PostgreSQL\\{ver}\\bin\\pg_restore.exe")
        if cand.is_file():
            return str(cand)
    return None


# ---------------------------------------------------------------------------
# Reading a table out of a custom-format dump, without a database
# ---------------------------------------------------------------------------

def _unescape(v: str):
    r"""COPY text format -> Python. `\N` is NULL, and the escapes are the
    small fixed set COPY emits."""
    if v == r"\N":
        return None
    return (v.replace(r"\r", "\r").replace(r"\n", "\n")
             .replace(r"\t", "\t").replace("\\\\", "\\"))


def read_table(pg_restore: str, dump: Path, table: str
               ) -> tuple[list[str], list[list]]:
    """Return (columns, rows) for one table straight out of the archive."""
    # encoding pinned: pg_restore emits UTF-8, but text=True on Windows
    # decodes with cp1252, and one curly quote (byte 0x9d) in an email
    # body killed the reader thread - which silently dropped
    # inbox_messages, the exact table the owner wanted back (2026-09-01).
    proc = subprocess.run(
        [pg_restore, "--data-only", "-t", table, "-f", "-", str(dump)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    m = re.search(r"^COPY [^(]*\(([^)]*)\) FROM stdin;$", out, re.M)
    if not m:
        return [], []
    columns = [c.strip().strip('"') for c in m.group(1).split(",")]
    rows: list[list] = []
    started = False
    for line in out.splitlines():
        if not started:
            if line.startswith("COPY ") and line.endswith("FROM stdin;"):
                started = True
            continue
        if line == "\\.":
            break
        rows.append([_unescape(v) for v in line.split("\t")])
    return columns, rows


# ---------------------------------------------------------------------------
# Remapping identities that were re-created between snapshot and restore
# ---------------------------------------------------------------------------

def build_identity_map(pg_restore: str, dump: Path, conn) -> tuple[dict, dict, list[str]]:
    """id-in-snapshot -> id-now, for users (by email) and orgs (by name)."""
    notes: list[str] = []
    user_map: dict[str, str] = {}
    org_map: dict[str, str] = {}

    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users")
        live_users = {r["email"].lower(): str(r["id"]) for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM organizations")
        live_orgs = {(r["name"] or "").lower(): str(r["id"]) for r in cur.fetchall()}

    cols, rows = read_table(pg_restore, dump, "users")
    if cols:
        i_id, i_email = cols.index("id"), cols.index("email")
        for r in rows:
            snap_id, email = r[i_id], (r[i_email] or "").lower()
            live_id = live_users.get(email)
            if live_id:
                user_map[snap_id] = live_id
                if snap_id != live_id:
                    notes.append(f"user {email}: {snap_id[:8]} -> {live_id[:8]}")
            else:
                notes.append(f"user {email or snap_id}: NO match in the live "
                             f"database - its rows will be skipped")

    cols, rows = read_table(pg_restore, dump, "organizations")
    if cols:
        i_id, i_name = cols.index("id"), cols.index("name")
        for r in rows:
            snap_id, name = r[i_id], (r[i_name] or "").lower()
            live_id = live_orgs.get(name)
            if live_id:
                org_map[snap_id] = live_id
                if snap_id != live_id:
                    notes.append(f"org {name!r}: {snap_id[:8]} -> {live_id[:8]}")
            else:
                notes.append(f"org {name!r}: NO match in the live database - "
                             f"its rows will be skipped")
    return user_map, org_map, notes


def remap_row(columns: list[str], row: list, user_map: dict, org_map: dict
              ) -> tuple[list | None, str]:
    """Point a snapshot row at the identities that exist now."""
    out = list(row)
    for i, col in enumerate(columns):
        val = row[i]
        if val is None:
            continue
        if col in USER_FK_COLUMNS:
            if val in user_map:
                out[i] = user_map[val]
            else:
                return None, f"unknown user reference in {col}"
        elif col in ORG_FK_COLUMNS:
            if val in org_map:
                out[i] = org_map[val]
            else:
                return None, f"unknown org reference in {col}"
    return out, ""


def row_context(columns: list[str], row: list) -> tuple[str | None, str | None]:
    """(org_id, user_id) a row belongs to, read off the row itself.

    The org comes from org_id; the user from the row's ownership column
    (user_id or owner_user_id - actor_user_id is an audit reference, not
    ownership, and must not impersonate a tenant context).
    """
    org = user = None
    for i, col in enumerate(columns):
        if col == "org_id" and row[i]:
            org = str(row[i])
        elif col in ("user_id", "owner_user_id") and row[i] and user is None:
            user = str(row[i])
    return org, user


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", required=True,
                    help="the pre-restore-*.dump written by restore-pilot-db.ps1")
    ap.add_argument("--apply", action="store_true",
                    help="actually insert (default: report only)")
    args = ap.parse_args()

    dump = Path(args.snapshot)
    if not dump.is_file():
        print(f"snapshot not found: {dump}")
        return 1

    pg_restore = find_pg_restore()
    if pg_restore is None:
        print("pg_restore not found - install the PostgreSQL client tools.")
        return 1

    from data import pg
    # The BYPASSRLS reader, or FORCE RLS hides the very rows being compared
    # and every table reads as empty (the 2026-08-27 miscount).
    url = (os.environ.get("ER_BACKUP_DATABASE_URL", "").strip()
           or pg.database_url() or "")
    if not url:
        print("no DATABASE_URL / ER_BACKUP_DATABASE_URL configured.")
        return 1

    import psycopg
    from psycopg.rows import dict_row

    read_conn = psycopg.connect(url, row_factory=dict_row)
    try:
        user_map, org_map, notes = build_identity_map(pg_restore, dump, read_conn)

        print(f"snapshot : {dump}")
        print(f"mode     : {'APPLY' if args.apply else 'REPORT ONLY (nothing written)'}")
        print()
        if notes:
            print("identity remapping (accounts were re-created by the restore):")
            for n in notes:
                print(f"  {n}")
            print()

        plan: dict[str, tuple[list[str], list[list]]] = {}
        skipped: dict[str, dict[str, int]] = {}

        print(f"{'table':<26}{'in snapshot':>12}{'already live':>14}{'to add':>9}")
        for table in MERGE_TABLES:
            cols, rows = read_table(pg_restore, dump, table)
            if not cols or not rows:
                continue
            if "id" not in cols:
                # No single-column key to compare on; skip rather than guess
                # and risk duplicating rows.
                skipped.setdefault(table, {})["no id column"] = len(rows)
                continue
            i_id = cols.index("id")
            with read_conn.cursor() as cur:
                cur.execute(f"SELECT id FROM {table}")
                live_ids = {str(r["id"]) for r in cur.fetchall()}

            add_cols, add_rows = cols, []
            already = 0
            for r in rows:
                if r[i_id] in live_ids:
                    already += 1
                    continue
                mapped, why = remap_row(cols, r, user_map, org_map)
                if mapped is None:
                    skipped.setdefault(table, {})
                    skipped[table][why] = skipped[table].get(why, 0) + 1
                    continue
                add_rows.append(mapped)
            if add_rows:
                plan[table] = (add_cols, add_rows)
            print(f"{table:<26}{len(rows):>12}{already:>14}{len(add_rows):>9}")

        total = sum(len(r) for _c, r in plan.values())
        print()
        if skipped:
            print("not recoverable:")
            for table, reasons in skipped.items():
                for why, n in reasons.items():
                    print(f"  {table}: {n} row(s) - {why}")
            print()

        if total == 0:
            print("Nothing to merge - the live database already has every row "
                  "in the snapshot.")
            return 0

        print(f"{total} row(s) would be added. Existing rows are never "
              f"overwritten.")
        if not args.apply:
            print()
            print("Re-run with --apply to insert them.")
            return 0
    finally:
        read_conn.close()

    # --- write ---------------------------------------------------------
    # A single transaction: all of it lands or none of it does. Inserts use
    # ON CONFLICT DO NOTHING as a second guard against the race where a row
    # appeared between the read above and this write.
    #
    # Row-level security applies to WRITES too: every org table has an
    # org_isolation WITH CHECK, and the per-user tables (inbox_messages,
    # user_property_overrides, mailbox_connections) additionally check
    # current_user_id(). The first --apply run died on exactly this
    # ("new row violates row-level security policy for table deals",
    # 2026-09-01) because the connection carried no tenant context. So:
    # before each insert, declare WHOSE row this is, from the row itself,
    # with the same GUCs the app sets (data/pg.py org/user_connection).
    write_url = pg.database_url() or url
    inserted = 0
    with psycopg.connect(write_url) as conn:
        with conn.cursor() as cur:
            for table, (cols, rows) in plan.items():
                collist = ", ".join(f'"{c}"' for c in cols)
                marks = ", ".join(["%s"] * len(cols))
                sql = (f'INSERT INTO {table} ({collist}) VALUES ({marks}) '
                       f'ON CONFLICT DO NOTHING')
                for r in rows:
                    org_ctx, user_ctx = row_context(cols, r)
                    cur.execute(
                        "SELECT set_config('app.current_org_id', %s, false),"
                        "       set_config('app.current_user_id', %s, false)",
                        (org_ctx or "", user_ctx or ""))
                    cur.execute(sql, r)
                    inserted += cur.rowcount
        conn.commit()

    print()
    print(f"merged {inserted} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
