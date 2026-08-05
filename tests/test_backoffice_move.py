"""Placement of the back-office property tools.

History (owner asks 2026-08-04):
  1. Data Sources + Owner Intelligence were pulled off the deal tabs into the
     Admin back-office.
  2. Then moved again onto the MARKET tab, which now reads
     Comparables -> Owner Intelligence (+ Outreach) -> Data Sources.

Source-level assertions — the render functions need a live Streamlit runtime,
so we pin WHERE each panel is wired rather than rendering it.
"""

from __future__ import annotations

import inspect

import app
import ui.comps as comps


def test_market_tab_hosts_comps_owner_intel_and_data_sources():
    src = inspect.getsource(app._render_active_section)
    assert "render_comps" in src                      # comparables
    assert "render_owner_intel" in src                # owner intelligence
    assert "render_outreach" in src                   # outreach
    assert "render_listing_urls_panel" in src         # data sources


def test_market_tab_orders_owner_then_comps_then_data_sources():
    src = inspect.getsource(app._render_active_section)
    i_owner = src.index("render_owner_intel")
    i_comps = src.index("render_comps")
    i_data = src.index("render_listing_urls_panel")
    assert i_owner < i_comps < i_data, (
        "Market tab must read Owner Intelligence -> Comparables -> Data Sources")


def test_backoffice_no_longer_hosts_the_property_panels():
    src = inspect.getsource(app._render_backoffice)
    assert "render_owner_intel" not in src
    assert "render_listing_urls_panel" not in src
    # what remains is org administration for a real admin
    assert "render_admin_page" in src


def test_rent_listing_urls_not_baked_into_the_comps_module():
    src = inspect.getsource(comps)
    assert "render_listing_urls_panel" not in src


def test_backoffice_reachable_by_the_single_tenant_operator():
    src = inspect.getsource(app)
    assert "(user is None) or user.is_admin" in src


def test_backoffice_admin_page_still_requires_a_real_admin():
    src = inspect.getsource(app._render_backoffice)
    assert "user is not None and user.is_admin" in src


def test_diligence_keeps_the_dd_checklist():
    src = inspect.getsource(app._render_active_section)
    assert "render_due_diligence" in src
    assert "render_acquisition_checklist" in src
