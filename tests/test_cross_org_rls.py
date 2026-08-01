"""AC-10.1 — a user in Org A cannot reach ANY Org B record.

The spec asks for this by name: "verified by an automated cross-org RLS test
suite". BUILD-ORDER recorded it as verified; no such suite existed. Every
isolation guarantee in the pilot rests on Postgres row-level security rather
than WHERE clauses — `inbox_messages` is queried with no filter at all — so
an unenforced policy is not a degraded feature, it is one tenant reading
another's deals.

Deliberately GENERIC: the table list is read from `pg_class.relrowsecurity`
at runtime and every protected table is exercised the same way. A table added
later without a policy fails here automatically, which a hand-written list of
today's fifteen tables would not do.

Requires a non-superuser DATABASE_URL — superusers bypass RLS even under
FORCE ROW LEVEL SECURITY, and `conftest.py` refuses that configuration.
"""

from __future__ import annotations

import re
import uuid

import pytest

from data import pg

pytestmark = pytest.mark.skipif(not pg.is_configured(),
                                reason="Postgres not configured (DATABASE_URL unset)")

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"

# Columns whose value must be a real org id rather than filler.
_ORG_COL = "org_id"


def _protected_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("""SELECT c.relname AS t FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public' AND c.relrowsecurity
                       ORDER BY c.relname""")
        return [r["t"] for r in cur.fetchall()]


def _required_columns(conn, table: str) -> list[tuple[str, str]]:
    """(name, type) for columns that must be supplied on INSERT."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.attname AS name,
                   format_type(a.atttypid, a.atttypmod) AS typ
              FROM pg_class c
              JOIN pg_attribute a ON a.attrelid = c.oid
                                 AND a.attnum > 0 AND NOT a.attisdropped
              LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
             WHERE c.relname = %s AND a.attnotnull AND d.adbin IS NULL
             ORDER BY a.attnum""", (table,))
        return [(r["name"], r["typ"]) for r in cur.fetchall()]


_CHECK_LITERAL = re.compile(r"'([^']+)'::")


def _policy_user_columns(conn, table: str) -> list[str]:
    """User columns a policy insists on, even where the column is nullable.

    `inbox_messages` and `mailbox_connections` add
    `owner_user_id = current_user_id()` to their WITH CHECK. The column is
    nullable, so a "required columns" insert leaves it NULL and the policy
    rejects the row - which looks like an isolation failure and is really a
    filling failure.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pg_get_expr(polwithcheck, polrelid) AS wc,
                   pg_get_expr(polqual, polrelid)      AS q
              FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
             WHERE c.relname = %s""", (table,))
        exprs = " ".join(str(r["wc"] or "") + " " + str(r["q"] or "")
                         for r in cur.fetchall())
    return [c for c in ("owner_user_id", "user_id") if c in exprs]


def _or_null_columns(conn, table: str) -> list[str]:
    """Columns named by a table-level "at least one of" CHECK.

    `revocations` requires `e164 IS NOT NULL OR email IS NOT NULL` - neither
    column is individually NOT NULL, so nothing in the column metadata says
    the row needs one.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pg_get_constraintdef(c.oid) AS def
              FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
             WHERE t.relname = %s AND c.contype = 'c'""", (table,))
        defs = [r["def"] for r in cur.fetchall()]
    for d in defs:
        if " OR " in d and "IS NOT NULL" in d:
            return re.findall(r"\(([a-z0-9_]+) IS NOT NULL\)", d)[:1]
    return []


def _column_types(conn, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.attname AS name,
                   format_type(a.atttypid, a.atttypmod) AS typ
              FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid
             WHERE c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped""",
                    (table,))
        return {r["name"]: r["typ"] for r in cur.fetchall()}


def _check_values(conn, table: str) -> dict[str, str]:
    """{column: a value its CHECK constraint accepts}.

    Several tables constrain a column to an enum-like set
    (`channel = ANY (ARRAY['sms'::text, 'email'::text])`). Filling those with
    "x" fails the constraint, not the policy — which would quietly shrink the
    sweep to the tables that happen to be permissive.
    """
    out: dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pg_get_constraintdef(c.oid) AS def
              FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
             WHERE t.relname = %s AND c.contype = 'c'""", (table,))
        defs = [r["def"] for r in cur.fetchall()]
    for d in defs:
        col = re.search(r"CHECK \(+\(?([a-z_]+)", d)
        lit = _CHECK_LITERAL.search(d)
        if col and lit:
            out.setdefault(col.group(1), lit.group(1))
    return out


def _filler(typ: str, name: str, org_id: str, user_id: str | None = None,
            checks: dict[str, str] | None = None):
    """A value that will satisfy the column's type AND its constraints."""
    if name == _ORG_COL:
        return org_id
    if checks and name in checks:
        return checks[name]
    if user_id and name in ("user_id", "owner_user_id", "created_by",
                            "granted_by", "actor_user_id"):
        return user_id
    t = typ.lower()
    if t.startswith("uuid"):
        return str(uuid.uuid4())
    if any(t.startswith(k) for k in ("int", "big", "small", "numeric", "double", "real")):
        return 1
    if t.startswith("bool"):
        return False
    if "timestamp" in t or t.startswith("date"):
        return "2026-01-01T00:00:00+00:00"
    if t.startswith("jsonb") or t.startswith("json"):
        return "{}"
    if "[]" in t:
        return []
    return "x"


def _set_org(conn, org_id: str, user_id: str | None = None) -> None:
    """Set the tenant, and the user when the table has a per-user policy.

    `inbox_messages` and `mailbox_connections` filter on BOTH
    `current_org_id()` and `current_user_id()` (mail is private to its owner,
    not shared org-wide), so an org-only context cannot even insert there.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))
        cur.execute("SELECT set_config('app.current_user_id', %s, false)",
                    (user_id or "",))


def _insert_row(conn, table: str, org_id: str,
                user_id: str | None = None) -> bool:
    """Insert one minimal row for `org_id`. False when the table's shape
    defeats generic filling — reported, not silently skipped."""
    cols = _required_columns(conn, table)
    names = [c for c, _ in cols]
    if _ORG_COL not in names:
        return False
    checks = _check_values(conn, table)
    types = _column_types(conn, table)
    # Nullable columns the POLICY or a table-level CHECK still requires.
    for extra in _policy_user_columns(conn, table) + _or_null_columns(conn, table):
        if extra in types and extra not in names:
            names.append(extra)
            cols = list(cols) + [(extra, types[extra])]
    vals = [_filler(t, c, org_id, user_id, checks) for c, t in cols]
    quoted = ", ".join(f'"{c}"' for c in names)
    marks = ", ".join(["%s"] * len(names))
    try:
        with conn.cursor() as cur:
            cur.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({marks})', vals)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def _purge(conn, tables, users) -> None:
    """Remove this suite's rows from both tenants.

    Some tables are append-only by trigger and will refuse; that is correct
    behaviour, so a failure here is skipped rather than raised.
    """
    for table in tables:
        for org in (ORG_A, ORG_B):
            _set_org(conn, org, users.get(org))
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DELETE FROM "{table}"')
                conn.commit()
            except Exception:
                conn.rollback()


def _ensure_users(conn) -> dict[str, str]:
    """One user per org — several tables carry a user FK and a per-user policy."""
    ids = {}
    with conn.cursor() as cur:
        for org, email in ((ORG_A, "a@rls.test"), (ORG_B, "b@rls.test")):
            uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))
            cur.execute(
                "INSERT INTO users (id, idp_sub, email, display_name, status) "
                "VALUES (%s, %s, %s, %s, 'active') "
                "ON CONFLICT (id) DO NOTHING",
                (uid, f"rls-test|{email}", email, email))
            # role_preset is a FK to role_presets.key, not the display label.
            cur.execute(
                "INSERT INTO memberships (org_id, user_id, role_preset) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (org, uid, "principal"))
            ids[org] = uid
    conn.commit()
    return ids


def _ensure_orgs(conn) -> None:
    """Both tenants must exist before anything can reference them.

    `organizations` is NOT RLS-protected — it is the table the policies read
    tenancy FROM — so this is a plain insert.
    """
    with conn.cursor() as cur:
        for org, name in ((ORG_A, "Org A (rls test)"), (ORG_B, "Org B (rls test)")):
            cur.execute(
                "INSERT INTO organizations (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING", (org, name))
    conn.commit()


@pytest.fixture(scope="module")
def seeded():
    """One row per org in every protected table we can fill generically."""
    with pg.connection() as conn:
        tables = _protected_tables(conn)
        assert tables, "no RLS-protected tables found - schema not applied?"
        _ensure_orgs(conn)
        users = _ensure_users(conn)
        # Clean BEFORE seeding as well as after: a run that died mid-way
        # leaves rows behind, and the next run then counts two where it
        # expects one - a confusing failure that has nothing to do with RLS.
        _purge(conn, tables, users)
        filled: list[str] = []
        for table in tables:
            _set_org(conn, ORG_A, users[ORG_A])
            ok_a = _insert_row(conn, table, ORG_A, users[ORG_A])
            _set_org(conn, ORG_B, users[ORG_B])
            ok_b = _insert_row(conn, table, ORG_B, users[ORG_B])
            if ok_a and ok_b:
                filled.append(table)
        assert filled, "could not seed any table"
        # Tests need the same user context the rows were written under:
        # mail tables isolate per USER as well as per org, so an org-only
        # connection correctly sees nothing there.
        yield filled, users
        _purge(conn, filled, users)


def test_every_protected_table_was_exercised(seeded):
    tables, _users = seeded
    """A table this suite cannot seed is invisible to it — say so loudly
    rather than reporting a green run over a partial sweep."""
    with pg.connection() as conn:
        all_tables = set(_protected_tables(conn))
    missed = all_tables - set(tables)
    assert not missed, (
        f"not covered by the cross-org sweep: {sorted(missed)} — extend "
        f"_filler() so these are proven too")


def test_org_a_cannot_read_org_b_rows(seeded):
    """The core of AC-10.1."""
    tables, users = seeded
    with pg.connection() as conn:
        _set_org(conn, ORG_A, users[ORG_A])
        for table in tables:
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(*) AS n FROM "{table}" '
                            f'WHERE {_ORG_COL} = %s', (ORG_B,))
                assert cur.fetchone()["n"] == 0, f"{table}: Org A can read Org B"


def test_each_org_sees_its_own_rows_and_only_its_own(seeded):
    """Isolation must not be achieved by showing nobody anything.

    Asserted as "every visible row belongs to me" rather than an exact count:
    `outreach_touches` is append-only by trigger (AC-B2, the outreach audit
    trail is immutable), so rows legitimately accumulate and a count of 1
    would be a brittle proxy for the property that actually matters.
    """
    tables, users = seeded
    with pg.connection() as conn:
        for org in (ORG_A, ORG_B):
            _set_org(conn, org, users[org])
            for table in tables:
                with conn.cursor() as cur:
                    cur.execute(f'SELECT count(*) AS n FROM "{table}"')
                    visible = cur.fetchone()["n"]
                    cur.execute(f'SELECT count(*) AS n FROM "{table}" '
                                f'WHERE {_ORG_COL} <> %s', (org,))
                    foreign = cur.fetchone()["n"]
                assert visible >= 1, f"{table}: org {org} sees nothing at all"
                assert foreign == 0, (
                    f"{table}: org {org} can see {foreign} row(s) it does "
                    f"not own")


def test_org_a_cannot_update_org_b_rows(seeded):
    """Reads are not the only leak — a blind UPDATE must touch nothing."""
    tables, users = seeded
    with pg.connection() as conn:
        _set_org(conn, ORG_A, users[ORG_A])
        for table in tables:
            with conn.cursor() as cur:
                cur.execute(f'UPDATE "{table}" SET {_ORG_COL} = {_ORG_COL} '
                            f'WHERE {_ORG_COL} = %s', (ORG_B,))
                assert cur.rowcount == 0, f"{table}: Org A updated Org B rows"
        conn.rollback()


def test_org_a_cannot_delete_org_b_rows(seeded):
    tables, users = seeded
    with pg.connection() as conn:
        for table in tables:
            _set_org(conn, ORG_A, users[ORG_A])
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DELETE FROM "{table}" WHERE {_ORG_COL} = %s',
                                (ORG_B,))
                    removed = cur.rowcount
            except Exception:
                # Append-only tables raise instead of deleting - also a pass.
                conn.rollback()
                continue
            conn.rollback()
            assert removed == 0, f"{table}: Org A deleted Org B rows"


def test_org_a_cannot_insert_a_row_owned_by_org_b(seeded):
    """WITH CHECK, not just USING: planting a row in another tenant is the
    same breach as reading one."""
    tables, users = seeded
    with pg.connection() as conn:
        for table in tables:
            _set_org(conn, ORG_A, users[ORG_A])
            planted = _insert_row(conn, table, ORG_B, users[ORG_A])
            assert not planted, f"{table}: Org A inserted a row owned by Org B"
            conn.rollback()


def test_no_org_context_reads_nothing(seeded):
    """Fail closed. A connection that forgot to set the tenant must see an
    empty database, never everything."""
    tables, _users = seeded
    with pg.connection() as conn:
        _set_org(conn, "")
        for table in tables:
            with conn.cursor() as cur:
                try:
                    cur.execute(f'SELECT count(*) AS n FROM "{table}"')
                    n = cur.fetchone()["n"]
                except Exception:
                    conn.rollback()
                    continue          # erroring is also failing closed
                assert n == 0, f"{table}: readable with no org context"
