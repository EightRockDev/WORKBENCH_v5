"""Data Sources + Leads moved off the deal tabs into the Admin panel
(owner ask 2026-08-04).

Source-level assertions - the render functions need a live Streamlit runtime,
so we pin WHERE each panel is wired rather than rendering it. The point is
that deal analysis (Market/Diligence) no longer carries the back-office
config, and the admin back-office does.
"""

from __future__ import annotations

import inspect

import app
import ui.comps as comps


def test_backoffice_hosts_data_sources_and_leads():
    src = inspect.getsource(app._render_backoffice)
    assert "render_listing_urls_panel" in src        # data sources
    assert "render_owner_intel" in src               # leads
    assert "render_outreach" in src                  # outreach on leads


def test_diligence_tab_no_longer_renders_owner_intelligence():
    src = inspect.getsource(app._render_active_section)
    assert "render_owner_intel" not in src, (
        "Owner Intelligence must live in the Admin panel, not Diligence")
    # the DD checklist stays on Diligence
    assert "render_due_diligence" in src


def test_market_tab_no_longer_renders_rent_listing_urls():
    src = inspect.getsource(comps)
    assert "render_listing_urls_panel" not in src, (
        "Rent Listing URLs must live in the Admin panel, not the comps view")


def test_backoffice_is_reachable_by_the_single_tenant_operator():
    """The admin panel is gated, but the ungated/passcode owner (user is
    None) must still reach the back-office - otherwise the move hides the
    tools the owner just asked to relocate."""
    src = inspect.getsource(app)
    assert "(user is None) or user.is_admin" in src


def test_backoffice_admin_tabs_still_require_a_real_admin():
    src = inspect.getsource(app._render_backoffice)
    assert "user is not None and user.is_admin" in src
    assert "render_admin_page" in src
