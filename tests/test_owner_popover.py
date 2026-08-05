"""Tests for the People-block contact rendering (owner report 2026-08-05).

All POC / contact info now renders INLINE inside the People block — no separate
popover. Two things are pinned: (1) the DB-load guard `_load_resolved_pocs` must
degrade to None rather than raise when the store is unreachable; (2) the inline
HTML builders must reflect what actually resolved — "no phone resolved" when the
waterfall came back empty (the real Brg Aura case in the screenshot), the real
number when it didn't, and everything HTML-escaped.
"""

from __future__ import annotations

import streamlit as st

from ui.v2_theme_05292026 import (
    _OWNER_ROLES,
    _load_resolved_pocs,
    _people_contact_html,
    _poc_contact_rows_html,
)


# ---- DB-load guard degrades, never raises -------------------------------

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


# ---- inline contact rows honestly reflect what resolved -----------------

_BRG_POC = {   # the screenshot case: entity owner, no phone/email, mailing only
    "role": "owner",
    "person": {"full_name": "Brg Aura At East Beach, Llc"},
    "phones": [], "emails": [],
    "addresses": [{"formatted": "209 Madison St #400, Alexandria, VA 22314",
                   "kind": "mailing"}],
    "entity_chain": [],
}


def test_empty_waterfall_says_no_phone_no_email_not_silence():
    html = _poc_contact_rows_html(_BRG_POC)
    assert "no phone resolved" in html
    assert "no email resolved" in html
    # the mailing address that DID resolve is shown
    assert "209 Madison St #400, Alexandria, VA 22314" in html


def test_resolved_phone_shows_the_number_and_grade():
    poc = {"role": "principal", "person": {"full_name": "Jane Doe"},
           "phones": [{"number": "757-555-0100", "grade": "A"}],
           "emails": [{"email": "jane@example.com", "grade": "B"}]}
    html = _poc_contact_rows_html(poc)
    assert "757-555-0100" in html
    assert "grade A" in html
    assert "jane@example.com" in html
    assert "no phone resolved" not in html


def test_values_are_html_escaped():
    poc = {"role": "owner", "person": {"full_name": "A & B <Llc>"},
           "phones": [], "emails": [{"email": "x<y>@z"}]}
    html = _poc_contact_rows_html(poc)
    assert "x&lt;y&gt;@z" in html


def test_unpierced_note_is_carried_inline():
    poc = {"role": "entity_unpierced",
           "person": {"full_name": "Some Holdings LLC",
                      "unpierced_note": "reach this owner through the manager"},
           "phones": [], "emails": []}
    html = _poc_contact_rows_html(poc)
    assert "reach this owner through the manager" in html


# ---- role filtering + empty-pocs behavior -------------------------------

def test_people_contact_html_empty_when_no_pocs():
    assert _people_contact_html({"owner": "X"}, _OWNER_ROLES, None) == ""
    assert _people_contact_html({"owner": "X"}, _OWNER_ROLES, []) == ""


def test_people_contact_html_filters_by_role():
    prop = {"owner": "Brg Aura At East Beach, Llc"}
    pocs = [_BRG_POC,
            {"role": "property_manager", "person": {"full_name": "Vest Residential"},
             "phones": [], "emails": []}]
    owner_html = _people_contact_html(prop, _OWNER_ROLES, pocs)
    # owner-side render includes the mailing address; not the manager row
    assert "209 Madison St #400" in owner_html
    assert "Vest Residential" not in owner_html
