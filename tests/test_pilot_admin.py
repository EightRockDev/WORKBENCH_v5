"""Acceptance tests for the v5.0 pilot auth + concurrency layer (Sections 9.3/9.4).

These run against a real PostgreSQL database (the spec's pilot store). They are
skipped automatically when DATABASE_URL / [postgres].url is not configured, so
the SQLite-only dev suite is unaffected.

Covers:
  AC-9.4  first signup -> admin; later signups -> pending; approve; RBAC gate.
  AC-9.3  optimistic-concurrency conflict detection; presence soft locks.
"""

from __future__ import annotations

import pytest

from data import pg

pytestmark = pytest.mark.skipif(not pg.is_reachable(),
                                reason="Postgres not reachable (DATABASE_URL unset or server down)")


@pytest.fixture()
def clean_db():
    """Reset the pilot tables to a known-empty state around each test."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE users, organizations, audit_log RESTART IDENTITY CASCADE")
        conn.commit()
    yield
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE users, organizations, audit_log RESTART IDENTITY CASCADE")
        conn.commit()


# ---------------------------------------------------------------------------
# AC-9.4 — authentication & user administration
# ---------------------------------------------------------------------------

def test_first_user_becomes_admin(clean_db):
    from core import user_admin

    u = user_admin.sync_user_on_login("auth0|aaa", "founder@eight-rock.com", "Founder")
    assert u.is_admin and u.is_active and not u.is_pending


def test_second_user_is_pending(clean_db):
    from core import user_admin

    user_admin.sync_user_on_login("auth0|aaa", "founder@eight-rock.com", "Founder")
    second = user_admin.sync_user_on_login("auth0|bbb", "random@example.com", "Random")
    assert not second.is_admin
    assert second.platform_role == "trial"
    assert second.is_pending  # sees only the pending-approval screen (FR-9.4.3)


def test_approve_activates_pending_user(clean_db):
    from core import user_admin

    admin = user_admin.sync_user_on_login("auth0|aaa", "founder@eight-rock.com", "Founder")
    pend = user_admin.sync_user_on_login("auth0|bbb", "analyst@eight-rock.com", "Analyst")
    user_admin.approve_user(admin.id, pend.id)

    refreshed = user_admin.get_user("auth0|bbb")
    assert refreshed is not None and refreshed.is_active and refreshed.platform_role == "internal"


def test_login_upsert_is_idempotent_and_stamps_last_login(clean_db):
    from core import user_admin

    first = user_admin.sync_user_on_login("auth0|aaa", "founder@eight-rock.com", "Founder")
    again = user_admin.sync_user_on_login("auth0|aaa", "founder@newmail.com", "Founder")
    assert first.id == again.id            # same identity, not a duplicate row
    assert again.email == "founder@newmail.com"
    assert len(user_admin.list_users()) == 1


def test_require_admin_blocks_non_admin(clean_db):
    from core import user_admin

    user_admin.sync_user_on_login("auth0|aaa", "founder@eight-rock.com", "Founder")
    trial = user_admin.sync_user_on_login("auth0|bbb", "random@example.com", "Random")
    with pytest.raises(PermissionError):
        user_admin.require_admin(trial)


def test_admin_actions_write_audit_log(clean_db):
    from core import user_admin

    admin = user_admin.sync_user_on_login("auth0|aaa", "founder@eight-rock.com", "Founder")
    pend = user_admin.sync_user_on_login("auth0|bbb", "analyst@eight-rock.com", "Analyst")
    user_admin.set_role(admin.id, pend.id, "internal")
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM audit_log WHERE action='user.set_role'")
        assert cur.fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# AC-9.3 — optimistic concurrency + presence
# ---------------------------------------------------------------------------

def _make_org_and_poc():
    """Create an org and one poc_record; return (org_id, poc_id, version)."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO organizations (name) VALUES ('Test Org') RETURNING id")
        org_id = str(cur.fetchone()["id"])
        conn.commit()
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO poc_records (org_id, property_id, role, person)
               VALUES (%s, '8R-51710-A', 'owner', '{"full_name":"Owner One"}')
            RETURNING id, row_version""", (org_id,))
        row = cur.fetchone()
        conn.commit()
        return org_id, str(row["id"]), row["row_version"]


def test_optimistic_update_detects_conflict(clean_db):
    from data import concurrency

    org_id, poc_id, v0 = _make_org_and_poc()

    # User A saves against the loaded version -> succeeds, version bumps.
    with pg.org_connection(org_id) as conn:
        a = concurrency.optimistic_update(conn, "poc_records", poc_id, v0,
                                          {"portfolio_id": "PORT-A"})
    assert a.ok and a.new_version == v0 + 1

    # User B saves against the STALE version -> conflict, not silent overwrite.
    with pg.org_connection(org_id) as conn:
        b = concurrency.optimistic_update(conn, "poc_records", poc_id, v0,
                                          {"portfolio_id": "PORT-B"})
    assert not b.ok
    assert b.current_version == v0 + 1     # tells the UI what the live version is

    # The winning write survived; B's stale write did not clobber it.
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("SELECT portfolio_id FROM poc_records WHERE id = %s", (poc_id,))
        assert cur.fetchone()["portfolio_id"] == "PORT-A"


def test_soft_lock_presence(clean_db):
    from data import concurrency

    org_id, poc_id, _ = _make_org_and_poc()
    # two distinct users
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (idp_sub,email,platform_role,status) "
                    "VALUES ('u|a','a@x.com','internal','active'),"
                    "       ('u|b','b@x.com','internal','active') RETURNING id")
        conn.commit()
        cur.execute("SELECT id, email FROM users ORDER BY email")
        rows = cur.fetchall()
    ua = str(rows[0]["id"]); ub = str(rows[1]["id"])

    # A opens the record for editing.
    la = concurrency.acquire_or_refresh_lock(org_id, "poc", poc_id, ua)
    assert la.held and la.mine

    # B tries — sees A's lock, is NOT granted it, but reads are never blocked.
    lb = concurrency.acquire_or_refresh_lock(org_id, "poc", poc_id, ub)
    assert lb.held and not lb.mine and lb.by_user_id == ua

    # A releases; now B can take it.
    concurrency.release_lock(org_id, "poc", poc_id, ua)
    lb2 = concurrency.acquire_or_refresh_lock(org_id, "poc", poc_id, ub)
    assert lb2.held and lb2.mine
