"""Portfolio Risk Dashboard — new top-level tab.

Aggregates across every property in the book. Run by the user from the
sidebar's Portfolio module. Doesn't depend on a selected property.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd

import config
from core import portfolio_risk
from ui.components import section_card


def render_portfolio() -> None:
    """Top-level portfolio dashboard."""
    c = config.COLORS

    with st.spinner("Aggregating portfolio…"):
        rollup = portfolio_risk.build_rollup()

    if not rollup.properties:
        st.info(
            "No properties have underwriting or LP ledgers saved yet. "
            "Add LPs to a property's Investors tab to populate this view."
        )
        return

    # ---- Header KPI tiles ----
    _render_kpis(rollup, c)

    # ---- Warnings ----
    if rollup.warnings:
        with section_card("Concentration Warnings", icon="⚠️"):
            for w in rollup.warnings:
                st.warning(w)

    # ---- Property table ----
    _render_property_table(rollup)

    # ---- Concentration breakdowns ----
    _render_concentration(rollup, c)

    # ---- Rate shock ----
    _render_rate_shock(rollup, c)


def _render_kpis(rollup, c: dict) -> None:
    with section_card(
        f"Portfolio Overview — {len(rollup.properties)} properties",
        icon="📊", accent="ac",
        subtitle="Aggregated across every property with underwriting or an LP ledger.",
    ):
        col1, col2, col3, col4 = st.columns(4)
        col1.markdown(_tile(
            "Total Purchase Price",
            f"${rollup.total_purchase_price:,.0f}",
            f"{rollup.total_units:,} units",
            color=c["bl"], c=c,
        ), unsafe_allow_html=True)
        col2.markdown(_tile(
            "Total Loan",
            f"${rollup.total_loan_amount:,.0f}",
            f"{(rollup.total_loan_amount/rollup.total_purchase_price*100 if rollup.total_purchase_price else 0):.0f}% LTV",
            color=c["yw"], c=c,
        ), unsafe_allow_html=True)
        col3.markdown(_tile(
            "LP Committed",
            f"${rollup.total_committed:,.0f}",
            f"{rollup.total_lps} LP slots",
            color=c["gn"], c=c,
        ), unsafe_allow_html=True)
        col4.markdown(_tile(
            "Outstanding to LPs",
            f"${rollup.total_outstanding:,.0f}",
            f"${rollup.total_distributed:,.0f} distributed",
            color=c["ac3"], c=c,
        ), unsafe_allow_html=True)


def _render_property_table(rollup) -> None:
    with section_card("Per-Property Breakdown", icon="🏢"):
        rows = []
        for p in rollup.properties:
            rows.append({
                "Property": p.name,
                "City": p.city or "?",
                "Units": p.units or "",
                "Purchase Price": f"${p.purchase_price:,.0f}" if p.purchase_price else "—",
                "Loan": f"${p.loan_amount:,.0f}" if p.loan_amount else "—",
                "Equity Raised": f"${p.equity_raise:,.0f}" if p.equity_raise else "—",
                "LPs": p.lp_count,
                "LP Committed": f"${p.total_committed:,.0f}" if p.total_committed else "—",
                "Distributed": f"${p.total_distributed:,.0f}" if p.total_distributed else "—",
                "Outstanding": f"${p.total_unreturned:,.0f}" if p.total_unreturned else "—",
            })
        df = pd.DataFrame(rows).sort_values("LP Committed", ascending=False)
        st.dataframe(df, hide_index=True, use_container_width=True)


def _render_concentration(rollup, c: dict) -> None:
    with section_card("Concentration", icon="📐",
                      subtitle="Where the book is leaning. Flagged in red if exceeds limits."):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(
                f'<div style="font-size:12px;color:{c["tx2"]};font-weight:600;'
                f'margin-bottom:6px">By City</div>',
                unsafe_allow_html=True,
            )
            total_eq = rollup.total_equity_raised or rollup.total_committed or 1
            for city, amt in sorted(rollup.by_city.items(), key=lambda kv: -kv[1]):
                share = amt / total_eq
                color = c["rd"] if share > 0.40 else c["tx2"]
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:4px 0;font-size:13px">'
                    f'<span style="color:{c["tx"]}">{city}</span>'
                    f'<span style="color:{color};font-weight:600">'
                    f'${amt:,.0f} ({share*100:.0f}%)</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        with col2:
            st.markdown(
                f'<div style="font-size:12px;color:{c["tx2"]};font-weight:600;'
                f'margin-bottom:6px">By Vintage Decade</div>',
                unsafe_allow_html=True,
            )
            for dec, amt in sorted(rollup.by_vintage_decade.items()):
                share = amt / total_eq
                color = c["rd"] if share > 0.50 else c["tx2"]
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:4px 0;font-size:13px">'
                    f'<span style="color:{c["tx"]}">{dec}</span>'
                    f'<span style="color:{color};font-weight:600">'
                    f'${amt:,.0f} ({share*100:.0f}%)</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown('<div style="margin-top:14px"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:12px;color:{c["tx2"]};font-weight:600;'
            f'margin-bottom:6px">Loan Maturity Pipeline</div>',
            unsafe_allow_html=True,
        )
        rows = []
        for yr, amt in sorted(rollup.by_loan_maturity_year.items()):
            share = amt / total_eq if total_eq else 0
            rows.append({
                "Year": yr,
                "Equity at Risk": f"${amt:,.0f}",
                "% of Book": f"{share*100:.0f}%",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _render_rate_shock(rollup, c: dict) -> None:
    with section_card("Rate Shock Sensitivity", icon="🌪️"):
        col1, col2, col3 = st.columns(3)
        col1.metric(
            "+100 bps shock",
            f"${rollup.total_loan_amount * 0.01:,.0f}/yr",
            "additional debt service",
        )
        col2.metric(
            "+200 bps shock",
            f"${rollup.rate_shock_200bp_annual_cost:,.0f}/yr",
            "additional debt service",
        )
        col3.metric(
            "+300 bps shock",
            f"${rollup.total_loan_amount * 0.03:,.0f}/yr",
            "additional debt service",
        )
        st.caption(
            "Assumes 100% of loan principal is rate-sensitive. Floating-rate "
            "debt + maturity refinancings within hold period both expose the "
            "book to this — bake into deal-level Monte Carlo when that ships."
        )


def _tile(label: str, value: str, sub: str, color: str, c: dict) -> str:
    return (
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {color};border-radius:6px;padding:10px 14px">'
        f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
        f'letter-spacing:0.6px;font-weight:600">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color};'
        f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:3px">{value}</div>'
        f'<div style="color:{c["tx3"]};font-size:11px;margin-top:2px">{sub}</div>'
        f'</div>'
    )
