"""Tests for core.auth — user model + local-dev fallback + role gating.

MSAL-flow tests would require a live Entra tenant; covered by the deploy
runbook smoke test (step 8). These tests verify the in-process logic that
runs without a network round-trip.
"""

from __future__ import annotations

import pytest

from core import auth
from core.auth import ANONYMOUS, LOCAL_DEV_USER, User, current_user, is_auth_enabled


# ---------------------------------------------------------------------------
# User dataclass
# ---------------------------------------------------------------------------

class TestUserModel:
    def test_has_role(self):
        u = User(oid="x", email="a@b.com", name="A", roles=("internal", "admin"))
        assert u.has_role("internal")
        assert not u.has_role("lp-investor")

    def test_has_any_role(self):
        u = User(oid="x", email="", name="", roles=("internal",))
        assert u.has_any_role("internal", "lp-investor")
        assert not u.has_any_role("admin", "lp-investor")

    def test_is_internal_shortcut(self):
        u = User(oid="x", email="", name="", roles=("eight-rock-internal",))
        assert u.is_internal
        assert not u.is_lp

    def test_is_lp_shortcut(self):
        u = User(oid="x", email="", name="", roles=("lp-investor",), lp_id="lp-001")
        assert u.is_lp
        assert not u.is_internal
        assert u.lp_id == "lp-001"

    def test_display_name_fallback(self):
        u = User(oid="x", email="brian@eightrockcp.com", name="")
        assert u.display_name == "brian@eightrockcp.com"
        u2 = User(oid="x", email="", name="")
        assert u2.display_name == "User"


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

class TestBackendSelection:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.delenv("ER_AUTH_BACKEND", raising=False)
        assert not is_auth_enabled()

    def test_msal_enabled(self, monkeypatch):
        monkeypatch.setenv("ER_AUTH_BACKEND", "msal")
        assert is_auth_enabled()

    def test_disabled_returns_local_dev_user(self, monkeypatch):
        monkeypatch.delenv("ER_AUTH_BACKEND", raising=False)
        u = current_user()
        assert u is LOCAL_DEV_USER
        assert u.is_internal
        assert not u.is_anonymous


# ---------------------------------------------------------------------------
# Token claim parsing
# ---------------------------------------------------------------------------

class TestUserFromToken:
    def test_basic_claims(self, monkeypatch):
        monkeypatch.delenv("ER_AUTH_ROLE_CLAIM", raising=False)
        monkeypatch.delenv("ER_AUTH_LP_ID_CLAIM", raising=False)
        token = {
            "id_token_claims": {
                "oid": "obj-id-123",
                "email": "peter@eightrockcp.com",
                "name": "Peter",
                "roles": ["eight-rock-internal"],
            },
            "expires_in": 3600,
        }
        u = auth.user_from_token(token)
        assert u.oid == "obj-id-123"
        assert u.email == "peter@eightrockcp.com"
        assert u.is_internal
        assert u.token_expires_at is not None

    def test_lp_id_claim(self, monkeypatch):
        monkeypatch.setenv("ER_AUTH_LP_ID_CLAIM", "lp_id")
        token = {
            "id_token_claims": {
                "oid": "obj-id-lp1",
                "email": "lp1@example.com",
                "name": "LP One",
                "roles": ["lp-investor"],
                "lp_id": "LP-001",
            },
        }
        u = auth.user_from_token(token)
        assert u.is_lp
        assert u.lp_id == "LP-001"

    def test_no_roles_claim_is_safe(self):
        token = {
            "id_token_claims": {
                "oid": "obj-id-x",
                "email": "x@y.com",
                "name": "X",
                # no roles
            },
        }
        u = auth.user_from_token(token)
        assert u.roles == ()
        assert not u.is_internal
        assert not u.is_lp

    def test_string_role_normalized_to_tuple(self):
        """Some token providers send role as string instead of array."""
        token = {
            "id_token_claims": {
                "oid": "x", "email": "", "name": "",
                "roles": "eight-rock-internal",
            },
        }
        u = auth.user_from_token(token)
        assert u.roles == ("eight-rock-internal",)


# ---------------------------------------------------------------------------
# lp_scope_filter — convenience for query gating
# ---------------------------------------------------------------------------

class TestLpScopeFilter:
    def test_internal_user_returns_none(self, monkeypatch):
        monkeypatch.delenv("ER_AUTH_BACKEND", raising=False)
        # LOCAL_DEV_USER is internal; lp_scope_filter() should return None.
        assert auth.lp_scope_filter() is None
