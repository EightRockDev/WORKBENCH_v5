"""Tests for the owner-contact popover helper (first-user feedback 2026-08).

The rendering itself needs a running Streamlit app, but the data-loading
guard — `_load_resolved_pocs` — is what protects the DB-less desktop path from
crashing, so that's what we pin here. It must return None (→ Resolve-Contacts
fallback) rather than raise whenever the POC store isn't reachable.
"""

from __future__ import annotations

import streamlit as st

from ui.v2_theme_05292026 import _load_resolved_pocs


def test_returns_none_without_property_id():
    assert _load_resolved_pocs({}) is None


def test_returns_none_without_org_context():
    st.session_state.pop("org_id", None)
    assert _load_resolved_pocs({"property_id": "p1"}) is None


def test_returns_none_when_pg_unconfigured(monkeypatch):
    st.session_state["org_id"] = "org1"
    import data.pg as pg
    monkeypatch.setattr(pg, "is_configured", lambda: False)
    try:
        assert _load_resolved_pocs({"property_id": "p1"}) is None
    finally:
        st.session_state.pop("org_id", None)


def test_never_raises_when_store_errors(monkeypatch):
    st.session_state["org_id"] = "org1"
    import data.pg as pg
    monkeypatch.setattr(pg, "is_configured", lambda: True)
    from core.skiptrace import pipeline
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(pipeline, "load_pocs", _boom)
    try:
        assert _load_resolved_pocs({"property_id": "p1"}) is None
    finally:
        st.session_state.pop("org_id", None)
