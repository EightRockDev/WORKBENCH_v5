"""Property Screener module — filter both property pools, click into a deal.

Owner spec (2026-08-25): a nav button between Loans and Help; filters on
top (name / city / state / units / class / year built / last sale price /
management company / owner, plus zip, market, occupancy and sale-date
ranges); a Submit button; free text matches CONTAINS, case-insensitive;
clicking a result opens it in Deal Analysis.

The query logic lives in core/screener.py (tested there, no Streamlit).
This file is only widgets: read the filter widgets, hand the dict to
``run_screener``, paint the table.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import streamlit as st

import config
from core.screener import SOURCE_CURATED, run_screener
from data.db import DB_PATH
from ui.components import section_card, v2_strip_icon

# Every filter widget key, for Reset. Prefix scr_ per house convention
# (inventory uses inv_browse_*).
_FILTER_KEYS = (
    "scr_name", "scr_city", "scr_state", "scr_zip", "scr_owner",
    "scr_mgmt", "scr_market", "scr_class", "scr_units", "scr_year",
    "scr_price_min", "scr_price_max", "scr_date_from", "scr_date_to",
    "scr_occ",
)

_THIS_YEAR = _dt.date.today().year


def _iso_or_blank(d) -> str:
    return d.isoformat() if isinstance(d, _dt.date) else ""


def _filters_from_state() -> dict:
    units = st.session_state.get("scr_units", (0, 1000))
    year = st.session_state.get("scr_year", (1850, _THIS_YEAR))
    occ = st.session_state.get("scr_occ", (0, 100))
    return {
        "name": st.session_state.get("scr_name", ""),
        "city": st.session_state.get("scr_city", ""),
        "state": st.session_state.get("scr_state", ""),
        "zip": st.session_state.get("scr_zip", ""),
        "owner": st.session_state.get("scr_owner", ""),
        "management_company": st.session_state.get("scr_mgmt", ""),
        "market": st.session_state.get("scr_market", ""),
        "asset_class": st.session_state.get("scr_class", []),
        # Sliders parked at their ends mean "no filter" — sending the
        # bound anyway would silently drop rows with the value missing.
        "units_min": units[0] if units[0] > 0 else None,
        "units_max": units[1] if units[1] < 1000 else None,
        "year_min": year[0] if year[0] > 1850 else None,
        "year_max": year[1] if year[1] < _THIS_YEAR else None,
        "price_min": st.session_state.get("scr_price_min") or None,
        "price_max": st.session_state.get("scr_price_max") or None,
        # date_input hands back a datetime.date or None — never junk
        # text (run_screener normalizes again anyway, belt and braces).
        "date_from": _iso_or_blank(st.session_state.get("scr_date_from")),
        "date_to": _iso_or_blank(st.session_state.get("scr_date_to")),
        "occ_min": occ[0] if occ[0] > 0 else None,
        "occ_max": occ[1] if occ[1] < 100 else None,
    }


def _render_filters(c: dict) -> bool:
    """The filter grid. Returns True when Submit was clicked."""
    with st.expander(v2_strip_icon("⚙️ Filters"), expanded=True):
        st.markdown(
            f'<div style="color:{c["tx2"]};font-size:11px;font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.5px">Text — matches '
            f'anywhere in the field (e.g. "Dolly" finds "Dolly Parton")'
            f'</div>', unsafe_allow_html=True)
        t1, t2, t3 = st.columns(3)
        with t1:
            st.text_input("Property name", key="scr_name")
            st.text_input("Owner", key="scr_owner")
        with t2:
            st.text_input("City", key="scr_city")
            st.text_input("Management company", key="scr_mgmt")
        with t3:
            st.text_input("State", key="scr_state")
            st.text_input("Zip", key="scr_zip")
        m1, m2, _m3 = st.columns(3)
        with m1:
            st.text_input("Market / submarket", key="scr_market")
        with m2:
            st.multiselect("Class", ["A", "B", "C", "D"], key="scr_class")

        st.markdown(
            f'<div style="color:{c["tx2"]};font-size:11px;font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.5px;margin-top:6px">'
            f'Ranges</div>', unsafe_allow_html=True)
        r1, r2, r3 = st.columns(3)
        with r1:
            st.slider("Unit count", 0, 1000, (0, 1000), key="scr_units")
            st.number_input("Last sale price — min ($)", min_value=0,
                            value=0, step=100_000, key="scr_price_min")
        with r2:
            st.slider("Year built", 1850, _THIS_YEAR, (1850, _THIS_YEAR),
                      key="scr_year")
            st.number_input("Last sale price — max ($)", min_value=0,
                            value=0, step=100_000, key="scr_price_max",
                            help="0 = no maximum")
        with r3:
            st.slider("Occupancy %", 0, 100, (0, 100), key="scr_occ")
            d1, d2 = st.columns(2)
            with d1:
                st.date_input("Sold after", value=None, key="scr_date_from",
                              min_value=_dt.date(1900, 1, 1))
            with d2:
                st.date_input("Sold before", value=None, key="scr_date_to",
                              min_value=_dt.date(1900, 1, 1))

        st.caption("County records carry no property name, class, "
                   "management company or occupancy — using those filters "
                   "narrows results to your own records.")

        b1, b2, _sp = st.columns([1, 1, 4])
        submitted = b1.button("Submit", type="primary",
                              use_container_width=True, key="scr_submit")
        if b2.button("Reset", use_container_width=True, key="scr_reset"):
            for k in _FILTER_KEYS:
                st.session_state.pop(k, None)
            st.session_state.pop("scr_results", None)
            st.rerun()
    return submitted


def _fmt_money(v) -> str:
    return f"${v:,.0f}" if v else "—"


def _render_results(c: dict, rows: list[dict]) -> None:
    if not rows:
        st.info("No properties match those filters. Loosen one and "
                "press Submit again.")
        return

    curated_n = sum(1 for r in rows if r["source"] == SOURCE_CURATED)
    st.markdown(
        f'<div style="color:{c["tx2"]};font-size:13px;margin:4px 0 8px">'
        f'<b>{len(rows):,}</b> matches — {curated_n:,} from my records, '
        f'{len(rows) - curated_n:,} from county records. '
        f'<span style="color:{c["tx3"]}">Select a row to open it in '
        f'Deal Analysis.</span></div>',
        unsafe_allow_html=True)

    display = pd.DataFrame([{
        "Source": r["source"],
        "Name": r["name"] or "—",
        "City": r["city"] or "—",
        "State": r["state"] or "—",
        "Zip": r["zip"] or "—",
        "Units": r["units"],
        "Class": r["asset_class"] or "—",
        "Year Built": r["year_built"],
        "Last Sale": _fmt_money(r["sale_price"]),
        "Sold": r["sale_when"] or "—",
        "Owner": r["owner"] or "—",
        "Mgmt Co": r["management_company"] or "—",
    } for r in rows])

    event = st.dataframe(
        display, use_container_width=True, hide_index=True, height=500,
        on_select="rerun", selection_mode="single-row", key="scr_table")

    picked = (event.selection.rows or [None])[0] \
        if event and event.selection else None
    # A selection made against an OLDER result set can outlive Submit
    # (the widget's identity ignores its data), so a stale index may be
    # out of range — or worse, point at a different property. Submit
    # clears it below; this guard is the backstop.
    if picked is None or picked >= len(rows):
        return
    row = rows[picked]
    # Curated rows always have a Deal Analysis page. Backbone rows only
    # resolve through get_property() after the 8r cutover flips the read
    # seam (data/db.py) — before that, be honest instead of a dead click.
    if row["source"] == SOURCE_CURATED or config.SPINE_READ_SOURCE == "8r":
        st.session_state["selected_property_id"] = row["property_id"]
        st.session_state["active_module"] = "deal_analysis"
        # Drop the table's selection state, or coming back to the
        # screener re-fires this navigation from the stale selection.
        st.session_state.pop("scr_table", None)
        st.rerun()
    else:
        st.info(f"**{row['name']}** is a county record — it gets a full "
                f"property page when the county backbone goes live. Its "
                f"facts are all in the row above.")


def render_property_screener() -> None:
    c = config.COLORS
    st.markdown(v2_strip_icon("## 🔎 Property Screener"))
    st.caption("Search every property the workbench knows — your own "
               "records and the county backbone — in one screen.")

    with section_card("Property Screener", icon="🔎"):
        submitted = _render_filters(c)
        if submitted:
            # New results, new table: drop any selection made against the
            # old rows BEFORE the dataframe instantiates, or the stale row
            # index replays against the new list (wrong property, or an
            # IndexError past the end).
            try:
                st.session_state["scr_table"] = {
                    "selection": {"rows": [], "columns": [], "cells": []}}
            except Exception:
                st.session_state.pop("scr_table", None)
            st.session_state["scr_results"] = run_screener(
                _filters_from_state(), db_path=DB_PATH)

        if "scr_results" in st.session_state:
            _render_results(c, st.session_state["scr_results"])
        else:
            st.markdown(
                f'<div style="color:{c["tx3"]};font-size:13px;'
                f'padding:12px 0">Set any filters above and press '
                f'<b>Submit</b>. Submit with no filters to browse '
                f'everything.</div>', unsafe_allow_html=True)
