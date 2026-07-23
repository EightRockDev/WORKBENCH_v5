"""Monte Carlo refi/exit panel — Returns & Waterfall tab.

10,000-path probabilistic risk view. Complements the existing 4-scenario
fixed-shock panel (`Refi / Exit Stress Test` in the Underwriting tab) —
where that gives 4 discrete answers, this gives distributions:
P(refi fails), P(LP IRR < 12%), CVaR-95, fan charts.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
import pandas as pd

import config
from core import monte_carlo as mc
from data.property_io import DealState
from ui.components import section_card


def render_monte_carlo_panel(
    deal: DealState, year1_noi: float, current_10y: float | None = None,
) -> None:
    c = config.COLORS
    if not deal or year1_noi <= 0:
        return

    with section_card(
        "Monte Carlo Risk View (10,000 paths)",
        icon="🎲",
        accent="ac",
        subtitle=(
            "Probabilistic stress test. Distributions over exit cap, refi rate, "
            "rent growth, vacancy, op-shocks. P(refi fails), P(LP IRR < 12%), "
            "CVaR-95, percentile fan chart."
        ),
    ):
        col_a, col_b, col_c = st.columns([1, 1, 2])
        with col_a:
            n_paths = st.selectbox(
                "Paths", [1000, 5000, 10_000], index=2,
                help="More = smoother distribution; 10K is the standard.",
                key=f"mc_paths_{id(deal)}",
            )
        with col_b:
            seed = st.number_input(
                "Seed", min_value=0, max_value=99999, value=42,
                help="Deterministic — change to see a different draw.",
                key=f"mc_seed_{id(deal)}",
            )
        with col_c:
            if st.button("▶ Run Monte Carlo", key=f"mc_run_{id(deal)}", type="primary"):
                st.session_state[f"mc_result_{id(deal)}"] = "compute"

        result_key = f"mc_result_{id(deal)}"
        if st.session_state.get(result_key) != "compute":
            st.caption("Click ▶ to run. ~2 seconds for 10K paths.")
            return

        inputs = mc.MonteCarloInputs(
            year1_noi=year1_noi,
            purchase_price=deal.pp,
            loan_amount=deal.loan_amount,
            interest_rate=deal.interest_rate,
            amort_months=config.AMORT_MONTHS,
            io_years=deal.io,
            hold_years=deal.hp,
            equity_raise=deal.equity_raise,
            base_rent_growth=deal.rent_growth,
            base_expense_growth=deal.expense_growth,
            base_vacancy=deal.vacancy_frac,
            base_exit_cap=deal.exit_cap,
            base_refi_rate=deal.interest_rate,
            current_10y=current_10y or 0.045,
        )

        with st.spinner(f"Running {n_paths:,} paths..."):
            result = mc.run_monte_carlo(inputs, n_paths=n_paths, seed=seed)
        st.session_state[result_key] = result

        # ---- Verdict tile ----
        verdict_color = {
            "GO": c["gn"], "WATCH": c["yw"], "NO-GO": c["rd"],
        }.get(result.verdict, c["tx3"])
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {verdict_color};'
            f'border-radius:4px;padding:12px;margin-bottom:14px">'
            f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'font-weight:600;letter-spacing:0.5px">Probabilistic Verdict</div>'
            f'<div style="font-size:22px;color:{verdict_color};font-weight:700;'
            f'margin-top:4px">{result.verdict}</div>'
            f'<div style="font-size:13px;color:{c["tx"]};margin-top:6px;'
            f'line-height:1.5">{result.verdict_reason}</div></div>',
            unsafe_allow_html=True,
        )

        # ---- KPI grid ----
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(
            "P(refi fails)",
            f"{result.prob_refi_fails*100:.1f}%",
            "Target < 10%",
            delta_color="inverse",
        )
        col2.metric(
            "P(LP IRR < 12%)",
            f"{result.prob_lp_irr_below_12pct*100:.1f}%",
            "Target < 25%",
            delta_color="inverse",
        )
        col3.metric(
            "P(LP IRR < 0%)",
            f"{result.prob_lp_irr_below_0pct*100:.1f}%",
            "Target ~ 0%",
            delta_color="inverse",
        )
        col4.metric(
            "CVaR-95 LP IRR",
            f"{result.cvar_95_lp_irr*100:.1f}%",
            "Expected loss in bottom 5%",
        )

        # ---- Percentile distribution ----
        st.markdown(
            f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600;margin-top:18px;margin-bottom:6px">'
            f'LP IRR distribution at year {deal.hp}</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(5)
        for col, (label, value) in zip(cols, [
            ("5%ile (worst)", result.lp_irr_p5),
            ("25%ile", result.lp_irr_p25),
            ("Median", result.median_lp_irr),
            ("75%ile", result.lp_irr_p75),
            ("95%ile (best)", result.lp_irr_p95),
        ]):
            col.metric(label, f"{value*100:.1f}%")

        # ---- Equity Multiple distribution ----
        st.markdown(
            f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600;margin-top:14px;margin-bottom:6px">'
            f'Equity Multiple distribution</div>',
            unsafe_allow_html=True,
        )
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("5%ile", f"{result.em_p5:.2f}x")
        col_b.metric("Median", f"{result.em_p50:.2f}x")
        col_c.metric("95%ile", f"{result.em_p95:.2f}x")

        # ---- Fan chart by year ----
        st.markdown(
            f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600;margin-top:14px;margin-bottom:6px">'
            f'LP IRR fan chart by hold year</div>',
            unsafe_allow_html=True,
        )
        fan_rows = []
        for y in sorted(result.fan_chart.keys()):
            fan = result.fan_chart[y]
            fan_rows.append({
                "Year": y,
                "5%ile": f"{fan['p5']*100:.1f}%",
                "25%ile": f"{fan['p25']*100:.1f}%",
                "Median": f"{fan['p50']*100:.1f}%",
                "75%ile": f"{fan['p75']*100:.1f}%",
                "95%ile": f"{fan['p95']*100:.1f}%",
                "P(refi fails)": f"{fan['prob_refi_fails']*100:.1f}%",
            })
        st.dataframe(pd.DataFrame(fan_rows), hide_index=True, use_container_width=True)
