"""Microsoft Entra ID (Azure AD) authentication for the Streamlit workbench.

Architecture (per Brian's 2026-05-26 decisions):

* **Native MSAL in-app**, not Easy Auth. We need to read claims at the
  Streamlit-page layer to gate LP investors to their own deals; Easy Auth
  only forwards a header and can't filter inside the app.
* **Two role groups** defined in Entra:
    - ``eight-rock-internal``: Brian, Peter, future analysts. Full access.
    - ``lp-investor``: each named LP. Sees only Owner Portal pages keyed
      to their `lp_id` claim.
* **Authorization Code Flow with PKCE** (no secret in browser). MSAL handles
  this; we just expose the redirect endpoint and persist tokens in
  Streamlit's session_state.

Local-dev mode:
    When ``ER_AUTH_BACKEND=disabled`` (default for local dev), every call
    to ``current_user()`` returns a synthetic ``User`` with the
    ``eight-rock-internal`` role. This keeps Brian's local workflow zero-
    friction — no Entra round-trip when developing on his desktop.

Env vars (set in App Service Configuration; .env for local-dev test):
    ER_AUTH_BACKEND          "msal" | "disabled" (default "disabled")
    ER_AUTH_TENANT_ID        Entra tenant GUID
    ER_AUTH_CLIENT_ID        Web-app registration client ID (PUBLIC client)
    ER_AUTH_CLIENT_SECRET    Web-app secret (only for confidential server flow)
    ER_AUTH_REDIRECT_URI     e.g. https://eight-rock-workbench.azurewebsites.net/
    ER_AUTH_SCOPES           Space-separated, default "User.Read"
    ER_AUTH_ROLE_CLAIM       Token claim key holding role array; default "roles"
    ER_AUTH_LP_ID_CLAIM      Token claim key for LP's identity; default "lp_id"

Usage:

    from core.auth import current_user, require_role

    user = current_user()  # never returns None; falls back to anonymous
    if not user.has_role("eight-rock-internal"):
        st.error("Internal users only")
        st.stop()

    # Decorator form for whole-page gating:
    @require_role("eight-rock-internal")
    def render_calibration_panel(...): ...

Pages that LPs can hit must call ``user.lp_id`` to scope queries; the rest
of the workbench is gated to ``eight-rock-internal``.
"""

from __future__ import annotations

import datetime as dt
import functools
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# User dataclass — what `current_user()` returns
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class User:
    """Represents the user driving the current Streamlit session.

    `oid` is the Entra ObjectId — stable, opaque, the source of truth for
    "who is this." `email` and `name` are convenience but not unique.
    `roles` comes from the token's roles claim (configured per Entra app
    registration); `lp_id` is the per-LP scoping claim that maps an LP user
    to their specific Eight Rock investor record.
    """

    oid: str
    email: str
    name: str
    roles: tuple[str, ...] = field(default_factory=tuple)
    lp_id: str | None = None
    token_expires_at: dt.datetime | None = None
    is_anonymous: bool = False

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)

    @property
    def is_internal(self) -> bool:
        return self.has_role("eight-rock-internal")

    @property
    def is_lp(self) -> bool:
        return self.has_role("lp-investor")

    @property
    def display_name(self) -> str:
        return self.name or self.email or "User"


ANONYMOUS = User(
    oid="anonymous", email="", name="Anonymous", is_anonymous=True,
)


# Synthetic local-dev user — drops in for any code path that calls
# `current_user()` when ER_AUTH_BACKEND=disabled. Has the internal role so
# Brian can develop against the full workbench without an Entra round-trip.
LOCAL_DEV_USER = User(
    oid="local-dev",
    email=os.getenv("ER_LOCAL_DEV_EMAIL", "brian@eightrockcp.com"),
    name=os.getenv("ER_LOCAL_DEV_NAME", "Brian (local dev)"),
    roles=("eight-rock-internal",),
)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def _auth_backend() -> str:
    return (os.getenv("ER_AUTH_BACKEND") or "disabled").lower()


def is_auth_enabled() -> bool:
    return _auth_backend() == "msal"


# ---------------------------------------------------------------------------
# MSAL helpers — invoked from app.py + Streamlit pages
# ---------------------------------------------------------------------------

def _build_msal_app():
    """Construct the MSAL ConfidentialClientApplication.

    Streamlit runs server-side, so we can hold a client secret safely and
    use the confidential client flow. (If we ever ship a SPA-only build,
    swap to PublicClientApplication + PKCE.)
    """
    import msal
    tenant_id = os.environ["ER_AUTH_TENANT_ID"]
    client_id = os.environ["ER_AUTH_CLIENT_ID"]
    client_secret = os.environ["ER_AUTH_CLIENT_SECRET"]
    return msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )


def _scopes() -> list[str]:
    raw = os.getenv("ER_AUTH_SCOPES", "User.Read")
    return [s for s in raw.split() if s]


def _redirect_uri() -> str:
    return os.environ["ER_AUTH_REDIRECT_URI"]


def _role_claim_key() -> str:
    return os.getenv("ER_AUTH_ROLE_CLAIM", "roles")


def _lp_id_claim_key() -> str:
    return os.getenv("ER_AUTH_LP_ID_CLAIM", "lp_id")


def login_url(state: str = "") -> str:
    """Generate the Entra login URL for the current session.

    Stash `state` in session_state before redirecting and verify it on
    callback to prevent CSRF.
    """
    if not is_auth_enabled():
        raise RuntimeError("login_url() called with auth disabled")
    app = _build_msal_app()
    return app.get_authorization_request_url(
        scopes=_scopes(),
        redirect_uri=_redirect_uri(),
        state=state,
    )


def logout_url() -> str:
    tenant_id = os.environ["ER_AUTH_TENANT_ID"]
    post_logout = _redirect_uri()
    return (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={post_logout}"
    )


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an auth code for tokens. Raises on failure."""
    app = _build_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=_scopes(),
        redirect_uri=_redirect_uri(),
    )
    if "error" in result:
        raise RuntimeError(
            f"Token exchange failed: {result.get('error_description') or result['error']}"
        )
    return result


def user_from_token(token_result: dict[str, Any]) -> User:
    """Construct a User from MSAL token result. Expects an id_token_claims dict."""
    claims = token_result.get("id_token_claims") or {}
    role_key = _role_claim_key()
    raw_roles = claims.get(role_key) or []
    if isinstance(raw_roles, str):
        roles_t: tuple[str, ...] = (raw_roles,)
    else:
        roles_t = tuple(str(r) for r in raw_roles)
    expires_in = token_result.get("expires_in")
    expires_at = (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(expires_in))
        if expires_in else None
    )
    return User(
        oid=str(claims.get("oid") or claims.get("sub") or "unknown"),
        email=str(claims.get("email") or claims.get("preferred_username") or ""),
        name=str(claims.get("name") or ""),
        roles=roles_t,
        lp_id=claims.get(_lp_id_claim_key()),
        token_expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Streamlit integration — drop-in for app.py
# ---------------------------------------------------------------------------

def current_user() -> User:
    """Return the user driving the current Streamlit session.

    Behavior depends on ``ER_AUTH_BACKEND``:
      - ``disabled`` (local dev) → returns ``LOCAL_DEV_USER`` (internal role).
      - ``msal`` (production) → returns the user from Streamlit session_state
        if logged in, else ``ANONYMOUS``. Pages that require auth must check
        and either show login or stop.

    Never returns None — callers can always read ``user.roles`` safely.
    """
    if not is_auth_enabled():
        return LOCAL_DEV_USER
    try:
        import streamlit as st
    except ImportError:
        return ANONYMOUS
    user = st.session_state.get("er_user")
    if user is None:
        return ANONYMOUS
    # Token expiry check — force re-login if past expiry
    if user.token_expires_at is not None:
        now_utc = dt.datetime.now(dt.timezone.utc)
        exp = user.token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=dt.timezone.utc)
        if now_utc > exp:
            st.session_state.pop("er_user", None)
            return ANONYMOUS
    return user


def handle_auth_callback() -> User | None:
    """Process an OAuth callback. Returns the User on success, None on failure.

    Call this at the top of app.py before any page rendering. It reads the
    ``code`` and ``state`` query params; if present and valid, exchanges
    them for tokens and stores the User in session_state.
    """
    if not is_auth_enabled():
        return None
    try:
        import streamlit as st
    except ImportError:
        return None
    qp = st.query_params
    code = qp.get("code")
    if not code:
        return None
    expected_state = st.session_state.get("er_auth_state")
    received_state = qp.get("state", "")
    if expected_state and received_state != expected_state:
        st.error("Auth state mismatch — please sign in again.")
        return None
    try:
        token_result = exchange_code_for_token(code)
    except Exception as e:
        st.error(f"Auth callback failed: {e}")
        return None
    user = user_from_token(token_result)
    st.session_state["er_user"] = user
    # Clear the code/state from the URL so a refresh doesn't re-process
    st.query_params.clear()
    return user


def render_login_gate() -> None:
    """Render a Streamlit page that shows 'Sign in with Microsoft'.

    Call this from app.py when ``current_user().is_anonymous`` and the
    page being requested isn't public.
    """
    import streamlit as st
    import secrets
    state = secrets.token_urlsafe(16)
    st.session_state["er_auth_state"] = state
    url = login_url(state=state)
    st.markdown(
        f"""
        <div style="max-width:480px;margin:80px auto;padding:48px 32px;
                    background:#fff;border:1px solid #e1e7f0;border-radius:10px;
                    text-align:center">
          <div style="font-size:24px;font-weight:700;margin-bottom:8px">
            Eight Rock Workbench
          </div>
          <div style="color:#6b7588;font-size:14px;margin-bottom:32px">
            Sign in with your Microsoft account to continue.
          </div>
          <a href="{url}"
             style="display:inline-block;padding:12px 24px;background:#0078d4;
                    color:#fff;text-decoration:none;border-radius:6px;
                    font-weight:600;font-size:14px">
            Sign in with Microsoft
          </a>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Role-gating decorator — for whole-page renderers
# ---------------------------------------------------------------------------

def require_role(*roles: str) -> Callable:
    """Decorator: wrap a page renderer so it stops unless the user has the role.

    Example::

        @require_role("eight-rock-internal")
        def render_calibration_panel(...): ...

    If the user has the role, the function runs as normal. Otherwise a
    minimal "access denied" panel is rendered and Streamlit halts.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            import streamlit as st
            user = current_user()
            if not user.has_any_role(*roles):
                st.error(
                    f"This page is restricted to: {', '.join(roles)}. "
                    f"You're signed in as {user.display_name} "
                    f"(roles: {', '.join(user.roles) or 'none'})."
                )
                st.stop()
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Convenience: scope a deal query to the current LP if they're an LP user
# ---------------------------------------------------------------------------

def lp_scope_filter() -> dict | None:
    """Return a query filter dict if the current user is an LP investor.

    Used by Owner Portal pages to enforce "LP sees their own deals only."
    Returns ``{"lp_id": <id>}`` for LP users and ``None`` for internal
    users (who see everything). Callers pass this into the LP ledger query.
    """
    user = current_user()
    if user.is_lp and user.lp_id:
        return {"lp_id": user.lp_id}
    return None
