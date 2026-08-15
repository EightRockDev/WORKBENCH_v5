"""Stop sign-in failures from being a dead-end "Internal Server Error".

Owner locked out, 2026-08-15. The screen said only `Internal Server Error`
at `/oauth2callback` - no reason, no way forward, nothing in any log the
owner would ever open.

That page comes from Streamlit itself, not from this app. In the version
pinned here (1.57.0) the callback route ends:

    client, _ = _create_oauth_client(provider)
    token = await client.authorize_access_token(request)   # <- unguarded
    ...

`authorize_access_token` talks to the identity provider, and every way that
call can fail raises straight out of the route handler, so Starlette returns
its default 500 body. The failure modes are ordinary, not exotic:

  * the one-time sign-in code was already spent - refreshing the callback
    page, or hitting Back onto it, replays a code the provider has retired;
  * the provider rejected the client credentials;
  * the provider was unreachable, or TLS to it failed;
  * the redirect URI the provider was given does not match the one in
    `secrets.toml`.

All four look identical from the browser, which is why this cost a day.

This module wraps that one route handler so a failed exchange renders a
readable page - what went wrong, in the owner's words, and a button that
starts over - and prints the real traceback to the app's output for us.

Two facts make the wrap safe and effective:

  * Streamlit's route closure resolves `_auth_callback` from module globals
    on every request (`return await _auth_callback(request, base_url)`), so
    replacing the module attribute takes effect even though the routing
    table was built before this app module was imported.
  * It is idempotent, and it is a no-op on Streamlit versions that already
    guard the call (1.61 added a try/except there). Upgrading Streamlit
    later does not double-handle anything.

Nothing here weakens the check: a failed sign-in still fails. The user just
finds out why.
"""

from __future__ import annotations

import html
import traceback

_INK = "#0A1628"
_GOLD = "#C8900A"

_WRAPPED_FLAG = "_er_auth_callback_wrapped"


def _plain_reason(exc: BaseException) -> tuple[str, str]:
    """(headline, what to do) for one failed token exchange, in plain words."""
    blob = f"{type(exc).__name__} {exc}".lower()

    if "invalid_grant" in blob or "expired" in blob or "already" in blob:
        return (
            "That sign-in link had already been used.",
            "Sign-in links are good for one use only. Refreshing this page or "
            "pressing Back re-uses the old one. Start again with the button "
            "below and it will go through.",
        )
    if "invalid_client" in blob or "unauthorized_client" in blob:
        return (
            "The sign-in provider rejected the workbench's credentials.",
            "This is a settings problem, not something you did. Starting "
            "again will not fix it - it needs the provider settings checked.",
        )
    if "redirect_uri" in blob or "redirect uri" in blob:
        return (
            "The sign-in provider did not recognise the address it was "
            "sent back to.",
            "This is a settings problem, not something you did. It needs the "
            "provider's allowed addresses checked.",
        )
    if any(k in blob for k in ("timeout", "timed out", "connect", "ssl",
                              "certificate", "resolve", "network", "dns")):
        return (
            "The workbench could not reach the sign-in provider.",
            "Usually the internet connection dropping for a moment. Wait a "
            "few seconds and start again.",
        )
    return (
        "Sign-in did not finish.",
        "Start again with the button below. If it happens twice in a row, "
        "the details at the bottom of this page say why.",
    )


def _error_page(exc: BaseException, base_url: str) -> str:
    headline, advice = _plain_reason(exc)
    detail = html.escape(f"{type(exc).__name__}: {exc}")
    home = base_url if base_url.startswith("/") else "/" + base_url
    if not home.endswith("/"):
        home += "/"
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Sign-in - Eight Rock Workbench</title>
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;
 justify-content:center;background:
 radial-gradient(1100px 620px at 50% -12%,rgba(200,144,10,.20) 0%,
 rgba(200,144,10,.05) 42%,rgba(0,0,0,0) 72%),
 linear-gradient(178deg,{_INK} 0%,#060D18 100%);
 font-family:ui-sans-serif,-apple-system,'Segoe UI',sans-serif;color:#fff;">
<div style="max-width:460px;padding:40px 24px;text-align:center;">
  <div style="width:60px;height:60px;margin:0 auto 26px;display:flex;
   align-items:center;justify-content:center;border-radius:15px;
   background:linear-gradient(160deg,{_GOLD} 0%,#a37102 100%);color:{_INK};
   font:700 21px/1 ui-sans-serif,sans-serif;">8R</div>
  <h1 style="margin:0;font:600 20px/1.35 ui-sans-serif,sans-serif;">
    {html.escape(headline)}</h1>
  <p style="margin:16px 0 0;color:rgba(255,255,255,.72);
   font:400 14.5px/1.65 ui-sans-serif,sans-serif;">{html.escape(advice)}</p>
  <a href="{html.escape(home)}" style="display:block;margin:30px 0 0;
   padding:.72rem 1rem;border-radius:9px;text-decoration:none;
   background:linear-gradient(160deg,{_GOLD} 0%,#a37102 100%);color:{_INK};
   font:600 14.5px/1.4 ui-sans-serif,sans-serif;">Start sign-in again</a>
  <details style="margin-top:34px;text-align:left;">
    <summary style="cursor:pointer;color:rgba(255,255,255,.34);
     font:500 10.5px/1.6 ui-monospace,monospace;letter-spacing:.11em;
     text-transform:uppercase;">Technical detail</summary>
    <pre style="margin:12px 0 0;padding:12px;border-radius:8px;
     background:rgba(0,0,0,.32);color:rgba(255,255,255,.62);
     font:400 11.5px/1.55 ui-monospace,monospace;white-space:pre-wrap;
     word-break:break-word;">{detail}</pre>
  </details>
</div></body></html>"""


def install() -> bool:
    """Wrap Streamlit's OAuth callback route. Returns True if wrapped.

    Never raises: a diagnostic aid must not be able to take down the app it
    is diagnosing. A Streamlit version that renames or moves the route
    simply leaves the app exactly as it was.
    """
    try:
        from streamlit.web.server.starlette import (  # type: ignore
            starlette_auth_routes as routes,
        )
    except Exception:
        return False

    original = getattr(routes, "_auth_callback", None)
    if original is None or getattr(routes, _WRAPPED_FLAG, False):
        return False

    async def _guarded_auth_callback(request, base_url):  # type: ignore[no-untyped-def]
        try:
            return await original(request, base_url)
        except Exception as exc:
            # Full traceback to the app's own output - the only place the
            # real cause has ever been recoverable from.
            traceback.print_exc()
            try:
                from starlette.responses import HTMLResponse

                # 200, deliberately: some browsers and proxies replace the
                # body of a 5xx with their own page, which is how this
                # became invisible in the first place.
                return HTMLResponse(_error_page(exc, base_url), status_code=200)
            except Exception:
                raise exc from None

    routes._auth_callback = _guarded_auth_callback  # type: ignore[attr-defined]
    setattr(routes, _WRAPPED_FLAG, True)
    return True
