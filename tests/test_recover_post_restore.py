"""The post-restore merge must add what was lost and touch nothing else.

Context: the 2026-08-27 in-place restore brought back the Aug 18 accounts
and deals, but `pg_restore --clean` discarded the rows written between the
2026-08-18 wipe and the restore (inbox_messages 83 -> 81). Those rows exist
only in the pre-restore snapshot, and they point at identities the restore
replaced — Brian's re-bootstrapped user id, the org created after the wipe.

The parsing and remapping are pure functions precisely so they can be
tested without a Postgres server; that is the same split that let
core/screener.py be tested honestly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "recover_post_restore",
    Path(__file__).resolve().parent.parent / "scripts" / "recover_post_restore.py")
rpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rpr)


# The ids from the real incident, so the fixtures read like the event.
SNAP_BRIAN = "200d51ca-3921-4906-837e-8f179c5edcc2"   # post-wipe bootstrap
LIVE_BRIAN = "ed0c71a7-4a1a-4fbf-a4e3-c5e9e3550e9c"   # restored Aug 18 record
SNAP_ORG = "5df1b2fd-393c-44ac-a075-01f19fccc232"
LIVE_ORG = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture()
def maps():
    return {SNAP_BRIAN: LIVE_BRIAN}, {SNAP_ORG: LIVE_ORG}


# ---------------------------------------------------------------------------
# COPY parsing
# ---------------------------------------------------------------------------

def test_null_and_escapes_round_trip():
    r"""COPY writes NULL as \N and escapes tabs/newlines. Treating \N as the
    literal string would write the text "\N" into a restored row."""
    assert rpr._unescape(r"\N") is None
    assert rpr._unescape("plain") == "plain"
    assert rpr._unescape(r"two\tcols") == "two\tcols"
    assert rpr._unescape(r"line\nbreak") == "line\nbreak"
    assert rpr._unescape(r"back\\slash") == "back\\slash"


def test_read_table_parses_a_dump_section(monkeypatch):
    payload = (
        "--\n-- PostgreSQL database dump\n--\n\n"
        "SET statement_timeout = 0;\n\n"
        "COPY public.inbox_messages (id, org_id, subject, body) FROM stdin;\n"
        "m1\t" + SNAP_ORG + "\tOffering memo\tsee attached\n"
        "m2\t" + SNAP_ORG + "\tRe: 611 Michigan\t\\N\n"
        "\\.\n\n\n"
        "-- PostgreSQL database dump complete\n")

    class _Proc:
        stdout = payload
        returncode = 0

    monkeypatch.setattr(rpr.subprocess, "run", lambda *a, **k: _Proc())
    cols, rows = rpr.read_table("pg_restore", Path("x.dump"), "inbox_messages")

    assert cols == ["id", "org_id", "subject", "body"]
    assert len(rows) == 2, "the terminator or the header leaked into the rows"
    assert rows[0] == ["m1", SNAP_ORG, "Offering memo", "see attached"]
    assert rows[1][3] is None


def test_a_table_absent_from_the_dump_yields_nothing(monkeypatch):
    class _Proc:
        stdout = "--\n-- nothing here\n--\n"
        returncode = 1

    monkeypatch.setattr(rpr.subprocess, "run", lambda *a, **k: _Proc())
    assert rpr.read_table("pg_restore", Path("x.dump"), "deals") == ([], [])


# ---------------------------------------------------------------------------
# Identity remapping — the reason a plain diff would fail
# ---------------------------------------------------------------------------

def test_user_and_org_references_are_rewritten(maps):
    user_map, org_map = maps
    cols = ["id", "org_id", "owner_user_id", "subject"]
    row = ["m1", SNAP_ORG, SNAP_BRIAN, "Offering memo"]

    out, why = rpr.remap_row(cols, row, user_map, org_map)
    assert why == ""
    assert out == ["m1", LIVE_ORG, LIVE_BRIAN, "Offering memo"], (
        "the row still points at identities the restore deleted - every "
        "insert would fail its foreign key")


def test_rows_referencing_a_vanished_identity_are_skipped_not_guessed(maps):
    """A post-wipe row belonging to an account with no restored counterpart
    has nowhere to go. Dropping the reference would silently reassign
    someone's work; skipping and reporting is the honest answer."""
    user_map, org_map = maps
    cols = ["id", "org_id", "user_id"]
    row = ["x1", SNAP_ORG, "99999999-0000-0000-0000-000000000000"]

    out, why = rpr.remap_row(cols, row, user_map, org_map)
    assert out is None
    assert "user" in why


def test_null_references_are_left_alone(maps):
    """audit_log.actor_user_id is nullable — a NULL is not a broken link."""
    user_map, org_map = maps
    cols = ["id", "org_id", "actor_user_id"]
    out, why = rpr.remap_row(cols, ["a1", SNAP_ORG, None], user_map, org_map)
    assert why == "" and out == ["a1", LIVE_ORG, None]


def test_every_user_foreign_key_column_is_covered():
    """A column this list forgets is a column that keeps a dead id and
    fails at insert time, mid-merge."""
    schema = (Path(__file__).resolve().parent.parent
              / "db" / "pilot_schema.sql").read_text(encoding="utf-8")
    referencing = set()
    for line in schema.splitlines():
        m = __import__("re").match(
            r"\s*(\w+)\s+uuid[^,]*REFERENCES\s+users\(", line)
        if m:
            referencing.add(m.group(1))
    missed = referencing - set(rpr.USER_FK_COLUMNS)
    assert not missed, (
        f"these columns reference users(id) but are never remapped: {missed}")


def test_organizations_and_users_are_not_themselves_merged():
    """The restored Aug 18 identities are authoritative. Re-inserting the
    post-wipe user/org rows would resurrect the duplicate accounts the
    remap exists to avoid — and collide on the UNIQUE email."""
    assert "users" not in rpr.MERGE_TABLES
    assert "organizations" not in rpr.MERGE_TABLES
    assert "memberships" not in rpr.MERGE_TABLES


def test_parents_are_merged_before_children():
    """deals and crm_contacts are referenced by later tables; inserting a
    child first fails its foreign key."""
    order = list(rpr.MERGE_TABLES)
    assert order.index("deals") < order.index("term_sheets")
    assert order.index("crm_contacts") < order.index("relationship_edges")


def test_report_only_is_the_default():
    """The owner asked for read-only until he approves. Assert the flag
    exists and that nothing writes without it."""
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "recover_post_restore.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--apply", action="store_true"' in src
    apply_gate = src.index("if not args.apply:")
    # Anchor on the write CONNECTION, not the text "INSERT INTO" - the
    # _insert_sql helper legitimately contains that string above the gate.
    first_write = src.index("psycopg.connect(write_url)")
    assert apply_gate < first_write, (
        "the write path is reachable without --apply")


def test_inserts_never_overwrite_an_existing_row():
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "recover_post_restore.py").read_text(encoding="utf-8")
    assert "ON CONFLICT DO NOTHING" in src, (
        "a merge that can overwrite is a second restore, not a merge")


def test_it_reads_through_the_bypassrls_role():
    """FORCE RLS hides rows from the app role, which is what made the
    2026-08-27 damage table under-report. A comparison that cannot see the
    rows would 'recover' rows that are already there."""
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "recover_post_restore.py").read_text(encoding="utf-8")
    assert "ER_BACKUP_DATABASE_URL" in src


def test_the_script_runs_from_the_repo_root_like_the_owner_runs_it():
    """`uv run python scripts/recover_post_restore.py` on the host failed
    with ModuleNotFoundError: data - scripts/ was on sys.path, the repo
    root was not. Run it exactly that way and require a clean argparse
    failure, not an import crash."""
    import subprocess
    import sys as _sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [_sys.executable, "scripts/recover_post_restore.py",
         "--snapshot", "does-not-exist.dump"],
        cwd=str(root), capture_output=True, text=True, timeout=120)

    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr[-400:]
    assert "snapshot not found" in proc.stdout, (
        f"expected the friendly missing-file message, got:\n"
        f"{proc.stdout[-200:]}{proc.stderr[-200:]}")


def test_utf8_content_survives_a_cp1252_locale(tmp_path, monkeypatch):
    """On the host, an email body with byte 0x9d (a Windows curly quote)
    crashed the cp1252 reader thread and inbox_messages vanished from the
    report without an error. The read must be pinned to UTF-8."""
    import subprocess as sp
    import sys as _sys

    # A stand-in pg_restore that emits a COPY block containing the exact
    # byte sequence that broke: written raw to stdout, no encoding help.
    stub = tmp_path / "fake_pg_restore.py"
    stub.write_text(
        "import sys\n"
        "out = sys.stdout.buffer\n"
        "out.write(b'COPY public.inbox_messages (id, body) FROM stdin;\\n')\n"
        "out.write(b'm1\\t' + 'offering \\u2019memo\\u2019'.encode('utf-8')"
        " + b'\\n')\n"
        "out.write(b'\\\\.\\n')\n", encoding="utf-8")

    real_run = sp.run

    def run_via_python(cmd, **kw):
        return real_run([_sys.executable, str(stub)] + list(cmd[1:]), **kw)

    monkeypatch.setattr(rpr.subprocess, "run", run_via_python)
    cols, rows = rpr.read_table("ignored", tmp_path / "x.dump",
                                "inbox_messages")

    assert cols == ["id", "body"], "the UTF-8 COPY block failed to parse"
    assert len(rows) == 1 and "memo" in rows[0][1]


def test_each_insert_declares_whose_row_it_is():
    """--apply died live on 'new row violates row-level security policy
    for table deals': the write connection carried no tenant context, so
    every org table's WITH CHECK refused it. The writer must set the same
    GUCs the app sets, per row, from the row itself."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "recover_post_restore.py").read_text(encoding="utf-8")
    ctx_at = src.index("set_config('app.current_org_id'")
    insert_at = src.index("cur.execute(sql, r)")
    assert ctx_at < insert_at, (
        "the tenant context is set after (or never before) the insert")
    assert "app.current_user_id" in src, (
        "per-user tables (inbox_messages...) also check current_user_id()")


def test_row_context_reads_ownership_off_the_row():
    cols = ["id", "org_id", "owner_user_id", "actor_user_id", "subject"]
    row = ["m1", "org-9", "user-7", "auditor-3", "hello"]
    assert rpr.row_context(cols, row) == ("org-9", "user-7")
    # actor_user_id is an audit pointer, not ownership.
    assert rpr.row_context(["id", "actor_user_id"], ["a", "x"]) == (None, None)
    assert rpr.row_context(["id"], ["a"]) == (None, None)


def test_a_json_backslash_escape_survives_the_copy_decoder():
    r"""The bug that stopped --apply run 2: JSON evidence text contains a
    two-character \n escape; COPY writes it as \\n, and the sequential
    replace turned it into backslash + REAL newline - invalid JSON that
    Postgres refused. A scanner must give the first backslash its pair."""
    import json

    # chars: { " a " :   " x \ n y " }  as COPY writes them: \ doubled.
    copy_text = r'{"evidence": ["\'208\\nunits\'"], "fields": {"u": 208}}'
    decoded = rpr._unescape(copy_text)
    assert "\n" not in decoded, "a JSON escape became a literal newline"
    json.loads(decoded)   # must be valid JSON again

    assert rpr._unescape(r"a\\nb") == "a\\nb".replace("\\\\", "\\"), (
        "backslash-backslash-n must decode to backslash+n, never to "
        "backslash+newline")
    # And the plain escapes still work.
    assert rpr._unescape(r"tab\there") == "tab\there"
    assert rpr._unescape(r"line\nbreak") == "line\nbreak"
    assert rpr._unescape(r"\N") is None


def test_identity_columns_are_overridden_and_resequenced():
    """--apply run 3 hit 'cannot insert a non-DEFAULT value into column
    id' on property_activity (GENERATED ALWAYS). Keeping the original ids
    is what makes re-runs skip instead of duplicate, so the writer must
    override - and then push the sequence past max(id), or the app's own
    next activity row collides with a restored one."""
    from pathlib import Path

    sql = rpr._insert_sql("property_activity", ["id", "org_id"], True)
    assert "OVERRIDING SYSTEM VALUE" in sql
    assert sql.index("(\"id\", \"org_id\")") < sql.index("OVERRIDING") < \
        sql.index("VALUES"), "clause order matters to Postgres"

    plain = rpr._insert_sql("deals", ["id"], False)
    assert "OVERRIDING" not in plain, (
        "the clause is a syntax error on tables without identity columns")

    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "recover_post_restore.py").read_text(encoding="utf-8")
    assert "attidentity" in src, "identity detection must come from the catalog"
    assert "pg_get_serial_sequence" in src and "setval" in src, (
        "without resequencing, the app's next insert reuses a restored id")
