"""ER_APP_PASSCODE gate - protects LAN exposure before real sign-in exists."""

from __future__ import annotations

import textwrap

import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = textwrap.dedent("""
    import streamlit as st
    from core import session
    session.require_passcode(st)
    st.markdown("APP CONTENT")
""")


def _app():
    return AppTest.from_string(SCRIPT, default_timeout=30)


def _body(at):
    return "\n".join(str(b.value) for b in at.markdown)


def test_no_passcode_configured_means_no_gate(monkeypatch):
    monkeypatch.delenv("ER_APP_PASSCODE", raising=False)
    at = _app().run()
    assert not at.exception
    assert "APP CONTENT" in _body(at)


def test_gate_blocks_until_entry(monkeypatch):
    monkeypatch.setenv("ER_APP_PASSCODE", "granite2026")
    at = _app().run()
    assert not at.exception
    assert "APP CONTENT" not in _body(at)
    assert at.text_input, "passcode field missing"


def test_wrong_passcode_stays_blocked(monkeypatch):
    monkeypatch.setenv("ER_APP_PASSCODE", "granite2026")
    at = _app().run()
    at.text_input[0].set_value("letmein")
    at = at.run()
    # submit the form
    at.button[0].set_value(True)
    at = at.run()
    assert "APP CONTENT" not in _body(at)
    assert any("Wrong passcode" in e.value for e in at.error)


def test_right_passcode_unlocks_and_persists(monkeypatch):
    monkeypatch.setenv("ER_APP_PASSCODE", "granite2026")
    at = _app().run()
    at.text_input[0].set_value("granite2026")
    at = at.run()
    at.button[0].set_value(True)
    at = at.run()
    assert "APP CONTENT" in _body(at), _body(at)
    # and a rerun (next page interaction) stays unlocked
    at = at.run()
    assert "APP CONTENT" in _body(at)


def test_surrounding_whitespace_is_forgiven(monkeypatch):
    """A phone keyboard often appends a space - that must not lock people out."""
    monkeypatch.setenv("ER_APP_PASSCODE", "granite2026")
    at = _app().run()
    at.text_input[0].set_value("  granite2026 ")
    at = at.run()
    at.button[0].set_value(True)
    at = at.run()
    assert "APP CONTENT" in _body(at)


def test_resolve_user_enforces_the_gate_first(monkeypatch):
    """The gate must run before any auth-mode branching in resolve_user."""
    monkeypatch.setenv("ER_APP_PASSCODE", "granite2026")
    script = textwrap.dedent("""
        import streamlit as st
        from core import session
        user = session.resolve_user(st)
        st.markdown("PAST THE GATE")
    """)
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception
    assert "PAST THE GATE" not in "\n".join(str(b.value) for b in at.markdown)
