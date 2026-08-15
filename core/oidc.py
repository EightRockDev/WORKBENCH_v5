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


# ---------------------------------------------------------------------------
# Unauthenticated screens (login / pending / suspended)
# ---------------------------------------------------------------------------
# These three are the only surfaces a person sees before they are inside the
# product, so they carry the whole first impression. They deliberately share
# one shell: a stranger who signs up and lands on "pending" should feel like
# they hit a locked door in the same building, not a different building.
#
# Self-contained on purpose - no dependency on the v2 theme module, which is
# loaded further down the app behind the gate. The two brand values are
# duplicated from config.COLORS (`ink` #0A1628, `ac` #C8900A) rather than
# imported, so that a failure anywhere in config cannot turn the login page
# into an unstyled white screen; a login page must render even when the app
# behind it cannot.

_AUTH_INK = "#0A1628"      # config COLORS-derived deep navy
_AUTH_GOLD = "#C8900A"     # Eight Rock gold
_AUTH_GOLD_DK = "#a37102"


def _auth_css(st) -> None:
    """Full-bleed branded chrome for the pre-login screens."""
    st.markdown(
        f"""<style>
      /* Streamlit chrome has no place on a sign-in screen. */
      #MainMenu, header[data-testid="stHeader"], footer {{ display: none !important; }}
      section[data-testid="stSidebar"] {{ display: none !important; }}

      .stApp {{
        background:
          radial-gradient(1100px 620px at 50% -12%,
                          rgba(200,144,10,0.20) 0%,
                          rgba(200,144,10,0.05) 42%,
                          rgba(0,0,0,0) 72%),
          linear-gradient(178deg, {_AUTH_INK} 0%, #060D18 100%);
      }}
      .block-container {{
        max-width: 460px !important;
        padding-top: 11vh !important;
        padding-bottom: 4rem !important;
      }}

      .er-auth {{ text-align: center; }}
      .er-auth-mark {{
        width: 60px; height: 60px; margin: 0 auto 26px;
        display: flex; align-items: center; justify-content: center;
        border-radius: 15px;
        background: linear-gradient(160deg, {_AUTH_GOLD} 0%, {_AUTH_GOLD_DK} 100%);
        color: {_AUTH_INK};
        font: 700 21px/1 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
        letter-spacing: 0.02em;
        box-shadow: 0 10px 34px rgba(200,144,10,0.32);
      }}
      .er-auth-word {{
        margin: 0;
        color: #ffffff;
        font: 600 27px/1.15 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
        letter-spacing: 0.24em;
        text-indent: 0.24em;   /* letter-spacing pads the right; re-center */
      }}
      .er-auth-tag {{
        margin: 15px 0 0;
        color: {_AUTH_GOLD};
        font: 600 10.5px/1.5 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
        letter-spacing: 0.19em;
        text-transform: uppercase;
      }}
      .er-auth-rule {{
        height: 1px; margin: 34px auto 30px; max-width: 200px;
        background: linear-gradient(90deg,
          rgba(255,255,255,0) 0%, rgba(255,255,255,0.20) 50%,
          rgba(255,255,255,0) 100%);
      }}
      .er-auth-lede {{
        margin: 0 0 22px;
        color: rgba(255,255,255,0.72);
        font: 400 14.5px/1.65 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
      }}
      .er-auth-note {{
        margin: 26px auto 0; max-width: 380px;
        color: rgba(255,255,255,0.62);
        font: 400 13.5px/1.7 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
      }}
      .er-auth-foot {{
        margin-top: 40px;
        color: rgba(255,255,255,0.34);
        font: 500 10.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
        letter-spacing: 0.11em; text-transform: uppercase;
      }}

      /* The button must stay a real Streamlit widget - st.login is a
         callback, so it cannot be hand-rolled HTML. Style it instead. */
      .stButton > button {{
        width: 100%;
        padding: 0.72rem 1rem;
        border: 0;
        border-radius: 11px;
        background: linear-gradient(160deg, {_AUTH_GOLD} 0%, {_AUTH_GOLD_DK} 100%);
        color: {_AUTH_INK};
        font-weight: 700;
        font-size: 14.5px;
        letter-spacing: 0.03em;
        box-shadow: 0 8px 26px rgba(200,144,10,0.30);
      }}
      .stButton > button:hover {{
        filter: brightness(1.07);
        box-shadow: 0 11px 32px rgba(200,144,10,0.42);
      }}
      .stButton > button:focus-visible {{
        outline: 3px solid #ffffff;
        outline-offset: 3px;
      }}
      @media (prefers-reduced-motion: no-preference) {{
        .stButton > button {{ transition: filter .16s ease, box-shadow .16s ease; }}
      }}
    </style>""",
        unsafe_allow_html=True,
    )


def _auth_hero(st, lede: str) -> None:
    """Mark + wordmark + tagline + lede. Shared by all three screens."""
    try:
        from config import WORKBENCH_VERSION as _ver
    except Exception:
        _ver = ""
    _auth_css(st)
    st.markdown(
        '<div class="er-auth">'
        '<div class="er-auth-mark">8R</div>'
        '<h1 class="er-auth-word">WORKBENCH</h1>'
        '<p class="er-auth-tag">Where Eight Rock breaks ground.</p>'
        '<div class="er-auth-rule"></div>'
        f'<p class="er-auth-lede">{lede}</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    return _ver


def _auth_footer(st, ver: str) -> None:
    # The running version, on the one screen that renders before login. The
    # owner spent 2026-08-15 unable to tell whether a deploy had actually
    # taken effect; this is the cheapest possible answer to that question.
    st.markdown(
        f'<div class="er-auth foot"><div class="er-auth-foot">'
        f'Eight Rock Capital Partners{" &middot; " + ver if ver else ""}'
        f'</div></div>',
        unsafe_allow_html=True,
    )


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
        ver = _auth_hero(st, "Sign in to continue.")
        st.button("Log in", type="primary", on_click=st.login)
        _auth_footer(st, ver)
        st.stop()
        return None

    sub, email, name = ident
    user = user_admin.sync_user_on_login(sub, email, name)

    if user.is_pending:
        _render_pending_screen(st, user)
        st.stop()
        return None

    if user.status == "suspended":
        ver = _auth_hero(
            st, "Your access has been suspended. "
                "Contact your administrator to restore it.")
        st.button("Log out", on_click=st.logout)
        _auth_footer(st, ver)
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
