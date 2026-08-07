"""Property activity trail — degradation everywhere, round-trip when Postgres
is reachable (auto-skip discipline as the other pg suites)."""

from __future__ import annotations

import uuid

import pytest

from core import property_activity as pa
from data import pg


def test_no_identity_or_pg_is_a_silent_noop(monkeypatch):
    monkeypatch.setattr(pg, "is_reachable", lambda: False)
    assert pa.log_view("o", "u", "Deal-A") is False
    assert pa.recent("o") == [] and pa.by_property("o") == []
    monkeypatch.setattr(pg, "is_reachable", lambda: True)
    assert pa.log_view(None, "u", "Deal-A") is False
    assert pa.log_view("o", None, "Deal-A") is False
    assert pa.log_view("o", "u", "") is False


pytestmark_pg = pytest.mark.skipif(
    not pg.is_reachable(), reason="no reachable Postgres")


@pytest.fixture
def org_user():
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO organizations (name, type) VALUES (%s,'sponsor') "
                    "RETURNING id", (f"act-{uuid.uuid4().hex[:8]}",))
        org_id = str(cur.fetchone()["id"])
        cur.execute("""INSERT INTO users (idp_sub, email, display_name,
                        platform_role, status)
                       VALUES (%s, %s, 'Test Analyst', 'internal', 'active')
                       RETURNING id""",
                    (f"sub-{uuid.uuid4().hex}", f"a-{uuid.uuid4().hex[:6]}@t.io"))
        user_id = str(cur.fetchone()["id"])
        conn.commit()
    try:
        yield org_id, user_id
    finally:
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()


@pytestmark_pg
def test_views_and_edits_round_trip_into_the_rollup(org_user):
    org_id, user_id = org_user
    key = f"Deal-{uuid.uuid4().hex[:8]}"
    assert pa.log_view(org_id, user_id, key)
    assert pa.log_edit(org_id, user_id, key, ["units", "avg_rent"])

    rows = pa.recent(org_id)
    assert [r["action"] for r in rows[:2]] == ["edited", "viewed"]
    assert rows[0]["fields"] == "avg_rent, units"
    assert rows[0]["user"] == "Test Analyst"

    roll = pa.by_property(org_id)
    mine = next(r for r in roll if r["property"] == key)
    assert mine["views"] == 1 and mine["edits"] == 1 and mine["people"] == 1
    assert "Test Analyst" in mine["who"]
