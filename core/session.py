"""Session resolution — who is the user, and do we gate? (Sections 9.4 / 11).

Bridges the Streamlit app to the pilot auth layer WITHOUT making the
deterministic core depend on it. Behavior is chosen by configuration so the app
degrades gracefully at every stage of the rollout:

- **No Postgres** (`DATABASE_URL` unset): legacy ungated mode — returns None; the
  app runs as the single-user tool it has always been. (Keeps the core
  LLM-/infra-free per Section 11, and keeps local dev zero-friction.)
- **Postgres + OIDC configured** (`[auth]` in secrets): full login + approval
  gate via ``core.oidc.gate`` (Section 9.4). Random signups land on the
  pending-approval screen.
- **Postgres, no OIDC yet, `ER_DEV_LOGIN=1`**: dev bypass — a synthetic local
  admin so the app and admin page are usable *before* Auth0/Entra is wired.
  Pilot/pre-public only; never set this once real sign-in is configured.
- **Postgres, no OIDC, no dev flag**: the app runs ungated but the admin panel
  is hidden.
"""

from __future__ import annotations

import os

from core.user_admin import AdminUser
from data import pg


def auth_configured(st) -> bool:
    """True when an `[auth]` OIDC block is present in Streamlit secrets."""
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def resolve_user(st) -> AdminUser | None:
    """Return the active user, or None when the app should run ungated.

    May call ``st.stop()`` internally (login / pending-approval screens) when a
    real OIDC provider is configured.
    """
    if not pg.is_configured():
        return None  # legacy single-user mode; no pilot auth

    if auth_configured(st):
        from core import oidc

        return oidc.gate(st)  # may st.stop() for login/pending/suspended

    if os.getenv("ER_DEV_LOGIN") == "1":
        from core import user_admin

        return user_admin.sync_user_on_login(
            "dev|local",
            os.getenv("ER_DEV_EMAIL", "dev@eight-rock.local"),
            "Dev (local)",
        )

    return None  # Postgres present but sign-in not configured; admin hidden


def resolve_org_context(user: AdminUser | None) -> tuple[str | None, object | None]:
    """Resolve the user's active org + effective permissions (Section 10).

    For the single-org pilot, an admin with no org yet gets a default one
    auto-created (they become Principal), so the org/role model is live without
    any setup. Returns (org_id, Permissions) or (None, None) in ungated mode.
    """
    if user is None:
        return None, None
    from core import orgs

    # Admins bootstrap a default org if they have none; others must be enrolled.
    if user.is_admin:
        org_id = orgs.ensure_default_org(user.id, "Eight Rock Capital")
    else:
        user_org_list = orgs.user_orgs(user.id, active_only=True)
        org_id = user_org_list[0]["org_id"] if user_org_list else None
    if org_id is None:
        return None, None
    return org_id, orgs.get_permissions(user.id, org_id)


def render_account_chip(st, user: AdminUser | None) -> None:
    """Small 'signed in as' caption + logout, shown only when truly logged in."""
    if user is None:
        return
    with st.sidebar:
        st.divider()
        st.caption(f"Signed in: **{user.display_name or user.email}** · {user.platform_role}")
        if auth_configured(st):
            st.button("Log out", key="_logout", on_click=st.logout)
