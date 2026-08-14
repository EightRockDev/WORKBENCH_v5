"""Input tab — the quick-start "first numbers" front door (first-user feedback).

Brian's first-user feedback (2026-08): "The first tab should be called 'Input',
where they enter the first numbers." New investors open a property and want one
obvious place to type purchase price and NOI and get an immediate read — not to
hunt through the full Underwriting dial board.

This tab is deliberately a *front door*, not a second underwriting engine. It
writes the SAME `deal.json` the Underwriting tab reads, through the SAME
`save_deal` path (with FR-9.3.1 version-checked writes), and seeds new deals from
the SAME `build_default_deal` helper. So there is exactly one source of truth:
edit the five headline numbers here, then click through to Underwriting for the
full model — nothing diverges. Uses an explicit `st.form` submit (no auto-save)
so this tab can never enter the rerun/fade loop the dial board once did.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import config
from core.calc import cap_rate
from data.property_io import (
    DealState,
    PropertyFolder,
    ensure_property_folder,
    load_deal,
    save_deal,
)
from ui.components import section_card
from ui.underwriting import _current_actor, build_default_deal


def _money(v: float | None) -> str:
    return f"${v:,.0f}" if v else "—"


def render_input(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    units = prop.get("units") or 0
    city = prop.get("city") or ""

    st.caption(
        "Enter the first numbers here to get an instant read. This is the "
        "quick-start front door — the same deal you'll open in **Underwriting** "
        "for the full model. Type the numbers, then Save."
    )

    # Property identity comes from the record (assessor roll / custom props),
    # not from deal.json — show it read-only so the analyst knows which deal
    # they're pricing without being able to accidentally rename it here.
    with section_card("Property", icon="🏢"):
        b1, b2, b3 = st.columns(3)
        b1.markdown(f"**{prop.get('name') or '—'}**  \n{prop.get('address') or ''}")
        b2.markdown(f"**{units or '—'}** units")
        b3.markdown(f"{city}{', ' + prop.get('state') if prop.get('state') else ''}")

    # Load the saved deal or seed defaults from the record (shared helper).
    deal = load_deal(folder.path) if folder is not None else None
    seeded = deal is None
    if deal is None:
        deal = build_default_deal(prop)

    with section_card("First numbers", icon="✏️"):
        if seeded:
            # Name the seed's BASIS inline (owner ask 2026-08-13): a seeded
            # price used to be units x a fixed $/unit with nothing to
            # distinguish it from a real number. An asset-anchored seed
            # informs; a market placeholder warns.
            from core import deal_seed
            _seed = deal_seed.build_seed(prop)
            _msg = ("No deal saved yet — the fields below are seeded from "
                    f"the property record. {deal_seed.seed_caption(_seed)} "
                    "Adjust and Save to create the deal.")
            (st.info if _seed.is_anchored else st.warning)(_msg)
        with st.form("input_first_numbers"):
            c1, c2 = st.columns(2)
            with c1:
                pp = st.number_input(
                    "Purchase price ($)", min_value=0, value=int(deal.pp),
                    step=5_000,
                    help="What you'd pay. Drives price-per-unit and going-in cap.")
                noi = st.number_input(
                    "Net operating income — NOI ($/yr)", min_value=0,
                    value=int(deal.noi), step=1_000,
                    help="In-place annual NOI. Going-in cap = NOI ÷ purchase price.")
            with c2:
                # Down payment lives on the Underwriting tab only (owner
                # 2026-08-13). It is deliberately NOT collected here: the
                # save below applies model_copy ON TOP of the loaded deal,
                # so an underwriter's saved dp survives a first-tab save
                # untouched.
                ir = st.number_input(
                    "Interest rate (%)", min_value=3.0, max_value=12.0,
                    value=float(deal.ir), step=0.1, format="%.1f")
                hp = st.number_input(
                    "Hold period (years)", min_value=3, max_value=10,
                    value=int(deal.hp), step=1)
            submitted = st.form_submit_button(
                "💾 Save", type="primary", use_container_width=True)

        if submitted:
            # No "dp" key: omitting it makes new_deal inherit the saved
            # down payment verbatim instead of clobbering it.
            new_deal = deal.model_copy(update={
                "pp": float(pp), "noi": float(noi), "hp": int(hp),
                "ir": float(ir),
            })
            save_folder = folder
            if save_folder is None:
                save_folder = ensure_property_folder(prop)
            res = save_deal(save_folder.path, new_deal,
                            expected_version=deal.row_version,
                            actor=_current_actor())
            if not res.ok:
                who = res.conflict_by or "someone else"
                st.error(
                    f"⚠️ **{who}** saved this deal while you were editing "
                    f"(now v{res.version}). Nothing was overwritten — reopen the "
                    "tab to load their copy, then re-enter your numbers.")
            else:
                deal = new_deal
                st.success("✓ Saved. Scroll down for the first look, or open "
                           "**Underwriting** for the full model.")

    # First look — instant read from whatever is currently on screen/saved.
    with section_card("First look", icon="📈"):
        ppu = (deal.pp / units) if units else 0.0
        cap = cap_rate(deal.noi, deal.pp)
        m1, m2, m3 = st.columns(3)
        m1.metric("Purchase price", _money(deal.pp))
        m2.metric("Price / unit", _money(ppu) if units else "—")
        m3.metric("Going-in cap", f"{cap*100:.2f}%" if deal.pp else "—")
        st.caption(
            "This is the going-in snapshot. Financing, growth, exit cap and the "
            "full 5-year model — plus the GO / WATCH / NO-GO verdict — live on "
            "the Underwriting and Summary tabs."
        )
        # A bare <a href="?goto=underwriting"> replaced the WHOLE query
        # string (RFC 3986 relative-reference resolution), dropping the
        # ?prop=<id> that identifies the open property - so the click threw
        # the user back to the property list instead of switching tabs
        # (owner 2026-08-13). Set ?goto= and rerun instead: assigning ONE
        # key on st.query_params leaves ?prop= intact, and
        # app.py::_sticky_property_tab consumes goto at the TOP of the next
        # run - before the selector widget is created. Writing
        # st.session_state["ptab_sel"] from here would raise: that widget
        # has already been instantiated by the time this tab body runs.
        if st.button("Open full Underwriting →", key="input_to_underwriting",
                     type="secondary"):
            st.query_params["goto"] = "underwriting"
            st.rerun()
