"""Module C UI — Forced-Seller Radar v2 (spec §6.1).

Shows the fused 0-100 distress score with the **evidence panel behind every
score**: each component's contribution and the facts that produced it, so the
number is never unexplained.

Signals not yet in the property spine (tax delinquency, permits, listing, loan
maturity) are entered/overridden here until Phase 0 and the GRANITE feed wire
them in automatically — the scorer is already reading them from one dict.
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from core import radar_v2 as rv
from core.permissions import Permissions
from core.skiptrace import pipeline as sk
from data import pg

_BAND_STYLE = {
    "ACT":     ("#b91c1c", "#fee2e2", "ACT NOW"),
    "WATCH":   ("#b45309", "#fef3c7", "WATCH"),
    "MONITOR": ("#15803d", "#dcfce7", "MONITOR"),
}
_LABEL = {
    "loan_maturity": "Loan maturity", "tax_delinquency": "Tax delinquency",
    "poc_signals": "Owner signals (Module A)", "tenure": "Ownership tenure",
    "permit_decay": "Permit decay", "listing": "Listing activity",
}


def render_radar(prop: dict | None) -> None:
    if not prop:
        return
    perms = st.session_state.get("perms")
    if isinstance(perms, Permissions) and not perms.can_open("granite"):
        return   # radar lives behind the granite module grant

    st.subheader("📡 Forced-Seller Radar v2")
    st.caption("One 0–100 distress score fusing loan maturity, tax delinquency, "
               "owner signals, tenure, permit decay and listing activity — with "
               "the evidence behind every point.")

    org_id = st.session_state.get("org_id")
    pocs = []
    if pg.is_configured() and org_id:
        try:
            pocs = sk.load_pocs(org_id, str(prop.get("property_id")))
        except Exception:
            pocs = []

    with st.expander("Signal inputs (until Phase 0 / GRANITE wire these automatically)"):
        c1, c2, c3 = st.columns(3)
        has_loan = c1.checkbox("Loan maturity known", value=True, key="rv-hasloan")
        mat = c1.date_input("Matures", value=dt.date(2027, 3, 1), key="rv-mat") if has_loan else None
        ltype = c1.text_input("Loan type", value="HUD", key="rv-ltype")
        delinq = c2.number_input("Years tax delinquent", 0.0, 10.0, 0.0, 0.5, key="rv-delinq")
        permits = c2.number_input("Permits last 5 years", 0, 50, 0, key="rv-permits")
        listed = c3.checkbox("Currently listed", key="rv-listed")
        delisted = c3.number_input("Delisted N days ago (0 = n/a)", 0, 2000, 0, key="rv-delisted")
        dissolved = c3.checkbox("Entity dissolution filing", key="rv-diss")

    signals = {
        "loan_maturity": mat if has_loan else None, "loan_type": ltype or None,
        "years_delinquent": float(delinq), "permits_last_5y": int(permits),
        "listed_now": bool(listed),
        "delisted_within_days": int(delisted) or None,
        "entity_dissolved": bool(dissolved),
    }
    score = rv.score_property(prop, pocs=pocs, signals=signals)

    fg, bg, label = _BAND_STYLE[score.band]
    c1, c2 = st.columns([1, 3])
    c1.markdown(
        f"<div style='background:{bg};border:1px solid {fg};border-radius:10px;"
        f"padding:14px;text-align:center'>"
        f"<div style='font-size:34px;font-weight:800;color:{fg};line-height:1'>"
        f"{score.score:.0f}</div>"
        f"<div style='font-size:11px;font-weight:700;color:{fg};letter-spacing:1px'>"
        f"{label}</div></div>", unsafe_allow_html=True)
    with c2:
        for comp in sorted(score.components, key=lambda c: -c.contribution):
            pct = comp.contribution / max(score.score, 1) * 100 if score.score else 0
            st.markdown(
                f"**{_LABEL.get(comp.key, comp.key)}** — {comp.contribution:.1f} pts "
                f"({comp.score:.0f}/100 × {comp.weight:.0%})"
                + (f"  ·  {pct:.0f}% of total" if score.score else ""))
            st.progress(min(1.0, comp.score / 100))

    st.markdown("**Evidence**")
    for e in score.evidence:
        st.markdown(f"- {e}")

    if not pocs:
        st.caption("💡 Run **Resolve Contacts** (Owner Intelligence) to add the v5.0 "
                   "owner signals — deceased flag, absentee mailing address, "
                   "portfolio size, entity dissolution.")
