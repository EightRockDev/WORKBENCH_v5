""""+ Add property" and the verified badge (spec §16).

A property the platform has not uncovered yet goes in here in under a minute
(AC-16.1) and is immediately usable by the submitting org — wearing a grey
Unverified badge until the nightly validation confirms its core elements
against the municipal roll, at which point it earns the blue check. The
badge decision's evidence renders on demand (AC-16.3).
"""

from __future__ import annotations

import json

import streamlit as st

import config
from core import user_properties as up
from data.db import DB_PATH

_BADGE = {
    up.VERIFIED:   ("&#10004;", "#1d9bf0", "Verified", True),
    up.UNVERIFIED: ("&#10003;", "#9aa4b2", "Unverified", False),
    up.PENDING:    ("&#10003;", "#9aa4b2", "Pending validation", False),
    up.FAILED:     ("&#10007;", "#b91c1c", "Failed validation", False),
}


def verification_badge(status: str) -> str:
    """The §16.4 badge as inline HTML. Blue is EARNED: filled circle only
    for VERIFIED; everything else is an outline in its state colour."""
    glyph, color, label, filled = _BADGE.get(status, _BADGE[up.UNVERIFIED])
    bg = color if filled else "transparent"
    fg = "#ffffff" if filled else color
    return (f'<span title="{label}" style="display:inline-flex;'
            f'align-items:center;justify-content:center;width:16px;'
            f'height:16px;border-radius:50%;border:1.5px solid {color};'
            f'background:{bg};color:{fg};font-size:10px;font-weight:700;'
            f'line-height:1">{glyph}</span>')


def render_add_property() -> None:
    c = config.COLORS
    with st.expander("➕ Add a property we haven't uncovered yet"):
        st.caption(
            "Usable by your organization immediately. It earns the blue "
            "check once the address, parcel and unit count are confirmed "
            "against the municipal roll — and joins shared comps only then.")
        with st.form("add_property_form", clear_on_submit=True):
            name = st.text_input("Community name *")
            address = st.text_input("Street address *")
            col1, col2 = st.columns(2)
            with col1:
                city = st.text_input("City *")
            with col2:
                units = st.number_input("Units *", min_value=1, step=1,
                                        value=None)
            parcel = st.text_input("Parcel / tax ID (speeds verification)")
            website = st.text_input("Property website (optional)")
            if st.form_submit_button("Add property"):
                if not (name and address and city and units):
                    st.error("Name, address, city and units are required.")
                else:
                    row = up.submit_property(
                        name=name.strip(), address=address.strip(),
                        city=city.strip(), units=int(units),
                        parcel_id=parcel.strip() or None,
                        website=website.strip() or None,
                        org_id=st.session_state.get("org_id"),
                        db_path=DB_PATH)
                    # Validate inline when the municipal data is on this
                    # machine; otherwise the nightly queue picks it up.
                    res = up.validate_property(row["user_property_id"],
                                               DB_PATH)
                    st.session_state["_last_added_property"] = (
                        row["user_property_id"], res.status, res.reason)
        flash = st.session_state.pop("_last_added_property", None)
        if flash:
            _pid, status, reason = flash
            if status == up.VERIFIED:
                st.success(f"Added and verified: {reason}")
            elif status == up.FAILED:
                st.error(f"Added, but validation failed: {reason}")
            else:
                st.info(f"Added. {reason}")

    _render_my_properties(c)


def _render_my_properties(c) -> None:
    rows = up.list_user_properties(
        DB_PATH, org_id=st.session_state.get("org_id"))
    if not rows:
        return
    st.markdown(f'<div style="color:{c["tx2"]};font-size:13px;'
                f'margin:6px 0 2px"><b>Added by your organization</b></div>',
                unsafe_allow_html=True)
    for r in rows:
        badge = verification_badge(r["status"])
        line = (f'{badge}&nbsp;&nbsp;<b>{r["name"]}</b> — {r["address"]}, '
                f'{r["city"]} · {r["units"]} units')
        st.markdown(f'<div style="font-size:13px;color:{c["tx"]};'
                    f'margin:2px 0">{line}</div>', unsafe_allow_html=True)
        if r["status"] in (up.FAILED, up.VERIFIED) and r["evidence"]:
            with st.expander("Why?", expanded=False):
                ev = json.loads(r["evidence"])
                st.caption(ev.get("reason", ""))
                st.json(ev.get("checks", {}))
