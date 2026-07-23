"""Waterfall tab — pref accrual, ROC schedule, 70/30 splits, LP IRR + EM.

Computed downstream of the Underwriting tab's CF projection. Same dial state
(deal.json) drives both, so changing a slider in Underwriting updates this
tab on the next rerun.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

import config
from core.calc import DebtTerms, build_cashflow, build_debt_schedule
from core.irr import equity_multiple, lp_irr, project_irr
from core.waterfall import run_waterfall
from data.property_io import (
    DealState,
    PropertyFolder,
    load_deal,
    load_sources,
)
from ui.components import section_card


def _derive_year1_inputs(
    deal: DealState,
    sources: dict[str, Any] | None,
) -> tuple[float, float]:
    if sources:
        rev = sources.get("totalRevenue")
        opex = sources.get("totalOpex")
        if rev and opex:
            try:
                rev_v = rev.get("value") if isinstance(rev, dict) else rev
                opex_v = opex.get("value") if isinstance(opex, dict) else opex
                if rev_v and opex_v:
                    return float(rev_v), float(opex_v)
            except (AttributeError, TypeError, ValueError):
                pass
    er = config.EXPENSE_RATIOS.get("C", 0.45)
    vac = deal.vacancy_frac
    denom = (1.0 - vac - er)
    gpr = deal.noi / denom if denom > 0 else deal.noi / 0.5
    expenses = gpr * er
    return gpr, expenses


def _money(v: float) -> str:
    return f"${v:,.0f}"


def _render_exit_cap_model(prop: dict[str, Any], deal: DealState, c: dict) -> None:
    """Model-recommended exit cap + comp evidence. Brian's slider still wins;
    this is just a "you're at X%, model says Y% based on N comps" comparison."""
    from core import exit_cap_model
    try:
        pred = exit_cap_model.predict_exit_cap(
            subject_city=prop.get("city"),
            subject_year_built=prop.get("year_built"),
            subject_units=prop.get("units"),
            subject_class=prop.get("asset_class") or "C",
            subject_lat=prop.get("latitude"),
            subject_lng=prop.get("longitude"),
        )
    except Exception as e:
        with section_card("Comp-Driven Exit Cap", icon="📊"):
            st.error(f"Couldn't compute: {e}")
        return

    dialed = deal.exit_cap
    delta_bps = (dialed - pred.predicted_cap) * 10_000
    if abs(delta_bps) < 20:
        delta_color, delta_label = c["gn"], "Aligned with model"
    elif delta_bps > 0:
        delta_color, delta_label = c["yw"], "Conservative vs model"
    else:
        delta_color, delta_label = c["rd"], "Aggressive vs model"

    with section_card(
        "Comp-Driven Exit Cap Model",
        icon="📊",
        accent="ac",
        subtitle=(
            "Model recommendation from recent HR multifamily sales. "
            "Your dial below still wins — this is for sanity-check."
        ),
    ):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(
                f'<div style="color:{c["tx3"]};font-size:11px;text-transform:uppercase">'
                f'Your dialed exit cap</div>'
                f'<div style="font-size:22px;font-weight:700;color:{c["tx"]};'
                f'font-variant-numeric:tabular-nums">{dialed*100:.2f}%</div>'
                f'<div style="font-size:11px;color:{c["tx3"]}">from slider</div>',
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                f'<div style="color:{c["tx3"]};font-size:11px;text-transform:uppercase">'
                f'Model recommendation</div>'
                f'<div style="font-size:22px;font-weight:700;color:{c["ac3"]};'
                f'font-variant-numeric:tabular-nums">{pred.predicted_cap*100:.2f}%</div>'
                f'<div style="font-size:11px;color:{c["tx3"]}">'
                f'CI: {pred.ci_low*100:.2f}% – {pred.ci_high*100:.2f}% (90%)</div>',
                unsafe_allow_html=True,
            )
        with col3:
            st.markdown(
                f'<div style="color:{c["tx3"]};font-size:11px;text-transform:uppercase">'
                f'Delta</div>'
                f'<div style="font-size:22px;font-weight:700;color:{delta_color};'
                f'font-variant-numeric:tabular-nums">{delta_bps:+.0f} bps</div>'
                f'<div style="font-size:11px;color:{delta_color}">{delta_label}</div>',
                unsafe_allow_html=True,
            )

        if pred.rationale:
            st.caption(" · ".join(pred.rationale))

        if pred.top_comps:
            st.markdown(
                f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
                f'font-weight:600;margin-top:10px;margin-bottom:6px">Top weighted comps</div>',
                unsafe_allow_html=True,
            )
            import pandas as pd
            rows = [{
                "City": comp.city,
                "Address": comp.address[:40],
                "Year": comp.year_built or "",
                "Units (est)": comp.units_estimate or "",
                "Sale Date": comp.sale_date.isoformat(),
                "Sale Price": f"${comp.sale_price:,.0f}",
                "Implied Cap": f"{(comp.implied_cap or 0)*100:.2f}%",
                "Weight": f"{comp.weight:.2f}",
            } for comp in pred.top_comps]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_waterfall(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    c = config.COLORS

    if folder is None:
        st.info("No property folder yet. The waterfall runs off the saved deal dial.")
        return
    deal = load_deal(folder.path)
    if deal is None:
        st.info("No saved underwriting yet. Open the Underwriting tab and adjust the sliders to create one.")
        return

    sources = load_sources(folder.path)
    gpr, expenses = _derive_year1_inputs(deal, sources)

    # Build CF
    debt_terms = DebtTerms(
        loan_amount=deal.loan_amount,
        annual_rate=deal.interest_rate,
        amort_months=config.AMORT_MONTHS,
        io_years=deal.io,
    )
    debt_sched = build_debt_schedule(debt_terms, deal.hp)
    cf = build_cashflow(
        year1_gpr=gpr,
        year1_vacancy_pct=deal.vacancy_frac,
        year1_expenses=expenses,
        rent_growth=deal.rent_growth,
        expense_growth=deal.expense_growth,
        am_fee_pct=deal.am_fee_pct,
        debt=debt_sched,
        hold_years=deal.hp,
        exit_cap=deal.exit_cap,
        equity_raise=deal.equity_raise,
    )

    # Build annual pots: years 1..N-1 = operating CF, year N = combined operating + sale
    annual_pots = [r.cash_flow for r in cf.rows[:-1]]
    annual_pots.append(cf.rows[-1].cash_flow + cf.exit_proceeds_net)

    wf = run_waterfall(
        equity_raise=deal.equity_raise,
        annual_pots=annual_pots,
    )

    # Top metrics row — needed first because both top-of-tab cards display
    # them (Year-1 KPIs needs `units`/`city`; Investor Returns uses LP IRR /
    # project IRR / equity multiple from this CF).
    lp_rate = lp_irr(wf.lp_cashflows)
    proj_rate = project_irr(
        equity_raise=deal.equity_raise,
        annual_cashflows=[r.cash_flow for r in cf.rows],
        exit_proceeds_net=cf.exit_proceeds_net,
    )
    lp_em = equity_multiple(deal.equity_raise, wf.total_lp_distributions)

    # ============================================================
    # 1. Year-1 KPIs — moved here from Underwriting tab per Brian 5/29 v2.0.18
    # ============================================================
    units_for_kpi = prop.get("units")
    city_for_kpi = prop.get("city")
    from ui.underwriting import _render_metrics as _render_year1_kpis
    with section_card("Year-1 KPIs", icon="📊"):
        _render_year1_kpis(deal, sources, units_for_kpi, city=city_for_kpi)

    # ============================================================
    # 2. Investor Returns — moved here from below the risk panels per
    #    Brian 5/29 v2.0.18 (returns belong above the risk lenses).
    # ============================================================
    with section_card("Investor Returns"):
        col1, col2, col3, col4 = st.columns(4)

        lp_irr_pct = (lp_rate or 0) * 100
        lp_target = config.LP_IRR_TARGET * 100
        lp_color = c["gn"] if lp_irr_pct >= lp_target else c["rd"]
        col1.markdown(
            f"<div style='color:{c['tx3']};font-size:11px;text-transform:uppercase'>LP IRR</div>"
            f"<div style='font-size:24px;font-weight:600;color:{lp_color}'>{lp_irr_pct:.2f}%</div>"
            f"<div style='font-size:10px;color:{c['tx3']}'>target ≥ {lp_target:.0f}%</div>",
            unsafe_allow_html=True,
        )

        proj_pct = (proj_rate or 0) * 100
        proj_target = config.PROJECT_IRR_TARGET * 100
        proj_color = c["gn"] if proj_pct >= proj_target else c["rd"]
        col2.markdown(
            f"<div style='color:{c['tx3']};font-size:11px;text-transform:uppercase'>Project IRR</div>"
            f"<div style='font-size:24px;font-weight:600;color:{proj_color}'>{proj_pct:.2f}%</div>"
            f"<div style='font-size:10px;color:{c['tx3']}'>target ≥ {proj_target:.0f}%</div>",
            unsafe_allow_html=True,
        )

        em_target = config.LP_EQUITY_MULTIPLE_TARGET
        em_color = c["gn"] if lp_em >= em_target else c["rd"]
        col3.markdown(
            f"<div style='color:{c['tx3']};font-size:11px;text-transform:uppercase'>LP Equity Multiple</div>"
            f"<div style='font-size:24px;font-weight:600;color:{em_color}'>{lp_em:.2f}x</div>"
            f"<div style='font-size:10px;color:{c['tx3']}'>target ≥ {em_target:.1f}x</div>",
            unsafe_allow_html=True,
        )

        col4.markdown(
            f"<div style='color:{c['tx3']};font-size:11px;text-transform:uppercase'>LP Capital Raised</div>"
            f"<div style='font-size:24px;font-weight:600;color:{c['tx']}'>${deal.equity_raise:,.0f}</div>"
            f"<div style='font-size:10px;color:{c['tx3']}'>100% LP, 0% GP co-invest</div>",
            unsafe_allow_html=True,
        )

    # ============================================================
    # 2b. Asset Management Fee in DOLLARS — Brian 5/31. Right below the
    #     LP returns boxes: what the GP's AM fee actually pays out across
    #     the hold (the cashflow rows carry per-year am_fee; the exit year
    #     carries none).
    # ============================================================
    am_fees = [float(getattr(r, "am_fee", 0.0) or 0.0) for r in cf.rows]
    am_total = sum(am_fees)
    am_y1 = am_fees[0] if am_fees else 0.0
    # Average across the fee-earning years (exclude the $0 exit year).
    fee_years = [f for f in am_fees if f > 0]
    am_avg = (sum(fee_years) / len(fee_years)) if fee_years else 0.0
    am_rate_pct = float(getattr(deal, "am_fee_pct", 0.0) or 0.0) * 100
    hold_years = int(getattr(deal, "hp", len(am_fees)) or len(am_fees))

    with section_card(
        "Asset Management Fee",
        icon="💼",
        subtitle=f"{am_rate_pct:.1f}% of gross rent · GP earns this across the {hold_years}-year hold",
    ):
        def _fee_tile(label, value, foot, color=None):
            col = color or c["tx"]
            return (
                f"<div style='color:{c['tx3']};font-size:11px;"
                f"text-transform:uppercase'>{label}</div>"
                f"<div style='font-size:24px;font-weight:600;color:{col}'>{value}</div>"
                f"<div style='font-size:10px;color:{c['tx3']}'>{foot}</div>"
            )
        g1, g2, g3 = st.columns(3)
        g1.markdown(_fee_tile(
            "Year-1 AM Fee", f"${am_y1:,.0f}", f"{am_rate_pct:.1f}% of Year-1 gross rent"
        ), unsafe_allow_html=True)
        g2.markdown(_fee_tile(
            "Avg Annual", f"${am_avg:,.0f}", "per fee-earning year"
        ), unsafe_allow_html=True)
        g3.markdown(_fee_tile(
            "Total Over Hold", f"${am_total:,.0f}",
            f"{hold_years} yrs (exit year earns none)", color=c["ac"],
        ), unsafe_allow_html=True)

    # ============================================================
    # 3. Risk lenses (moved DOWN from the top of the tab per Brian
    #    5/29 v2.0.18 — they sit between the returns story and the
    #    detailed schedules now).
    # ============================================================

    # Comp-driven exit cap recommendation
    _render_exit_cap_model(prop, deal, c)

    # Seller Floor reverse-engineering — drives LOI anchor offer
    from ui.seller_floor_panel import render_seller_floor_panel
    # Pass folder so the panel can auto-fill from the Subject tab's Sale
    # History (v2.0.37) instead of just the assessor ETL fallback.
    render_seller_floor_panel(prop, folder=folder)

    # Monte Carlo probabilistic risk view (10K paths)
    from ui.monte_carlo_panel import render_monte_carlo_panel
    render_monte_carlo_panel(deal, year1_noi=cf.rows[0].noi if cf.rows else 0)

    # Year-by-year waterfall schedule
    with section_card("Year-by-Year Waterfall"):
        schedule = []
        for yr in wf.years:
            schedule.append({
                "Year": yr.year,
                "Cash pot": _money(yr.pot),
                "Pref accrued": _money(yr.pref_accrued_this_year),
                "Pref carry start": _money(yr.pref_owed_start),
                "Pref paid": _money(yr.pref_paid),
                "Pref carry end": _money(yr.pref_owed_end),
                "ROC paid": _money(yr.roc_paid),
                "LP cap remaining": _money(yr.lp_capital_remaining_end),
                "Residual (Tier 3)": _money(yr.residual),
                "LP distribution": _money(yr.lp_distribution),
                "GP distribution": _money(yr.gp_distribution),
            })
        df = pd.DataFrame(schedule)
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Distribution summary
    with section_card("Distribution Summary"):
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                f"""
**LP cash flows** (for IRR):
```
Year 0: ${wf.lp_cashflows[0]:,.0f}  ← initial equity outflow
""" + "\n".join(
                    f"Year {i}: ${cf:,.0f}"
                    for i, cf in enumerate(wf.lp_cashflows[1:], start=1)
                ) + "\n```"
            )
        with col_b:
            st.markdown(
                f"""
**GP cash flows** (promote only, no co-invest):
```
""" + "\n".join(
                    f"Year {i}: ${cf:,.0f}"
                    for i, cf in enumerate(wf.gp_cashflows[1:], start=1)
                ) + "\n```"
            )

        # Use plain markdown with explicit spacing — &nbsp; entities don't render
        # in st.markdown without unsafe_allow_html, which made this line ugly.
        col_lp_total, col_gp_total = st.columns(2)
        with col_lp_total:
            st.metric("Total LP distributions", _money(wf.total_lp_distributions))
        with col_gp_total:
            st.metric("Total GP distributions", _money(wf.total_gp_distributions))

        st.caption(
            "Mechanics: 8% pref (cumulative, non-compounded) on unreturned LP capital → "
            "Return of LP capital → 70 LP / 30 GP residual split. Sale-year operating "
            "CF and net sale proceeds combine into a single year-N pot per Eight Rock "
            "convention."
        )
