"""Organizations & memberships — the multi-tenant control plane (Section 10).

Point-and-click simple (10.5): an org admin creates an org, invites a person by
email, and picks a **role preset** from the platform library — no per-user
permission wiring. Org-owned config and all a member's work stay with the org, so
nothing is lost when someone leaves (one-click offboarding reassigns + revokes).

memberships is a control-plane table (not under org RLS — see db/pilot_schema.sql),
so these helpers use a plain connection. Deal-DATA isolation stays enforced by RLS
on the org-private tables via ``data.pg.org_connection``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.permissions import Permissions
from data import pg

ORG_TYPES = ("sponsor", "pm_arm", "construction_arm")


@dataclass(frozen=True)
class Member:
    user_id: str
    org_id: str
    email: str
    display_name: str | None
    role_preset: str
    scope: str
    status: str


# ---------------------------------------------------------------------------
# Org lifecycle
# ---------------------------------------------------------------------------

def create_org(actor_id: str, name: str, *, org_type: str = "sponsor",
               plan_tier: str = "solo", parent_org_id: str | None = None,
               make_actor_admin: bool = True) -> str:
    """Create an org and (by default) enroll the creator as its Principal/Owner
    (org super-admin) — the "first user in a new org is auto-promoted" rule (10.5)."""
    if org_type not in ORG_TYPES:
        raise ValueError(f"unknown org_type {org_type!r}")
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO organizations (name, type, plan_tier, parent_org_id)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (name, org_type, plan_tier, parent_org_id))
        org_id = str(cur.fetchone()["id"])
        _audit(cur, actor_id, "org.create", org_id, after={"name": name, "type": org_type})
        if make_actor_admin:
            cur.execute(
                """INSERT INTO memberships (user_id, org_id, role_preset, scope, status, invited_by)
                   VALUES (%s, %s, 'principal', 'org_all', 'active', %s)
                   ON CONFLICT (user_id, org_id) DO UPDATE SET role_preset='principal', status='active'""",
                (actor_id, org_id, actor_id))
            _audit(cur, actor_id, "member.add", org_id,
                   after={"user_id": actor_id, "role_preset": "principal"})
        conn.commit()
        return org_id


# ---------------------------------------------------------------------------
# Membership lifecycle (point-and-click: admin only ever picks a preset key)
# ---------------------------------------------------------------------------

def add_member(actor_id: str, user_id: str, org_id: str, role_preset: str,
               *, scope: str | None = None, expires_at: Any | None = None) -> None:
    _assert_preset_exists(role_preset)
    with pg.connection() as conn, conn.cursor() as cur:
        eff_scope = scope or _preset_default_scope(cur, role_preset)
        cur.execute(
            """INSERT INTO memberships (user_id, org_id, role_preset, scope, status, invited_by, expires_at)
               VALUES (%s, %s, %s, %s, 'invited', %s, %s)
               ON CONFLICT (user_id, org_id) DO UPDATE
                 SET role_preset=EXCLUDED.role_preset, scope=EXCLUDED.scope, status='invited'""",
            (user_id, org_id, role_preset, eff_scope, actor_id, expires_at))
        _audit(cur, actor_id, "member.add", org_id,
               after={"user_id": user_id, "role_preset": role_preset, "scope": eff_scope})
        conn.commit()


def set_member_preset(actor_id: str, user_id: str, org_id: str, role_preset: str) -> None:
    _assert_preset_exists(role_preset)
    _update_member(actor_id, user_id, org_id, "role_preset", role_preset, "member.set_preset")


def set_member_scope(actor_id: str, user_id: str, org_id: str, scope: str) -> None:
    _update_member(actor_id, user_id, org_id, "scope", scope, "member.set_scope")


def set_member_status(actor_id: str, user_id: str, org_id: str, status: str) -> None:
    if status not in ("invited", "active", "suspended"):
        raise ValueError(f"unknown status {status!r}")
    _update_member(actor_id, user_id, org_id, "status", status, "member.set_status")


def activate_member(actor_id: str, user_id: str, org_id: str) -> None:
    set_member_status(actor_id, user_id, org_id, "active")


def offboard_member(actor_id: str, user_id: str, org_id: str,
                    successor_user_id: str | None = None) -> None:
    """One-click offboarding (10.5): suspend the member immediately (revokes
    access). Their underwrites/notes/models/POC results are org-owned and remain
    fully visible. If a successor is named, reassignment of open work is recorded
    (deal reassignment lands with the deal-table migration)."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE memberships SET status='suspended' WHERE user_id=%s AND org_id=%s",
            (user_id, org_id))
        _audit(cur, actor_id, "member.offboard", org_id,
               after={"user_id": user_id, "successor": successor_user_id})
        conn.commit()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_members(org_id: str) -> list[Member]:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.user_id, m.org_id, u.email, u.display_name,
                      m.role_preset, m.scope, m.status
                 FROM memberships m JOIN users u ON u.id = m.user_id
                WHERE m.org_id = %s
                ORDER BY (m.status='active') DESC, u.email""", (org_id,))
        return [Member(user_id=str(r["user_id"]), org_id=str(r["org_id"]), email=r["email"],
                       display_name=r["display_name"], role_preset=r["role_preset"],
                       scope=r["scope"], status=r["status"]) for r in cur.fetchall()]


def user_orgs(user_id: str, *, active_only: bool = True) -> list[dict]:
    """Which orgs a user belongs to (resolved at login, before org context)."""
    with pg.connection() as conn, conn.cursor() as cur:
        sql = ("""SELECT m.org_id, o.name, m.role_preset, m.status
                    FROM memberships m JOIN organizations o ON o.id = m.org_id
                   WHERE m.user_id = %s""")
        if active_only:
            sql += " AND m.status = 'active'"
        cur.execute(sql + " ORDER BY o.name", (user_id,))
        return [dict(org_id=str(r["org_id"]), name=r["name"],
                     role_preset=r["role_preset"], status=r["status"]) for r in cur.fetchall()]


def get_preset(key: str) -> dict | None:
    """One role_presets row (full grants/masks), e.g. for admin role preview."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM role_presets WHERE key = %s", (key,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_presets() -> list[dict]:
    """The role-preset library (key + human label + default scope) for admin
    dropdowns. Platform-maintained; the admin only ever picks a key (10.3)."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT key, label, maps_to, default_scope FROM role_presets ORDER BY label")
        return [dict(r) for r in cur.fetchall()]


def ensure_default_org(user_id: str, name: str = "My Organization") -> str:
    """Return the user's first active org, creating one (as Principal) if none.

    Lets the single-org pilot 'just work': the first admin gets an org without
    any setup, and the org model (Section 10) is live underneath from day one.
    """
    existing = user_orgs(user_id, active_only=True)
    if existing:
        return existing[0]["org_id"]
    return create_org(user_id, name)


def get_permissions(user_id: str, org_id: str) -> Permissions | None:
    """Resolve a user's effective permissions in an org (membership + preset).
    Returns None if the user is not an active member."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.scope, m.status, p.*
                 FROM memberships m JOIN role_presets p ON p.key = m.role_preset
                WHERE m.user_id = %s AND m.org_id = %s""", (user_id, org_id))
        row = cur.fetchone()
        if row is None or row["status"] != "active":
            return None
        return Permissions.from_preset_row(org_id, row, scope=row["scope"])


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _assert_preset_exists(key: str) -> None:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM role_presets WHERE key = %s", (key,))
        if cur.fetchone() is None:
            raise ValueError(f"unknown role preset {key!r}")


def _preset_default_scope(cur, key: str) -> str:
    cur.execute("SELECT default_scope FROM role_presets WHERE key = %s", (key,))
    row = cur.fetchone()
    return (row and row["default_scope"]) or "org_all"


def _update_member(actor_id: str, user_id: str, org_id: str, column: str,
                   value: str, action: str) -> None:
    assert column in ("role_preset", "scope", "status")
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {column} AS old FROM memberships WHERE user_id=%s AND org_id=%s",
                    (user_id, org_id))
        before = cur.fetchone()
        if before is None:
            raise LookupError("membership not found")
        cur.execute(f"UPDATE memberships SET {column}=%s WHERE user_id=%s AND org_id=%s",
                    (value, user_id, org_id))
        _audit(cur, actor_id, action, org_id,
               before={column: before["old"]}, after={column: value})
        conn.commit()


def _audit(cur, actor: str, action: str, org_id: str,
           before: dict | None = None, after: dict | None = None) -> None:
    cur.execute(
        """INSERT INTO audit_log (org_id, actor_user_id, action, target, before, after)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (org_id, actor, action, org_id,
         json.dumps(before) if before else None,
         json.dumps(after) if after else None))
