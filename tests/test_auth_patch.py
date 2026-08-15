"""A failed sign-in must say what happened, not "Internal Server Error".

Streamlit 1.57's OAuth callback route ends with an unguarded call:

    client, _ = _create_oauth_client(provider)
    token = await client.authorize_access_token(request)

Everything that call can raise - a spent one-time code, a rejected
credential, an unreachable provider - escapes the handler and Starlette
returns its default 500 body. That is the page the owner was staring at on
2026-08-15, with nothing in it to act on and nothing written anywhere he
would look.

core.auth_patch wraps that one function. These tests pin the three
properties that make the wrap worth having: it catches, it explains, and it
cannot itself break the app.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from core import auth_patch


class _FakeRoutes(types.ModuleType):
    """Stand-in for streamlit's starlette_auth_routes module."""

    def __init__(self, exc: BaseException | None):
        super().__init__("fake_auth_routes")
        self._exc = exc
        self.calls = 0

        async def _auth_callback(request, base_url):
            self.calls += 1
            if self._exc is not None:
                raise self._exc
            return "the real redirect"

        self._auth_callback = _auth_callback


def _install_against(monkeypatch, module):
    """Point auth_patch.install() at a fake module and run it."""
    import sys

    pkg = "streamlit.web.server.starlette"
    monkeypatch.setitem(sys.modules, f"{pkg}.starlette_auth_routes", module)
    real_import = __import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == pkg and fromlist and "starlette_auth_routes" in fromlist:
            holder = types.ModuleType(pkg)
            holder.starlette_auth_routes = module
            return holder
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    return auth_patch.install()


def test_a_successful_callback_is_passed_straight_through(monkeypatch):
    mod = _FakeRoutes(None)
    assert _install_against(monkeypatch, mod) is True
    result = asyncio.run(mod._auth_callback(object(), "/"))
    assert result == "the real redirect"
    assert mod.calls == 1


def test_a_failed_token_exchange_renders_a_page_instead_of_a_500(monkeypatch):
    mod = _FakeRoutes(RuntimeError("invalid_grant: code already used"))
    assert _install_against(monkeypatch, mod) is True
    response = asyncio.run(mod._auth_callback(object(), "/"))

    # 200 deliberately: browsers and proxies replace 5xx bodies with their
    # own page, which is how the real cause stayed invisible.
    assert getattr(response, "status_code", None) == 200
    body = bytes(response.body).decode("utf-8")
    assert "already been used" in body
    assert "Start sign-in again" in body


def test_installing_twice_does_not_double_wrap(monkeypatch):
    mod = _FakeRoutes(None)
    assert _install_against(monkeypatch, mod) is True
    assert _install_against(monkeypatch, mod) is False


def test_install_never_raises_when_streamlit_moves(monkeypatch):
    """A diagnostic aid must not be able to take down the app it diagnoses."""
    import builtins

    real_import = builtins.__import__

    def _explode(name, *a, **kw):
        if "starlette_auth_routes" in (kw.get("fromlist") or (a[2] if len(a) > 2 else ()) or ()):
            raise ImportError("moved in a later Streamlit")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _explode)
    assert auth_patch.install() is False


@pytest.mark.parametrize("message,expected", [
    ("invalid_grant: authorization code already used", "already been used"),
    ("invalid_client: client authentication failed", "rejected the workbench"),
    ("redirect_uri mismatch", "did not recognise the address"),
    ("ConnectTimeout: provider unreachable", "could not reach the sign-in"),
    ("something nobody has seen before", "did not finish"),
])
def test_each_failure_gets_plain_language(message, expected):
    """The owner is not technical. 'invalid_grant' tells him nothing; 'that
    link was already used, start again' tells him exactly what to do."""
    headline, advice = auth_patch._plain_reason(RuntimeError(message))
    assert expected in (headline + " " + advice).lower() or expected in headline
