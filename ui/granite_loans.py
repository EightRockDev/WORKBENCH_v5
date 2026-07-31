"""GRANITE Loans module (spec 6.1, Tabs 2-5) - the daily-return hook.

Four tabs on one surface:
  Lenders        - who actually writes multifamily paper here, all years
  Loan Comps     - comparable originations banded around a deal size
  Borrower Intel - an entity's whole footprint on the 8R backbone,
                   one click from Module A contact resolution
  Alerts         - loan-maturity pressure + reassessment spikes

Everything renders from deterministic queries (core/granite_loans.py);
panels hide themselves when their data source isn't on this machine.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from core import granite_loans as gl
from core.market_data import HR_CITY_TO_COUNTY_FIPS_5


def _fmt_money(v) -> str:
    return f"${v:,.0f}" if v else "—"


def render_granite_loans() -> None:
    c = config.COLORS
    st.markdown("## 🏦 GRANITE Loans")
    st.caption("Lender database · loan comps · borrower intelligence — "
               "built from public HMDA records and the Eight Rock backbone.")

    tab_lenders, tab_comps, tab_intel, tab_alerts = st.tabs([
        "🏛️ Lenders", "📊 Loan Comps", "🕵️ Borrower Intel", "🔔 Alerts"])

    cities = ["All Hampton Roads"] + list(HR_CITY_TO_COUNTY_FIPS_5)

    with tab_lenders:
        city = st.selectbox("Market", cities, key="gl_lender_city")
        rows = gl.lender_history(None if city == cities[0] else city)
        if not rows:
            st.info("Lender data not available on this machine yet — the "
                    "ETL database (hampton_roads.db) supplies it.")
        else:
            df = pd.DataFrame(rows)
            df["total_volume"] = df["total_volume"].map(_fmt_money)
            df["avg_median_loan"] = df["avg_median_loan"].map(_fmt_money)
            df["avg_rate_spread"] = df["avg_rate_spread"].map(
                lambda v: f"{v:.2f}" if v is not None else "—")
            df["active"] = df["first_year"].astype(str) + "–" + \
                df["last_year"].astype(str)
            st.dataframe(
                df[["lender_name", "originations", "total_volume",
                    "avg_median_loan", "avg_rate_spread", "active"]],
                use_container_width=True, hide_index=True)
            st.caption("Multifamily originations per HMDA. Rate spread is "
                       "vs. the average prime offer rate — a lender whose "
                       "spread runs low is the first call on a refi.")

    with tab_comps:
        col1, col2, col3 = st.columns(3)
        with col1:
            city2 = st.selectbox("Market", list(HR_CITY_TO_COUNTY_FIPS_5),
                                 key="gl_comps_city")
        with col2:
            lo = st.number_input("Min loan $", value=0, step=250_000,
                                 key="gl_comps_lo")
        with col3:
            hi = st.number_input("Max loan $ (0 = no cap)", value=0,
                                 step=250_000, key="gl_comps_hi")
        rows = gl.loan_comps(city2, min_amount=lo or None,
                             max_amount=hi or None)
        if not rows:
            st.info("No originations match — widen the band, or the ETL "
                    "database isn't on this machine yet.")
        else:
            df = pd.DataFrame(rows)
            df["loan_amount"] = df["loan_amount"].map(_fmt_money)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"{len(rows)} comparable originations, newest "
                       "first. LTV and rate spread as reported to HMDA.")

    with tab_intel:
        st.markdown("Search an **owner entity** to see its whole Hampton "
                    "Roads footprint on the Eight Rock backbone.")
        frag = st.text_input("Entity / owner name (3+ characters)",
                             key="gl_intel_q",
                             placeholder="e.g. GRANBY HOLDINGS")
        from core.phase0 import find_workbench_db
        db = find_workbench_db()
        rows = gl.entity_portfolio(frag, db) if (frag and db) else []
        if frag and not rows:
            st.info("No backbone parcels match that owner. (3+ characters; "
                    "the backbone builds nightly from municipal rolls.)")
        if rows:
            roll = gl.portfolio_rollup(rows)
            m1, m2, m3 = st.columns(3)
            m1.metric("Parcels", f"{roll['parcels']:,}")
            m2.metric("Units", f"{roll['units']:,}")
            m3.metric("Assessed", _fmt_money(roll["assessed_value"]))
            df = pd.DataFrame(rows)
            df["assessed_value"] = df["assessed_value"].map(_fmt_money)
            st.dataframe(
                df[["owner_name", "address", "city", "units",
                    "year_built", "assessed_value", "use_code"]],
                use_container_width=True, hide_index=True)
            st.caption("Next step: open any of these in Deal Analysis and "
                       "run **Resolve Contacts** (Owner Intelligence) to "
                       "pierce the entity to a human with Module A.")

    with tab_alerts:
        # Durable alerts from the nightly sweep (core/alerts.py).
        from core.alerts import dismiss, open_alerts
        from core.phase0 import find_workbench_db
        db = find_workbench_db()
        sweep_rows = open_alerts(db) if db else []
        if sweep_rows:
            st.markdown(f"**{len(sweep_rows)} open alerts** from the "
                        "nightly backbone sweep")
            from core.alerts import queue_for_outreach
            for a in sweep_rows[:50]:
                col_a, col_b, col_c = st.columns([6, 1.4, 1])
                with col_a:
                    st.markdown(f"**{a['headline']}**  \n"
                                f"{a['detail']} · {a['created_at'][:10]}")
                with col_b:
                    if st.button("📞 To Outreach",
                                 key=f"gl_route_{a['id']}"):
                        queue_for_outreach(db, a["id"])
                        st.rerun()
                with col_c:
                    if st.button("Dismiss", key=f"gl_dismiss_{a['id']}"):
                        dismiss(db, a["id"])
                        st.rerun()
        else:
            st.caption("No open sweep alerts — the nightly cycle writes "
                       "them here when the backbone changes.")
        st.divider()
        # Reuse the C3 loan-maturity pressure panel (county HMDA volume +
        # 2020-22 purchase cohort) so the loan surface owns its alert.
        try:
            from ui.pipeline import _render_loan_maturity_alert
            _render_loan_maturity_alert()
        except Exception:
            st.info("Loan-maturity alerting needs the ETL database on "
                    "this machine.")
        st.caption("Coming next: saved-search alerts routed to Outreach "
                   "(spec 6.1) — radar hits become dial lists "
                   "automatically.")
