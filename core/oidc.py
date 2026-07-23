"""Bridge between Streamlit native OIDC and the Workbench users table (§9.4).

The spec's recommended integration layer is Streamlit's built-in OpenID Connect
login (``st.login`` / ``st.user`` / ``st.logout``, native since Streamlit 1.42)
with a hosted provider (Auth0 or Entra External ID) behind it, configured in a
small ``[auth]`` block in ``.streamlit/secrets.toml``. This module turns the
provider identity that Streamlit exposes into a local :class:`AdminUser`,
applying the safe-by-default onboarding rule and the pending-approval gate.

Flow (call :func:`gate` at the top of the app):
    1. If not logged in -> show a login button (``st.login``).
    2. On login, upsert the identity (``sync_user_on_login``) — first user is
       admin, everyone else is pending (FR-9.4.3).
    3. If the local user is still pending -> render the pending-approval screen
       and stop; a random signup never reaches deal data.
    4. Otherwise return the active :class:`AdminUser` for RBAC downstream.
"""

from __future__ import annotations

from core import user_admin
from core.user_admin import AdminUser


def _provider_identity(st) -> tuple[str, str, str | None] | None:
    """Extract (sub, email, name) from st.user, or None if not logged in."""
    user = getattr(st, "user", None)
    if not user or not getattr(user, "is_logged_in", False):
        return None
    # st.user exposes standard OIDC claims; sub is the stable provider id.
    sub = user.get("sub") or user.get("email")
    email = user.get("email", "")
    name = user.get("name")
    if not sub:
        return None
    return str(sub), str(email), (str(name) if name else None)


def gate(st) -> AdminUser | None:
    """Enforce login + approval. Returns the active user, or None after halting.

    Renders login / pending screens itself and calls ``st.stop()`` when the
    session should not proceed, so callers can simply do::

        user = core.oidc.gate(st)
        # from here down, `user` is an active, approved AdminUser
    """
    ident = _provider_identity(st)
    if ident is None:
        st.title("Eight Rock Workbench")
        st.caption("Sign in to continue.")
        st.button("Log in", type="primary", on_click=st.login)
        st.stop()
        return None

    sub, email, name = ident
    user = user_admin.sync_user_on_login(sub, email, name)

    if user.is_pending:
        _render_pending_screen(st, user)
        st.stop()
        return None

    if user.status == "suspended":
        st.error("Your access has been suspended. Contact your administrator.")
        st.button("Log out", on_click=st.logout)
        st.stop()
        return None

    return user


def _render_pending_screen(st, user: AdminUser) -> None:
    """FR-9.4.3 — a brand-new signup sees only this until an admin approves."""
    st.title("Eight Rock Workbench")
    st.info(
        f"Thanks, {user.display_name or user.email}. Your account is **pending "
        "approval**. An administrator will grant you access shortly — you'll "
        "see deal data once you're approved."
    )
    st.button("Log out", on_click=st.logout)
