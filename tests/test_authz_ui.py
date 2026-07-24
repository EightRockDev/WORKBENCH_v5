"""Unit tests for the UI authorization helpers (spec §10.4 enforcement layer).

DB-free: builds Permissions objects directly and drives ui.authz through
Streamlit's bare mode (st.session_state works without a running app).
"""

from __future__ import annotations

import streamlit as st

from core.permissions import MASK_PLACEHOLDER, Permissions
from ui import authz

MAINTENANCE = Permissions(
    org_id="org", role_preset="maintenance",
    modules=frozenset({"ops"}),
    masks=frozenset({"purchase_price", "returns_irr", "waterfall_promote", "lp_pii", "debt_terms"}),
    actions=frozenset(), scope="deal",
)
PRINCIPAL = Permissions(
    org_id="org", role_preset="principal",
    modules=frozenset({"underwriting", "waterfall", "lp_portal", "comps", "documents", "outreach"}),
    masks=frozenset(), actions=frozenset({"commit_go_nogo"}), scope="org_all",
)


def _set(perms):
    st.session_state["perms"] = perms


def test_ungated_mode_everything_passes():
    st.session_state.pop("perms", None)
    assert authz.guard_module("underwriting", "Underwriting") is True
    assert authz.mask("purchase_price", 123) == 123


def test_maintenance_is_locked_out_of_financial_modules():
    _set(MAINTENANCE)
    assert authz.guard_module("underwriting", "Underwriting") is False
    assert authz.guard_module("waterfall", "Returns") is False
    assert authz.guard_module("lp_portal", "Investors") is False
    assert authz.guard_module("ops", "Operations") is True  # their own module


def test_principal_opens_everything_granted():
    _set(PRINCIPAL)
    for m in ("underwriting", "waterfall", "lp_portal", "comps", "documents"):
        assert authz.guard_module(m, m) is True


def test_field_mask_applies_per_role():
    _set(MAINTENANCE)
    assert authz.mask("purchase_price", 3_500_000) == MASK_PLACEHOLDER
    scrubbed = authz.scrub({"purchase_price": 1, "units": 48})
    assert scrubbed["purchase_price"] == MASK_PLACEHOLDER and scrubbed["units"] == 48
    _set(PRINCIPAL)
    assert authz.mask("purchase_price", 3_500_000) == 3_500_000


def test_preview_roundtrip_returns_real_perms_when_off():
    st.session_state.pop("_preview_role", None)
    assert authz.apply_preview("org", PRINCIPAL) is PRINCIPAL
    st.session_state["_preview_role"] = "(my real role)"
    assert authz.apply_preview("org", PRINCIPAL) is PRINCIPAL
    st.session_state.pop("_preview_role", None)
    st.session_state.pop("perms", None)
