"""Schema auto-migration guard (prevents code/schema drift crashing the app).

The failure this prevents: pulling code that expects a new column while the
database is still on the old schema, producing a raw UndefinedColumn traceback.
"""

from __future__ import annotations

import pytest

from data import migrate, pg

pytestmark = pytest.mark.skipif(not pg.is_reachable(), reason="Postgres not configured")


def test_schema_reports_current_on_a_migrated_db():
    ok, missing = migrate.schema_is_current()
    assert ok, f"schema should be current, missing: {missing}"


def test_detects_and_heals_a_missing_column():
    """Drop a required column, prove it is detected, then auto-healed."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE inbox_messages DROP COLUMN IF EXISTS owner_user_id CASCADE")
        conn.commit()

    ok, missing = migrate.schema_is_current()
    assert not ok and "inbox_messages.owner_user_id" in missing

    healed, msg = migrate.ensure_schema(force=True)
    assert healed, msg
    ok2, missing2 = migrate.schema_is_current()
    assert ok2 and missing2 == []


def test_heal_restores_the_privacy_policy_too():
    """Dropping the column cascades away the RLS policy; the heal must restore
    it, or private mail would silently become readable."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE inbox_messages DROP COLUMN IF EXISTS owner_user_id CASCADE")
        conn.commit()
    migrate.ensure_schema(force=True)
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT policyname FROM pg_policies
                        WHERE tablename='inbox_messages'""")
        policies = {r["policyname"] for r in cur.fetchall()}
    assert "user_isolation" in policies


def _term_sheet_force_rls() -> bool:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT relforcerowsecurity AS f FROM pg_class WHERE relname='term_sheets'")
        return cur.fetchone()["f"]


def test_heals_a_db_that_accumulated_duplicate_term_sheets():
    """The real failure on the pilot host: a database that predates
    ux_term_sheets_message already holds duplicate rows, so creating the unique
    index fails. The dedupe that is supposed to run first is itself blocked by
    FORCE row-level security (no tenant context -> it matches zero rows), so the
    migration used to abort with 'duplicate keys exist' on every startup.
    """
    import uuid

    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS ux_term_sheets_message")
        cur.execute("SELECT id FROM organizations LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO organizations (name) VALUES ('migrate-test') RETURNING id")
            row = cur.fetchone()
        org = row["id"]
        for t in ("inbox_messages", "term_sheets"):
            cur.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        cur.execute("INSERT INTO inbox_messages (org_id, provider, external_id) "
                    "VALUES (%s, 'migrate-test', %s) RETURNING id", (org, str(uuid.uuid4())))
        msg = cur.fetchone()["id"]
        for _ in range(2):   # the duplicate a repeated Sync used to create
            cur.execute("INSERT INTO term_sheets (org_id, message_id) VALUES (%s, %s)", (org, msg))
        for t in ("inbox_messages", "term_sheets"):
            cur.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        conn.commit()

    ok, missing = migrate.schema_is_current()
    assert not ok and "index ux_term_sheets_message" in missing

    healed, message = migrate.ensure_schema(force=True)
    assert healed, message
    ok2, missing2 = migrate.schema_is_current()
    assert ok2 and missing2 == []

    # The dedupe must not leave row-level security switched off behind it.
    assert _term_sheet_force_rls() is True

    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE term_sheets NO FORCE ROW LEVEL SECURITY")
        cur.execute("SELECT count(*) AS n FROM (SELECT message_id FROM term_sheets "
                    "WHERE message_id IS NOT NULL GROUP BY message_id "
                    "HAVING count(*) > 1) d")
        remaining = cur.fetchone()["n"]
        cur.execute("ALTER TABLE term_sheets FORCE ROW LEVEL SECURITY")
        cur.execute("DELETE FROM inbox_messages WHERE provider='migrate-test'")
        conn.commit()
    assert remaining == 0


def test_ensure_schema_never_raises_when_db_is_unreachable(monkeypatch):
    def boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(migrate, "schema_is_current", boom)
    ok, msg = migrate.ensure_schema(force=True)
    assert ok is False and "migration failed" in msg.lower()
