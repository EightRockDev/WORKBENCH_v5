"""The workspace modules are reachable from V2 chrome (owner report 2026-08-18).

V2 hides V1's left sidebar with `section[data-testid="stSidebar"]
{ display:none }`, and that sidebar held the ONLY module switcher — so CRM,
Portfolio, GRANITE Loans and Help had no entrance at all in the UI the owner
actually runs. The comment above the hide rule said the modules were "still
reachable via Switch-to-V1", but that pill was removed on 2026-08-04 and
replaced with the who's-online count.

Two seams, two tests. Neither is a unit test of a single function, because
neither failure lived inside one: the nav strip has to RENDER a link per
module, and the link's `?module=` handle has to survive the trip through
app.main() into the dispatch that picks a module. A test that stubs either
half would have passed against the broken build.
"""

from __future__ import annotations

import os
import re
import textwrap

import pytest
from streamlit.testing.v1 import AppTest

# Imported lazily inside each test ON PURPOSE. Against the pre-fix build these
# names don't exist, and a module-level import would turn every test below
# into one collection error reading "cannot import MODULE_NAV" — which proves
# a symbol is missing, not that the owner had no way into CRM. Each test
# should fail saying what the user cannot do.
EXPECTED_MODULES = ("deal_analysis", "crm", "portfolio", "granite_loans", "help")


@pytest.fixture(autouse=True)
def _v2_mode(monkeypatch):
    monkeypatch.setenv("ER_THEME", "v2")


# --------------------------------------------------------------------------
# 1. The TOP BAR — the thing on screen — carries a link per module
# --------------------------------------------------------------------------

# Renders the real V2 top bar, exactly as app.py does on every surface. This
# is the assertion that could not have passed while the app was broken: the
# owner's screenshot shows this bar, and nothing on it led to CRM.
TOPBAR_SCRIPT = textwrap.dedent("""
    import streamlit as st
    from ui import v2_theme_05292026 as v2
    v2.render_v2_topbar(None)
""")

NAV_SCRIPT = textwrap.dedent("""
    import streamlit as st
    from ui import v2_theme_05292026 as v2
    v2.render_v2_module_nav()
""")


def _html(script: str, **state) -> str:
    at = AppTest.from_string(script, default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    at = at.run()
    assert not at.exception, at.exception
    return "".join(str(b.value) for b in at.markdown)


def test_topbar_carries_an_entrance_to_every_module():
    html = _html(TOPBAR_SCRIPT)
    missing = [s for s in EXPECTED_MODULES if f"?module={s}" not in html]
    assert not missing, (
        f"no way into {missing} from the V2 top bar — the sidebar that used "
        f"to hold the switcher is hidden by CSS in this theme")


def test_every_module_link_is_labelled():
    html = _html(NAV_SCRIPT)
    for label in ("Deal Analysis", "CRM & Sourcing", "Portfolio",
                  "GRANITE Loans", "Help"):
        assert label in html, f"{label} link is unlabelled"


def test_nav_marks_the_active_module():
    html = _html(NAV_SCRIPT, active_module="portfolio")
    # The active pill carries `class="... on"`; find which href owns it.
    on = re.findall(r'class="v2-modnav-item on" href="\?module=([a-z_]+)"', html)
    assert on == ["portfolio"], f"expected portfolio highlighted, got {on}"


def test_nav_is_v2_only():
    """V1 still has the sidebar switcher — a second nav there would be two
    controls for one piece of state."""
    os.environ["ER_THEME"] = "v1"
    try:
        assert _html(NAV_SCRIPT).strip() == ""
    finally:
        os.environ["ER_THEME"] = "v2"


# --------------------------------------------------------------------------
# 2. The handle works — ?module= reaches the dispatch in app.main()
# --------------------------------------------------------------------------

QP_SCRIPT = textwrap.dedent("""
    import streamlit as st
    import app
    app._apply_module_qp()
    st.markdown("MODULE=" + str(st.session_state.get("active_module")))
    st.markdown("PARAM=" + str(st.query_params.get("module")))
""")


def _run_qp(module: str | None):
    at = AppTest.from_string(QP_SCRIPT, default_timeout=60)
    if module is not None:
        at.query_params["module"] = module
    at = at.run()
    assert not at.exception, at.exception
    vals = {}
    for b in at.markdown:
        s = str(b.value)
        if "=" in s:
            k, _, v = s.partition("=")
            vals[k] = v
    return vals


@pytest.mark.parametrize("slug", EXPECTED_MODULES)
def test_module_param_selects_that_module(slug):
    assert _run_qp(slug)["MODULE"] == slug, (
        f"?module={slug} did not reach the workspace dispatch")


def test_module_param_clears_itself():
    """Consume-once, like ?goto=. A sticky ?module= in the URL would drag the
    user back to that module on the next rerun, whatever they clicked."""
    assert _run_qp("crm")["PARAM"] == "None"


def test_unknown_module_is_ignored():
    """A hand-typed or stale slug must not blank the workspace."""
    assert _run_qp("../../etc/passwd")["MODULE"] == "None"
    assert _run_qp("granite")["MODULE"] == "None"


# --------------------------------------------------------------------------
# 3. The two chromes stay in step
# --------------------------------------------------------------------------

def test_v2_nav_and_sidebar_switcher_offer_the_same_modules():
    """One product, two chromes. A module added to the sidebar switcher and
    forgotten here is invisible to every V2 user — which is exactly the bug
    this file exists for."""
    import inspect
    from ui import sidebar
    from ui.v2_theme_05292026 import MODULE_SLUGS

    src = inspect.getsource(sidebar._render_module_switcher)
    sidebar_slugs = set(re.findall(r'\("([a-z_]+)",\s+"[^"]*(?:🏢|🎯|📊|🏦|❓)', src))
    assert sidebar_slugs, "could not read the sidebar's module list"
    assert sidebar_slugs == set(MODULE_SLUGS) == set(EXPECTED_MODULES), (
        f"sidebar has {sidebar_slugs}, V2 nav has {set(MODULE_SLUGS)}")
