"""User administration & onboarding over PostgreSQL (spec Section 9.4).

Identity lives in the provider (Auth0 / Entra, via Streamlit native OIDC —
``st.login``/``st.user``); *authorization* lives here in the Workbench. On each
login we upsert the provider identity into the ``users`` table (keyed to the
provider ``sub``) and apply the safe-by-default onboarding rule:

    FR-9.4.3  The first authenticated user is auto-promoted to admin; everyone
              after lands as trial/pending and sees only a "pending approval"
              screen until an admin approves them.

RBAC is enforced server-side on every page/action (FR-9.4.4, SR-3.2), never by
hiding UI. Every membership/permission change is written to the append-only
audit log (SR-3.1).

This module operates on the non-RLS ``users`` / ``audit_log`` tables, so it uses
a plain :func:`data.pg.connection`. The org-scoped role model (Section 10) sits
on top of this and is a later work order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data import pg

# Platform-level roles for the single-org pilot (Section 9.4.1). The richer
# org-scoped role presets (Section 10.3) are seeded separately in role_presets.
PLATFORM_ROLES = ("admin", "internal", "lp", "trial")
STATUSES = ("invited", "active", "suspended")


@dataclass(frozen=True)
class AdminUser:
    """A row of the ``users`` table, as the admin page sees it."""

    id: str
    idp_sub: str
    email: str
    display_name: str | None
    platform_role: str
    status: str
    last_login: Any | None

    @property
    def is_admin(self) -> bool:
        return self.platform_role == "admin"

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_pending(self) -> bool:
        """Trial user still awaiting admin approval (FR-9.4.3)."""
        return self.status != "active"


# ---------------------------------------------------------------------------
# Login-time upsert + safe-by-default onboarding
# ---------------------------------------------------------------------------

def sync_user_on_login(idp_sub: str, email: str, display_name: str | None = None) -> AdminUser:
    """Upsert the provider identity and return the local user (FR-9.4.1/9.4.3).

    - Existing user: refresh email/name, stamp ``last_login``.
    - Brand-new user: if they are the *first* user in the system, create them as
      ``admin`` + ``active``; otherwise ``trial`` + ``invited`` (pending), so a
      random signup can never see deal data until approved.
    """
    with pg.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE idp_sub = %s", (idp_sub,))
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    """UPDATE users
                          SET email = %s,
                              display_name = COALESCE(%s, display_name),
                              last_login = now()
                        WHERE idp_sub = %s
                    RETURNING id, idp_sub, email, display_name, platform_role, status, last_login""",
                    (email, display_name, idp_sub),
                )
                row = cur.fetchone()
                conn.commit()
                return _row_to_user(row)

            # New user — first one bootstraps as admin (FR-9.4.3).
            cur.execute("SELECT count(*) AS n FROM users")
            is_first = cur.fetchone()["n"] == 0
            role, status = ("admin", "active") if is_first else ("trial", "invited")

            cur.execute(
                """INSERT INTO users (idp_sub, email, display_name, platform_role, status, last_login)
                   VALUES (%s, %s, %s, %s, %s, now())
                RETURNING id, idp_sub, email, display_name, platform_role, status, last_login""",
                (idp_sub, email, display_name, role, status),
            )
            row = cur.fetchone()
            _audit(cur, actor=row["id"], action="user.bootstrap" if is_first else "user.signup",
                   target=row["id"], after={"platform_role": role, "status": status})
            conn.commit()
            return _row_to_user(row)


# ---------------------------------------------------------------------------
# Admin operations (role = admin only — caller must gate with require_admin)
# ---------------------------------------------------------------------------

def list_users() -> list[AdminUser]:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, idp_sub, email, display_name, platform_role, status, last_login
                 FROM users ORDER BY (status='active') DESC, created_at""")
        return [_row_to_user(r) for r in cur.fetchall()]


def set_role(actor_id: str, user_id: str, role: str) -> None:
    if role not in PLATFORM_ROLES:
        raise ValueError(f"unknown role {role!r}; must be one of {PLATFORM_ROLES}")
    _mutate(actor_id, user_id, "platform_role", role, action="user.set_role")


def set_status(actor_id: str, user_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; must be one of {STATUSES}")
    _mutate(actor_id, user_id, "status", status, action="user.set_status")


def approve_user(actor_id: str, user_id: str) -> None:
    """Approve a pending signup: trial -> internal, invited -> active."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT platform_role, status FROM users WHERE id = %s", (user_id,))
        before = cur.fetchone()
        if before is None:
            raise LookupError(f"user {user_id} not found")
        new_role = "internal" if before["platform_role"] == "trial" else before["platform_role"]
        cur.execute(
            "UPDATE users SET platform_role = %s, status = 'active' WHERE id = %s",
            (new_role, user_id),
        )
        _audit(cur, actor=actor_id, action="user.approve", target=user_id,
               before=dict(before), after={"platform_role": new_role, "status": "active"})
        conn.commit()


def suspend_user(actor_id: str, user_id: str) -> None:
    set_status(actor_id, user_id, "suspended")


# ---------------------------------------------------------------------------
# Gates (server-side RBAC — FR-9.4.4)
# ---------------------------------------------------------------------------

def get_user(idp_sub: str) -> AdminUser | None:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, idp_sub, email, display_name, platform_role, status, last_login
                 FROM users WHERE idp_sub = %s""", (idp_sub,))
        row = cur.fetchone()
        return _row_to_user(row) if row else None


def require_admin(user: AdminUser) -> None:
    """Raise unless ``user`` is an active admin. Enforced server-side."""
    if not (user.is_admin and user.is_active):
        raise PermissionError("admin role required")


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _row_to_user(row: dict[str, Any]) -> AdminUser:
    return AdminUser(
        id=str(row["id"]), idp_sub=row["idp_sub"], email=row["email"],
        display_name=row["display_name"], platform_role=row["platform_role"],
        status=row["status"], last_login=row["last_login"],
    )


def _mutate(actor_id: str, user_id: str, column: str, value: str, *, action: str) -> None:
    # `column` is a fixed literal from the callers above, never user input.
    assert column in ("platform_role", "status")
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {column} AS old FROM users WHERE id = %s", (user_id,))
        before = cur.fetchone()
        if before is None:
            raise LookupError(f"user {user_id} not found")
        cur.execute(f"UPDATE users SET {column} = %s WHERE id = %s", (value, user_id))
        _audit(cur, actor=actor_id, action=action, target=user_id,
               before={column: before["old"]}, after={column: value})
        conn.commit()


def _audit(cur, *, actor: str, action: str, target: str,
           before: dict | None = None, after: dict | None = None,
           reason: str | None = None) -> None:
    import json
    cur.execute(
        """INSERT INTO audit_log (actor_user_id, action, target, before, after, reason)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (actor, action, str(target),
         json.dumps(before) if before is not None else None,
         json.dumps(after) if after is not None else None, reason),
    )
