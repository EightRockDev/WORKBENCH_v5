"""Schema auto-migration guard (prevents code/schema drift crashing the app).

The failure this prevents: pulling code that expects a new column while the
database is still on the old schema, producing a raw UndefinedColumn traceback.
"""

from __future__ import annotations

import pytest

from data import migrate, pg

pytestmark = pytest.mark.skipif(not pg.is_configured(), reason="Postgres not configured")


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


def test_ensure_schema_never_raises_when_db_is_unreachable(monkeypatch):
    def boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(migrate, "schema_is_current", boom)
    ok, msg = migrate.ensure_schema(force=True)
    assert ok is False and "migration failed" in msg.lower()
