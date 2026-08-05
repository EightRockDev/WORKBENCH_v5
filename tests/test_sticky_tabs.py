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


def test_defaults_to_input():
    # First-user feedback 2026-08: Input is the first/default section now — the
    # quick-start "first numbers" front door leads the property sub-tabs.
    at = _app().run()
    assert not at.exception, at.exception
    assert _active(at) == "input"


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


def test_goto_jumps_and_clears_itself():
    # First-user feedback 2026-08: a clickable KPI links to ?goto=<key>. The
    # helper must actually switch the keyed selector (not just the param) and
    # then clear goto so it doesn't re-fire on the next rerun.
    at = _app()
    at.query_params["goto"] = "underwriting"
    at = at.run()
    assert _active(at) == "underwriting"
    assert at.query_params.get("goto") in (None, "")


def test_goto_overrides_a_stored_selection():
    # The selector already holds a value from a prior interaction; ?goto must
    # still move it (a keyed widget ignores `default` once it has a value).
    at = _app().run()
    at.segmented_control[0].set_value("Diligence")
    at = at.run()
    assert _active(at) == "diligence"
    at.query_params["goto"] = "underwriting"
    at = at.run()
    assert _active(at) == "underwriting"


def test_every_key_is_reachable():
    at = _app().run()
    labels = {"Input": "input", "Subject": "subject",
              "Underwriting": "underwriting",
              "Returns": "returns", "Market": "market", "Summary": "summary",
              "Diligence": "diligence", "Investors": "investors"}
    for label, key in labels.items():
        at.segmented_control[0].set_value(label)
        at = at.run()
        assert _active(at) == key


# ---- cross-tab ghost kill (owner, escalated 2026-08-04) -----------------
# Switching to Underwriting still ghosted the Subject header's "Photo Upload"
# button, faded, until the new section finished rendering. The fix hides stale
# DOM in every section wrapper EXCEPT the active one, so the outgoing section's
# leftovers vanish while the active section keeps its normal in-place fade.

CSS_SCRIPT = textwrap.dedent("""
    import streamlit as st
    import app
    app._inject_ghost_kill_css("underwriting")
""")


def _css_app():
    return AppTest.from_string(CSS_SCRIPT, default_timeout=60)


def _emitted_css(at):
    return "\\n".join(str(b.value) for b in at.markdown)


def test_ghost_kill_targets_stale_dom():
    at = _css_app().run()
    assert not at.exception, at.exception
    css = _emitted_css(at)
    assert 'data-stale="true"' in css, "must target Streamlit's stale marker"


def test_ghost_kill_spares_the_active_section():
    """The active section must be EXCLUDED so a same-tab rerun (dragging an
    Underwriting slider) keeps its normal fade and never strobes."""
    at = _css_app().run()
    css = _emitted_css(at)
    assert ":not(.st-key-ptab_section_underwriting)" in css, (
        "the active section must be spared - without the :not() discriminator "
        "the active tab's own widgets get hidden mid-rerun and strobe")


def test_ghost_kill_hides_the_outgoing_section():
    """Sanity: the rule reaches OTHER section wrappers (the outgoing one that
    still holds the faded 'Photo Upload')."""
    at = _css_app().run()
    css = _emitted_css(at)
    assert 'display: none' in css or "display:none" in css
    assert '[class*="st-key-ptab_section_"]' in css
