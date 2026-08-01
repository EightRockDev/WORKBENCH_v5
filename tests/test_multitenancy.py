"""Acceptance tests for multi-tenancy & the role model (spec Section 10).

Covers:
  AC-10.2  field masking holds server-side (Maintenance/Leasing never receive
           purchase_price/returns; Bookkeeper never receives waterfall/lp_pii).
  AC-10.3  only Principal can commit GO/NO-GO; only Controller can edit the
           waterfall; neither right is self-grantable (separation of duty).
  AC-10.4  LP scope is own_only; Broker/Guest is single_deal (time-boxed).
  AC-10.5  point-and-click setup (create org -> invite -> assign preset) and
           one-click offboarding (revoke + keep work org-visible).

Skipped automatically when Postgres is not configured.
"""

from __future__ import annotations

import pytest

from core import orgs
from core.permissions import (
    FIELD_LP_PII, FIELD_PURCHASE_PRICE, FIELD_RETURNS_IRR, FIELD_WATERFALL_PROMOTE,
    MASK_PLACEHOLDER,
)
from data import pg

pytestmark = pytest.mark.skipif(not pg.is_reachable(),
                                reason="Postgres not reachable (DATABASE_URL unset or server down)")


@pytest.fixture()
def clean_db():
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE users, organizations, audit_log RESTART IDENTITY CASCADE")
        conn.commit()
    yield
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE users, organizations, audit_log RESTART IDENTITY CASCADE")
        conn.commit()


def _user(email: str) -> str:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO users (idp_sub, email, platform_role, status)
               VALUES (%s, %s, 'internal', 'active') RETURNING id""",
            (f"idp|{email}", email))
        conn.commit()
        return str(cur.fetchone()["id"])


# ---------------------------------------------------------------------------
# AC-10.5 — setup: create org -> creator is Principal/org-admin
# ---------------------------------------------------------------------------

def test_create_org_enrolls_creator_as_principal(clean_db):
    uid = _user("founder@eight-rock.com")
    org_id = orgs.create_org(uid, "Eight Rock Capital")
    perms = orgs.get_permissions(uid, org_id)
    assert perms is not None
    assert perms.role_preset == "principal"
    assert perms.can("commit_go_nogo") and perms.can("manage_users")
    # creator shows up as an active member
    members = orgs.list_members(org_id)
    assert len(members) == 1 and members[0].status == "active"
    # and the org appears in the user's org list (resolvable at login)
    assert any(o["org_id"] == org_id for o in orgs.user_orgs(uid))


# ---------------------------------------------------------------------------
# AC-10.2 — field masking is server-side, by preset
# ---------------------------------------------------------------------------

def test_maintenance_never_sees_price_or_returns(clean_db):
    admin = _user("admin@eight-rock.com")
    tech = _user("tech@eight-rock.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    orgs.add_member(admin, tech, org_id, "maintenance")
    orgs.activate_member(admin, tech, org_id)

    p = orgs.get_permissions(tech, org_id)
    assert p is not None
    assert p.is_masked(FIELD_PURCHASE_PRICE) and p.is_masked(FIELD_RETURNS_IRR)
    assert p.mask(FIELD_PURCHASE_PRICE, 3_500_000) == MASK_PLACEHOLDER
    # whole-record scrub strips the masked keys before serialization
    row = {"name": "Ghent Court", "purchase_price": 3_500_000, "returns_irr": 0.18, "units": 48}
    scrubbed = p.scrub(row)
    assert scrubbed["purchase_price"] == MASK_PLACEHOLDER
    assert scrubbed["returns_irr"] == MASK_PLACEHOLDER
    assert scrubbed["units"] == 48  # non-sensitive passes through
    # maintenance cannot even open a financial module
    assert not p.can_open("accounting") and not p.can_open("waterfall")


def test_bookkeeper_sees_accounting_but_not_waterfall(clean_db):
    admin = _user("admin@eight-rock.com")
    bk = _user("bookkeeper@eight-rock.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    orgs.add_member(admin, bk, org_id, "bookkeeper")
    orgs.activate_member(admin, bk, org_id)

    p = orgs.get_permissions(bk, org_id)
    assert p.can_open("accounting")
    assert not p.can_open("waterfall")               # no waterfall module grant
    assert p.is_masked(FIELD_WATERFALL_PROMOTE) and p.is_masked(FIELD_LP_PII)
    assert not p.can("edit_waterfall")


# ---------------------------------------------------------------------------
# AC-10.3 — separation of duty (GO gate + waterfall-edit not self-grantable)
# ---------------------------------------------------------------------------

def test_only_principal_commits_go(clean_db):
    admin = _user("principal@eight-rock.com")
    analyst = _user("analyst@eight-rock.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    orgs.add_member(admin, analyst, org_id, "analyst")
    orgs.activate_member(admin, analyst, org_id)

    principal_p = orgs.get_permissions(admin, org_id)
    analyst_p = orgs.get_permissions(analyst, org_id)

    assert principal_p.can("commit_go_nogo")
    assert not analyst_p.can("commit_go_nogo")
    with pytest.raises(PermissionError):
        analyst_p.require("commit_go_nogo")          # 403 semantics


def test_only_controller_edits_waterfall(clean_db):
    admin = _user("principal@eight-rock.com")
    controller = _user("cfo@eight-rock.com")
    analyst = _user("analyst@eight-rock.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    for uid, preset in [(controller, "controller"), (analyst, "analyst")]:
        orgs.add_member(admin, uid, org_id, preset)
        orgs.activate_member(admin, uid, org_id)

    assert orgs.get_permissions(controller, org_id).can("edit_waterfall")
    assert not orgs.get_permissions(analyst, org_id).can("edit_waterfall")


# ---------------------------------------------------------------------------
# AC-10.4 — external scopes
# ---------------------------------------------------------------------------

def test_lp_and_guest_scopes(clean_db):
    admin = _user("principal@eight-rock.com")
    lp = _user("lp@example.com")
    guest = _user("broker@example.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    orgs.add_member(admin, lp, org_id, "lp_investor")
    orgs.add_member(admin, guest, org_id, "guest", scope="single_deal:DEAL-123")
    orgs.activate_member(admin, lp, org_id)
    orgs.activate_member(admin, guest, org_id)

    lp_p = orgs.get_permissions(lp, org_id)
    guest_p = orgs.get_permissions(guest, org_id)
    assert lp_p.scope_kind == "own_only"
    assert lp_p.can_open("lp_portal") and not lp_p.can_open("underwriting")
    assert guest_p.scope_kind == "single_deal" and guest_p.scope_ids == ["DEAL-123"]


# ---------------------------------------------------------------------------
# AC-10.5 — offboarding: revoke access, keep work org-visible
# ---------------------------------------------------------------------------

def test_offboarding_revokes_access(clean_db):
    admin = _user("principal@eight-rock.com")
    leaver = _user("leaver@eight-rock.com")
    successor = _user("successor@eight-rock.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    orgs.add_member(admin, leaver, org_id, "analyst")
    orgs.activate_member(admin, leaver, org_id)
    assert orgs.get_permissions(leaver, org_id) is not None  # active before

    orgs.offboard_member(admin, leaver, org_id, successor_user_id=successor)
    assert orgs.get_permissions(leaver, org_id) is None       # access revoked
    # still listed (suspended) so their org-owned work remains attributable
    statuses = {m.user_id: m.status for m in orgs.list_members(org_id)}
    assert statuses[leaver] == "suspended"


def test_add_member_rejects_unknown_preset(clean_db):
    admin = _user("principal@eight-rock.com")
    u = _user("x@example.com")
    org_id = orgs.create_org(admin, "Eight Rock Capital")
    with pytest.raises(ValueError):
        orgs.add_member(admin, u, org_id, "wizard")
