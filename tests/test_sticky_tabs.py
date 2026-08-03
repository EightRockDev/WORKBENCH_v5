"""Property sub-tabs survive an in-section rerun (owner report 2026-08-03).

st.tabs snaps back to the first tab whenever a widget inside a tab triggers a
rerun - so clicking "Resolve" on Diligence bounced the user to Subject. The
selector is now a keyed segmented_control, whose value lives in session_state
and survives reruns. These tests drive the REAL helper from app.py.
"""

from __future__ import annotations

import textwrap

from streamlit.testing.v1 import AppTest

# Exercise the real _sticky_property_tab from app.py in isolation: a keyed
# selector plus a button that forces a rerun, mimicking Resolve on Diligence.
SCRIPT = textwrap.dedent("""
    import streamlit as st
    import app
    active = app._sticky_property_tab(is_v2=True)
    st.markdown(f"ACTIVE={active}")
    if st.button("force-rerun"):
        st.session_state["_clicked"] = True
    st.markdown(f"CLICKED={st.session_state.get('_clicked', False)}")
""")


def _app():
    return AppTest.from_string(SCRIPT, default_timeout=60)


def _active(at):
    for b in at.markdown:
        if str(b.value).startswith("ACTIVE="):
            return str(b.value).split("=", 1)[1]
    return None


def test_defaults_to_subject():
    at = _app().run()
    assert not at.exception, at.exception
    assert _active(at) == "subject"


def test_selecting_a_section_sticks_across_a_rerun():
    at = _app().run()
    # move to Diligence
    sc = at.segmented_control[0]
    sc.set_value("Diligence")
    at = at.run()
    assert _active(at) == "diligence"
    # now a button click forces a rerun (this is what Resolve does) -
    # the selector must NOT snap back to Subject
    at.button[0].click()
    at = at.run()
    assert _active(at) == "diligence", (
        "in-section rerun reset the tab - the st.tabs bounce is back")
    assert _active(at) != "subject"


def test_query_param_seeds_the_opening_section():
    at = _app()
    at.query_params["ptab"] = "underwriting"
    at = at.run()
    assert _active(at) == "underwriting"


def test_every_key_is_reachable():
    at = _app().run()
    labels = {"Subject": "subject", "Underwriting": "underwriting",
              "Returns": "returns", "Market": "market", "Summary": "summary",
              "Diligence": "diligence", "Investors": "investors"}
    for label, key in labels.items():
        at.segmented_control[0].set_value(label)
        at = at.run()
        assert _active(at) == key
