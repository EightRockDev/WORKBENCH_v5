"""Per-user property-card overrides — routing and degradation.

The Postgres path is exercised when a database is reachable (same auto-skip
discipline as the other pg suites); the routing/fallback logic is tested
everywhere with pg stubbed, because THAT branch decision is what decides
whether one user's edits leak to another.
"""

from __future__ import annotations

import json
import uuid

import pytest

from core import property_overrides as po
from data import pg


# ------------------------------------------------------- routing (no pg needed)

def test_no_identity_routes_to_legacy_fallback(monkeypatch):
    # Ungated dev mode: no org/user -> per-user store must decline (False /
    # None) so the caller uses the shared folder file, the pre-multi-user
    # behavior with only one human on the box.
    monkeypatch.setattr(pg, "is_reachable", lambda: True)
    assert po.load_user_overrides(None, None, "Deal-A") is None
    assert po.save_user_overrides(None, None, "Deal-A", {"units": 1}) is False


def test_unreachable_pg_degrades_not_crashes(monkeypatch):
    monkeypatch.setattr(pg, "is_reachable", lambda: False)
    assert po.load_user_overrides("o", "u", "Deal-A") is None
    assert po.save_user_overrides("o", "u", "Deal-A", {"units": 1}) is False


# ------------------------------------------------------- Postgres round-trip

pytestmark_pg = pytest.mark.skipif(
    not pg.is_reachable(), reason="no reachable Postgres")


@pytestmark_pg
def test_user_edits_are_private_to_that_user(pg_org_with_two_users):
    """The whole feature in one assertion set: A saves, A sees, B doesn't."""
    org_id, user_a, user_b = pg_org_with_two_users
    key = f"Deal-{uuid.uuid4().hex[:8]}"

    assert po.save_user_overrides(org_id, user_a, key, {"units": 42,
                                                        "market": "HACK"})
    mine = po.load_user_overrides(org_id, user_a, key)
    assert mine == {"units": 42}          # locked field dropped at save
    assert po.load_user_overrides(org_id, user_b, key) is None   # B sees nothing


@pytestmark_pg
def test_cleared_edits_stay_an_explicit_empty(pg_org_with_two_users):
    org_id, user_a, _ = pg_org_with_two_users
    key = f"Deal-{uuid.uuid4().hex[:8]}"
    po.save_user_overrides(org_id, user_a, key, {"units": 12})
    po.save_user_overrides(org_id, user_a, key, {})
    # {} (my explicit reset) is distinct from None (never saved).
    assert po.load_user_overrides(org_id, user_a, key) == {}


@pytest.fixture
def pg_org_with_two_users():
    """A throwaway org with two members; rows cascade-delete with the org."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name, type) VALUES (%s, 'sponsor') "
            "RETURNING id", (f"t-{uuid.uuid4().hex[:8]}",))
        org_id = str(cur.fetchone()["id"])
        users = []
        for i in range(2):
            cur.execute(
                """INSERT INTO users (idp_sub, email, platform_role, status)
                   VALUES (%s, %s, 'internal', 'active') RETURNING id""",
                (f"sub-{uuid.uuid4().hex}", f"u{i}-{uuid.uuid4().hex[:6]}@t.io"))
            users.append(str(cur.fetchone()["id"]))
        conn.commit()
    try:
        yield org_id, users[0], users[1]
    finally:
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
            for u in users:
                cur.execute("DELETE FROM users WHERE id = %s", (u,))
            conn.commit()
