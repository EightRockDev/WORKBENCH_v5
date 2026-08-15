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


_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def _bound_to_loopback() -> bool:
    """True only when this server refuses connections from other machines.

    Read from Streamlit's resolved config, so it reflects what the server is
    actually bound to rather than what someone meant to pass. Unknown or
    unreadable counts as NOT loopback: the safe answer when in doubt is the
    one that keeps the sign-in gate on.
    """
    try:
        from streamlit import config as _st_config

        return str(_st_config.get_option("server.address") or "").strip() in _LOOPBACK
    except Exception:
        return False


def local_console_login_enabled() -> bool:
    """Owner-at-the-keyboard bypass: `ER_LOCAL_LOGIN=1` on a loopback bind.

    Sign-in depends on two things outside this app - an identity provider on
    the internet and a reverse proxy in front. When either misbehaves the
    owner is locked out of his own machine's data with no way in, which is
    exactly what happened on 2026-08-15.

    This is the door that cannot be taken away by anything outside the
    machine. It opens ONLY when both hold:

      * `ER_LOCAL_LOGIN=1` is set - deliberate, by a launcher whose whole
        purpose is this;
      * the server is bound to loopback, so the only person who can reach it
        is someone already sitting at the machine, who by then has the files
        and the database anyway.

    The loopback condition is what makes this safe rather than a backdoor:
    the environment variable alone does nothing on a server that answers the
    network. Setting it on the LAN or Caddy-fronted instances has no effect,
    by construction, not by convention.
    """
    return os.getenv("ER_LOCAL_LOGIN") == "1" and _bound_to_loopback()


def require_passcode(st) -> None:
    """Shared-passcode gate for network exposure before real sign-in exists.

    Active whenever ``ER_APP_PASSCODE`` is set (written by the LAN-service
    installer). Runs BEFORE any auth-mode branching, so exposing the app on
    the office network is never wide-open even in dev-login or legacy mode.
    Once OIDC is configured the real login supersedes this; the variable can
    then be removed from ``.env``.
    """
    import hashlib
    import hmac

    expected = os.getenv("ER_APP_PASSCODE", "").strip()
    if not expected:
        return
    if st.session_state.get("_passcode_ok"):
        return
    # Remember-this-device (owner ask 2026-08-03: "enter it once"). A
    # correct entry stamps a derived token into the URL; the browser keeps
    # query params across refreshes and a bookmark keeps them forever, so
    # the prompt happens once per device instead of once per tab-session.
    # The token is an HMAC derivation - the passcode itself never appears
    # in the URL - and changing the passcode invalidates every remembered
    # device at once. Passcode-tier convenience, not auth: real per-user
    # login (OIDC, section 9.4) supersedes this whole gate when configured.
    device_token = passcode_device_token(expected)

    # 1) A real browser COOKIE is the durable path: it survives new tabs,
    #    fresh ?prop= URLs, and refreshes - which the earlier query-param
    #    token did NOT (clicking a property link dropped ?k=, re-prompting
    #    every pull, the exact complaint). st.context.cookies is read-only,
    #    so the cookie is SET by a tiny JS snippet on unlock (below).
    if _cookie_matches(st, device_token):
        st.session_state["_passcode_ok"] = True
        return
    # 2) Query-param token still honored (bookmark path, older Streamlit).
    try:
        if st.query_params.get("k") == device_token:
            st.session_state["_passcode_ok"] = True
            return
    except Exception:
        pass
    st.markdown("## \U0001f512 Eight Rock Workbench")
    st.caption("Enter the workbench passcode to continue. "
               "This device stays signed in afterward.")
    with st.form("passcode_gate"):
        entered = st.text_input("Passcode", type="password", key="_passcode_in")
        submitted = st.form_submit_button("Enter", type="primary")
    if submitted and hmac.compare_digest(entered.strip().encode(), expected.encode()):
        st.session_state["_passcode_ok"] = True
        _set_passcode_cookie(st, device_token)
        try:
            st.query_params["k"] = device_token   # belt-and-suspenders
        except Exception:
            pass
        return          # unlocked - render the app in this same run
    if submitted:
        st.error("Wrong passcode.")
    st.stop()


# Days a remembered device stays signed in without re-entering the passcode.
PASSCODE_COOKIE = "er_pc"
PASSCODE_COOKIE_DAYS = 30


def passcode_device_token(expected: str) -> str:
    """HMAC derivation of the passcode - what a remembered device presents.

    The passcode itself never leaves the server: the cookie and the URL only
    ever carry this derivation, and changing the passcode changes the token,
    signing every remembered device out at once.
    """
    import hashlib
    import hmac
    return hmac.new(expected.encode(), b"8r-device-v1",
                    hashlib.sha256).hexdigest()[:20]


def _cookie_matches(st, device_token: str) -> bool:
    try:
        return st.context.cookies.get(PASSCODE_COOKIE) == device_token
    except Exception:
        return False        # older Streamlit / no cookie context


def _set_passcode_cookie(st, device_token: str) -> None:
    """Write the remember-me cookie from the browser.

    st.context.cookies is read-only, so the cookie is set client-side. The
    component iframe is same-origin with the app (both on the office host),
    so writing to the parent document's cookie jar reaches the real page.
    SameSite=Lax keeps it on same-site navigations (every ?prop= click)
    without sending it cross-site. Best-effort: if the write is blocked the
    session_state + query-param paths still cover this tab.
    """
    max_age = PASSCODE_COOKIE_DAYS * 24 * 3600
    js = (
        "<script>try{(window.parent||window).document.cookie="
        f"'{PASSCODE_COOKIE}={device_token}; Max-Age={max_age}; Path=/; "
        "SameSite=Lax';}catch(e){}</script>")
    try:
        import streamlit.components.v1 as components
        components.html(js, height=0, width=0)
    except Exception:
        pass


def resolve_user(st) -> AdminUser | None:
    """Return the active user, or None when the app should run ungated.

    May call ``st.stop()`` internally (passcode gate / login / pending-approval
    screens).
    """
    require_passcode(st)  # network-exposure gate; no-op unless configured

    if not pg.is_configured():
        return None  # legacy single-user mode; no pilot auth

    if local_console_login_enabled():
        # Checked BEFORE OIDC on purpose: the whole point is a way in when
        # the identity provider or the reverse proxy is the thing that is
        # broken. Loopback-bound only - see local_console_login_enabled().
        from core import user_admin

        return user_admin.sync_user_on_login(
            "local|console",
            os.getenv("ER_DEV_EMAIL", "owner@eight-rock.local"),
            "Owner (this computer)",
        )

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


def resolve_org_context(user: AdminUser | None,
                        preferred_org_id: str | None = None,
                        ) -> tuple[str | None, object | None]:
    """Resolve the user's active org + effective permissions (Section 10).

    For the single-org pilot, an admin with no org yet gets a default one
    auto-created (they become Principal), so the org/role model is live without
    any setup. ``preferred_org_id`` (the admin-tab org switcher's choice,
    2026-08-09) wins when — and only when — the user is an ACTIVE member of
    that org; an invalid or stale preference silently falls back to the
    default and never grants access. Returns (org_id, Permissions) or
    (None, None) in ungated mode.
    """
    if user is None:
        return None, None
    from core import orgs

    try:
        # Admins bootstrap a default org if they have none; others must be enrolled.
        if user.is_admin:
            org_id = orgs.ensure_default_org(user.id, "Eight Rock Capital")
        else:
            user_org_list = orgs.user_orgs(user.id, active_only=True)
            org_id = user_org_list[0]["org_id"] if user_org_list else None
        if preferred_org_id and preferred_org_id != org_id:
            memberships = {o["org_id"]
                           for o in orgs.user_orgs(user.id, active_only=True)}
            if preferred_org_id in memberships:
                org_id = preferred_org_id
        if org_id is None:
            return None, None
        return org_id, orgs.get_permissions(user.id, org_id)
    except Exception as exc:  # pragma: no cover - surfaced as a soft banner
        # Most commonly a schema drift (DB not migrated after a git pull).
        # Degrade gracefully: the app still loads; the multi-tenant features are
        # simply inactive until the DB is migrated. Signal it for a UI hint.
        import os

        if os.environ.get("ER_DEBUG"):
            import traceback
            traceback.print_exc()
        return None, ("__schema_error__", str(exc))


def render_account_chip(st, user: AdminUser | None) -> None:
    """Small 'signed in as' caption + logout, shown only when truly logged in."""
    if user is None:
        return
    with st.sidebar:
        st.divider()
        st.caption(f"Signed in: **{user.display_name or user.email}** · {user.platform_role}")
        if auth_configured(st):
            st.button("Log out", key="_logout", on_click=st.logout)
