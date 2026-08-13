"""Underwriting tab — deal dials, live metrics, 5-yr CF, sensitivity, verdict.

Reads the selected property's deal.json + sources.json, exposes the dial values
as Streamlit sliders, recomputes everything on each interaction, and persists
changes back to deal.json.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

import config
from core.calc import (
    DebtTerms,
    amortized_debt_constant,
    breakeven_occupancy,
    build_cashflow,
    build_debt_schedule,
    cap_rate,
    cash_on_cash,
    debt_yield,
    dscr,
    effective_year1_vacancy,
    expense_ratio,
    return_on_cost,
)
from core.irr import project_irr
from core.market_data import get_macro_indicators, is_etl_available
from ui.calibration_panel import render_market_calibration_panel
from core.mill_rates import (
    DEFAULT_REASSESSMENT_RATIO,
    estimated_post_sale_tax,
    get_mill_rate,
)
from core.risk_metrics import run_refi_exit_test
from core.sensitivity import SensitivityBase, build_sensitivity
from core.verdict import evaluate
from data.property_io import (
    DealState,
    PropertyFolder,
    ensure_property_folder,
    load_deal,
    save_deal,
)
from ui.components import section_card
from ui.value_add import (
    _render_cost_seg_hook,
    _render_unit_rent_gap,
    _render_value_add_capex,
    _render_value_add_levers,
)


# ---------------------------------------------------------------------------
# Year-1 GPR + expenses derivation (T-12 if available, else defaults)
# ---------------------------------------------------------------------------

def _scalar(v: Any) -> float | None:
    """Unwrap a sources.json value to a float, or None if there isn't one.

    Entries are stored either bare (`1234`) or provenance-wrapped
    (`{"value": 1234, "source": "T12"}`), and nested groups like
    `t12_fixedCharges.realEstateTaxes` use the wrapped form too. Reading one
    without unwrapping put a dict into an arithmetic comparison and crashed
    the whole Underwriting tab, so every read goes through here.
    """
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _derive_year1_inputs(
    deal: DealState,
    sources: dict[str, Any] | None,
    units: int | None,
    *,
    city: str | None = None,
    pre_sale_tax: float | dict | None = None,
) -> tuple[float, float]:
    """Return (year1_gpr, year1_expenses) for the cash flow projection.

    If sources.json has T-12 data (totalRevenue + totalOpex), use those.
    Otherwise, derive from the deal's NOI + a class-based expense ratio.

    Post-sale adjustments (Beardsley):
      - tax_reassessment_on: full formula `(price × 85%) × mill_rate/100`
        when city + price known; fallback to flat +6% opex proxy.
      - insurance_escalator_on: +$50/unit/yr for agency debt. Default OFF.
    """
    if sources:
        rev = sources.get("totalRevenue")
        opex = sources.get("totalOpex")
        # Pull pre-sale tax from sources.json if available
        if pre_sale_tax is None:
            t12 = sources.get("t12_fixedCharges")
            if isinstance(t12, dict):
                pre_sale_tax = _scalar(t12.get("realEstateTaxes"))
        rev_v, opex_v = _scalar(rev), _scalar(opex)
        if rev_v and opex_v:
            return rev_v, _apply_expense_adjustments(
                opex_v, units, deal,
                city=city, purchase_price=deal.pp,
                pre_sale_tax=pre_sale_tax,
            )

    # Derive from NOI + expense ratio: NOI = (1 - vac) * GPR - expenses;
    # expenses = expense_ratio * GPR. Solve for GPR and expenses.
    # Using class C default 45% expense ratio (per config).
    er = config.EXPENSE_RATIOS.get("C", 0.45)
    vac = deal.vacancy_frac
    # NOI = GPR * (1 - vac) - GPR * er = GPR * (1 - vac - er)
    denom = (1.0 - vac - er)
    if denom <= 0:
        # Pathological inputs; fall back to NOI / 0.5 as gpr
        gpr = deal.noi / 0.5
    else:
        gpr = deal.noi / denom
    expenses = gpr * er
    return gpr, _apply_expense_adjustments(
        expenses, units, deal,
        city=city, purchase_price=deal.pp,
        pre_sale_tax=pre_sale_tax,
    )


def _apply_expense_adjustments(
    base_expenses: float,
    units: int | None,
    deal: DealState,
    *,
    city: str | None = None,
    purchase_price: float | None = None,
    pre_sale_tax: float | dict | None = None,
) -> float:
    """Apply post-sale tax reassessment + agency-debt insurance premium.

    Reassessment uses the FULL Beardsley formula when city + purchase price
    are known: new_tax = (purchase × 85%) × (mill_rate / 100). The DELTA
    over the seller's pre-sale tax is added to base expenses. Falls back
    to the conservative +6% opex proxy when inputs are missing.
    """
    adjusted = base_expenses
    # Callers should pass a number, but this runs against user-edited JSON —
    # normalize rather than trust, so a bad file degrades to the fallback
    # estimate instead of taking down the tab.
    pre_sale_tax = _scalar(pre_sale_tax)
    if deal.tax_reassessment_on:
        if city and purchase_price and purchase_price > 0:
            new_tax = estimated_post_sale_tax(purchase_price, city, DEFAULT_REASSESSMENT_RATIO)
            # If we know the seller's old tax line, add the DELTA. Otherwise
            # add the full new tax assuming the seller's tax was already
            # baked into base_expenses at a roughly 30%-of-opex share that
            # we can't extract — so fall back to delta-vs-implied-old-tax.
            if pre_sale_tax and pre_sale_tax > 0:
                delta = new_tax - pre_sale_tax
                adjusted += max(delta, 0.0)
            else:
                # Estimate seller's old tax as ~30% of base opex, add only the
                # difference. This is more accurate than the flat +6% proxy.
                implied_old_tax = base_expenses * 0.30
                delta = new_tax - implied_old_tax
                adjusted += max(delta, 0.0)
        else:
            # No city / price known — fall back to flat +6% proxy
            adjusted += base_expenses * 0.06
    if deal.insurance_escalator_on and units:
        adjusted += 50.0 * float(units)
    return adjusted


# ---------------------------------------------------------------------------
# Dial sliders (auto-save to deal.json on change)
# ---------------------------------------------------------------------------

def _render_macro_strip(deal: DealState) -> None:
    """Compact macro context band above the deal dials.

    Shows current 10Y Treasury and 30Y Mortgage from FRED, plus a 'Subject IR
    vs market' badge showing whether the user's interest rate is above or
    below the prevailing 30Y mortgage rate. Hides itself if ETL data isn't
    loaded.
    """
    if not is_etl_available():
        return
    c = config.COLORS
    macro = get_macro_indicators()
    if macro.empty:
        return

    # Pull current values from the dataframe
    series = {row["series_id"]: row for _, row in macro.iterrows()}
    ten_yr = series.get("DGS10")
    mortgage_30 = series.get("MORTGAGE30US")

    # Build the IR-vs-market badge — green if subject IR is at-or-below market,
    # red if subject IR is meaningfully above (cheaper-than-market debt = good).
    ir_badge_html = ""
    if mortgage_30 is not None:
        market_ir = float(mortgage_30["value"])
        diff = float(deal.ir) - market_ir
        if diff < -0.25:
            badge_color = c["gn"]
            badge_text = f"{abs(diff):.2f}% below 30Y mortgage"
        elif diff > 0.25:
            badge_color = c["yw"]
            badge_text = f"+{diff:.2f}% above 30Y mortgage"
        else:
            badge_color = c["tx2"]
            badge_text = "≈ at market"
        ir_badge_html = (
            f'<span style="color:{badge_color};font-size:11px;font-weight:600;'
            f'background:rgba(255,255,255,0.04);padding:2px 8px;border-radius:8px;'
            f'margin-left:6px">{badge_text}</span>'
        )

    # Three compact tiles: 10Y · 30Y Mortgage · Subject IR (with badge)
    col1, col2, col3 = st.columns(3)
    with col1:
        if ten_yr is not None:
            v = float(ten_yr["value"])
            date = str(ten_yr["date"])[:10]
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-radius:6px;padding:8px 12px">'
                f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
                f'letter-spacing:0.5px">10Y Treasury</div>'
                f'<div style="font-size:18px;font-weight:700;color:{c["tx"]};'
                f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:2px">'
                f'{v:.2f}%</div>'
                f'<div style="color:{c["tx3"]};font-size:10px">FRED · {date}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    with col2:
        if mortgage_30 is not None:
            v = float(mortgage_30["value"])
            date = str(mortgage_30["date"])[:10]
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-radius:6px;padding:8px 12px">'
                f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
                f'letter-spacing:0.5px">30Y Mortgage</div>'
                f'<div style="font-size:18px;font-weight:700;color:{c["tx"]};'
                f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:2px">'
                f'{v:.2f}%</div>'
                f'<div style="color:{c["tx3"]};font-size:10px">FRED · {date}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    with col3:
        st.markdown(
            f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
            f'border-left:3px solid {c["ac"]};border-radius:6px;padding:8px 12px">'
            f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.5px">Subject Interest Rate</div>'
            f'<div style="font-size:18px;font-weight:700;color:{c["ac2"]};'
            f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:2px">'
            f'{float(deal.ir):.2f}%{ir_badge_html}</div>'
            f'<div style="color:{c["tx3"]};font-size:10px">tune below ↓</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_dials(
    deal: DealState,
    folder: PropertyFolder | None,
    prop: dict[str, Any],
) -> DealState:
    """Render the deal dials. Returns a fresh DealState reflecting current slider values."""
    c = config.COLORS

    # Per-property widget-key prefix. Without this, Streamlit's session_state
    # persists the LAST property's dial values across property switches —
    # so when Brian opens Property B, he sees Property A's price/NOI bleeding
    # through. Including the property_id in every dial key forces Streamlit
    # to treat each property's dials as distinct widgets, re-initialized
    # from that property's deal.json on every render.
    pid = str(prop.get("property_id") or "").replace("-", "_") or "noid"

    # Market Calibration panel — moved to the BOTTOM of the Underwriting tab
    # per Brian 5/29 v2.0.14 (was here at the top of Deal Dials). Rendered
    # at the end of `render_underwriting` as its own section_card. Sliders
    # and macro strip stay here; reference data drops to the bottom.

    # Macro context band — current 10Y, 30Y mortgage, subject IR vs market.
    _render_macro_strip(deal)

    st.caption(
        "💡 Click any slider then use ← / → arrow keys for fine adjustment. "
        "For dollar fields below, type the exact figure or use ↑ / ↓ to step."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        # Purchase price + NOI: `st.number_input` instead of slider so Brian
        # can type exact figures like $3,050,000 directly. Streamlit's
        # number_input supports keyboard input, ↑/↓ arrows, and +/- spinner
        # buttons. Step size set tight enough to nudge by $1k if needed.
        pp = st.number_input(
            "Purchase price ($)",
            min_value=0,
            value=int(deal.pp),
            step=5_000,
            key=f"dial_pp_input_{pid}",
            help="Type the exact purchase price, or use ↑/↓ arrows / spinner to step by $5,000.",
        )
        st.caption(f"**${int(pp):,}**")

        # Surface T-12 NOI provenance next to the dial (per Brian 2026-05-08).
        # Reads `sources.json` if folder loaded — falls back gracefully when
        # the property has no T-12 data yet.
        t12_noi: float | None = None
        t12_source: str | None = None
        t12_period: str | None = None
        if folder is not None:
            from data.property_io import load_sources
            sources = load_sources(folder.path)
            if sources:
                noi_block = (
                    sources.get("t12_netOperatingIncome")
                    or sources.get("noi")
                )
                if isinstance(noi_block, dict):
                    raw_v = noi_block.get("value")
                    if raw_v is not None:
                        try:
                            t12_noi = float(raw_v)
                        except (TypeError, ValueError):
                            t12_noi = None
                    t12_source = (noi_block.get("source") or "T-12")
                # Period (e.g. "Apr 2025 - Mar 2026") if available
                period_block = sources.get("t12_period")
                if isinstance(period_block, dict):
                    t12_period = period_block.get("value")

        # Build label that shows T-12 NOI inline, per Brian's
        # "NOI ($X,XXX,XXX from T12)" format ask. Per follow-up 2026-05-08:
        # the label should explicitly read "Source: T-12 (period/year)" —
        # the redundant trailing "(T-12)" was confusing because we already
        # said "T-12" earlier in the same line.
        if t12_noi:
            period_str = f" ({t12_period})" if t12_period else ""
            noi_label = (
                f"NOI (${t12_noi:,.0f}/year) — Source: T-12{period_str}"
            )
        else:
            noi_label = "NOI ($/year)"

        noi_help = (
            "Annual NOI used by the cash-flow model. Type exact value or "
            "step by $1,000 with ↑/↓. "
        )
        if t12_noi:
            period_str = f" ({t12_period})" if t12_period else ""
            noi_help += (
                f"Latest T-12 reading: ${t12_noi:,.0f}{period_str}. "
                f"Set the dial to this value to underwrite at the actual "
                f"trailing-12 month performance."
            )

        noi = st.number_input(
            noi_label,
            min_value=0,
            value=int(deal.noi),
            step=1_000,
            key=f"dial_noi_input_{pid}",
            help=noi_help,
        )
        # Caption — confirm the dialed value AND show T-12 delta if relevant
        if t12_noi:
            delta = noi - t12_noi
            if abs(delta) < 1:
                caption = f"**${int(noi):,}**/year &nbsp;·&nbsp; matches T-12"
            elif delta > 0:
                caption = (
                    f"**${int(noi):,}**/year &nbsp;·&nbsp; "
                    f"<span style='color:{c['gn']}'>+${delta:,.0f} above T-12 "
                    f"${t12_noi:,.0f}</span>"
                )
            else:
                caption = (
                    f"**${int(noi):,}**/year &nbsp;·&nbsp; "
                    f"<span style='color:{c['rd']}'>−${abs(delta):,.0f} below "
                    f"T-12 ${t12_noi:,.0f}</span>"
                )
            st.markdown(
                f"<div style='font-size:13px;color:{c['tx2']};margin-top:-8px'>"
                f"{caption}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption(f"**${int(noi):,}**/year &nbsp;·&nbsp; *(no T-12 uploaded for this property)*")

        dp = st.slider(
            "Down payment %", 10.0, 50.0, float(deal.dp), step=0.5, key=f"dial_dp_{pid}",
        )
        ir = st.slider(
            "Interest rate %", 3.0, 12.0, float(deal.ir), step=0.05, key=f"dial_ir_{pid}",
        )
    with col2:
        # Vacancy with source badge
        vac_source = deal.vacancy_source
        badge_color = config.VACANCY_SOURCE_COLORS.get(vac_source, c["tx2"])
        st.markdown(
            f'Vacancy %  <span style="color:{badge_color};font-size:10px;'
            f'font-weight:600;border:1px solid {badge_color};padding:1px 6px;'
            f'border-radius:8px">{vac_source}</span>',
            unsafe_allow_html=True,
        )
        vac = st.slider(
            "Vacancy %", 0.0, 25.0, float(deal.vac), step=0.5,
            key=f"dial_vac_{pid}", label_visibility="collapsed",
        )
        rg = st.slider("Rent growth %", 0.0, 8.0, float(deal.rg), step=0.25, key=f"dial_rg_{pid}")
        eg = st.slider("Expense growth %", 0.0, 6.0, float(deal.eg), step=0.25, key=f"dial_eg_{pid}")
        xc = st.slider("Exit cap rate %", 4.0, 12.0, float(deal.xc), step=0.05, key=f"dial_xc_{pid}")
    with col3:
        hp = st.slider("Hold period (yrs)", 3, 10, int(deal.hp), key=f"dial_hp_{pid}")
        # Amortization input removed — it was a disabled "25 (locked)" field
        # that just cluttered the UI. The 25-yr amort term is now shown
        # inline under the dial row alongside the live P&I readout.
        io = st.slider("IO years", 0, 10, int(deal.io), key=f"dial_io_{pid}")
        amf = st.slider("AM fee % of GPR", 0.0, 5.0, float(deal.amf), step=0.25, key=f"dial_amf_{pid}")

    # ---- Live debt-service readout (spans 2 of 3 cols so it doesn't wrap) ----
    # Per Brian 2026-05-08: when this lived inside col1 it wrapped to multiple
    # lines because the column was too narrow for "$X,XXX,XXX/yr P&I (...) on
    # $X,XXX,XXX loan @ X.XX% · IO yrs 1–N: …". Pulled it out of the dial
    # 3-col layout and into a dedicated [2, 1] row so the whole sentence
    # reads on one line at typical viewport widths.
    from core.calc import amortizing_payment
    _live_loan = float(pp) * (1.0 - float(dp) / 100.0)
    _live_rate = float(ir) / 100.0
    _stabilized_pi = amortizing_payment(
        loan=_live_loan,
        annual_rate=_live_rate,
        amort_months=config.AMORT_MONTHS,
    )
    _io_payment = _live_loan * _live_rate  # interest-only annual cost
    if int(io) > 0:
        _io_str = (
            f" · IO yrs 1–{int(io)}: "
            f"${_io_payment:,.0f}/yr (${_io_payment/12:,.0f}/mo)"
        )
    else:
        _io_str = ""
    debt_col, _spacer = st.columns([2, 1])
    with debt_col:
        st.markdown(
            f'<div style="margin-top:8px;padding:10px 14px;background:{c["bg3"]};'
            f'border:1px solid {c["bdr"]};border-left:3px solid {c["ac"]};'
            f'border-radius:6px;font-size:13px;color:{c["tx"]};'
            f'line-height:1.5;white-space:nowrap;overflow-x:auto">'
            f'<span style="color:{c["tx2"]};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.6px;font-weight:600">Debt service · 25-yr amort</span>'
            f'<br/>'
            f'<b style="font-size:15px">${_stabilized_pi:,.0f}</b>/yr P&amp;I '
            f'<span style="color:{c["tx2"]}">'
            f'(${_stabilized_pi/12:,.0f}/mo) on '
            f'${_live_loan:,.0f} loan @ {ir:.2f}%'
            f'</span>'
            f'{_io_str}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # LP equity raise — defaults to down-payment dollars. Number input
    # lets Brian type exact figures (e.g. $1,250,000 if including reserves).
    default_raise = pp * (dp / 100.0)
    current_raise = int(deal.raise_amount) if deal.raise_amount else int(default_raise)
    raise_amount = st.number_input(
        f"LP equity raise — defaults to the down payment (${default_raise:,.0f})",
        min_value=0,
        value=current_raise,
        step=5_000,
        key=f"dial_raise_input_{pid}",
        help="Type exact LP raise amount, or step by $5,000 with ↑/↓.",
    )
    st.caption(f"**${int(raise_amount):,}**")

    # Post-sale expense adjustments (Beardsley) — surface as a small
    # toggle row so the analyst sees what's being applied to year-1 opex.
    st.markdown(
        f'<div style="margin-top:8px;color:{c["tx2"]};font-size:11px;'
        f'text-transform:uppercase;letter-spacing:0.7px;font-weight:600">'
        f'Post-sale expense adjustments</div>',
        unsafe_allow_html=True,
    )
    adj_col1, adj_col2 = st.columns(2)
    with adj_col1:
        tax_reassess = st.toggle(
            "Property tax reassessment on sale (+6% opex)",
            value=bool(deal.tax_reassessment_on),
            key=f"dial_tax_reassess_{pid}",
            help=(
                "Virginia reassesses property tax at sale. Most underwriters "
                "miss this until closing. Default ON. WHY: tax is ~30% of "
                "opex; reassessment typically bumps it 15-25%. We model +20% "
                "on the line = +6% on total opex (conservative)."
            ),
        )
    with adj_col2:
        ins_esc = st.toggle(
            "Agency debt insurance premium (+$50/unit/yr)",
            value=bool(deal.insurance_escalator_on),
            key=f"dial_ins_esc_{pid}",
            help=(
                "Fannie/Freddie loans require higher insurance coverage than "
                "local bank/life-co debt. WHY: typically +$50/unit/yr — a "
                "$5K hidden premium on a 100-unit deal. Toggle ON only if "
                "you're closing with agency debt."
            ),
        )

    # B3 + B4: reposition disruption sliders (Beardsley)
    st.markdown(
        f'<div style="margin-top:8px;color:{c["tx2"]};font-size:11px;'
        f'text-transform:uppercase;letter-spacing:0.7px;font-weight:600">'
        f'Reposition Disruption (Year-1 ramp)</div>',
        unsafe_allow_html=True,
    )
    rep_col1, rep_col2 = st.columns(2)
    with rep_col1:
        vac_spike = st.slider(
            "Going-in vacancy spike (pp added in first months)",
            min_value=0.0, max_value=25.0,
            value=float(deal.vac_spike_pp), step=0.5,
            key=f"dial_vac_spike_{pid}",
            help=(
                "Beardsley convention: NTVs, evictions, skips, and refreshed "
                "screening cause a 5-15pp occupancy drop in the first 3-6 "
                "months post-close. Default 10pp on top of the dialed "
                "vacancy. Set to 0 if you assume no reposition."
            ),
        )
    with rep_col2:
        stab_months = st.slider(
            "Stabilization timeline (months back to base vac)",
            min_value=0, max_value=27,
            value=int(deal.stabilization_months), step=1,
            key=f"dial_stab_months_{pid}",
            help=(
                "How long does the property take to ramp from the vacancy "
                "spike back to the dialed vacancy rate? Linear ramp. "
                "Beardsley uses 12-27 mo. Cosmetic value-add: 12 mo. "
                "Heavy reposition (interior reno every unit): 24-27 mo."
            ),
        )

    # Detect changes; mark vacancy_source = 'user' if user moved the vac slider
    new_vacancy_source = deal.vacancy_source
    if abs(vac - deal.vac) > 0.001:
        new_vacancy_source = "user"

    # Build the candidate from a COPY of the loaded deal, updating only the
    # dial fields. This is load-bearing: DealState also carries non-dial fields
    # the widgets don't touch — `selected_levers`, and the FR-9.3.1 concurrency
    # metadata `row_version` / `updated_by` / `updated_at`. Rebuilding via
    # `model_validate({...dials...})` reset those to defaults (row_version=0,
    # updated_at=None, selected_levers=[]), so a once-saved deal (non-zero
    # row_version, a timestamp) was NEVER equal to `new_deal` — the `!=` below
    # fired on every render, auto-saving and `st.rerun()`-ing forever. That is
    # the "Underwriting fades in/out on its own, Photo Upload never clears"
    # loop (owner, 2026-08-04): the page never reached a stable run. Copying
    # preserves those fields, so `!=` now reflects only real dial edits — and
    # it stops silently wiping `selected_levers` on every rerun.
    # Amortization stays locked at 25 yrs (config.AMORT_YEARS); the dial was
    # removed in v0.71 but the field is still written so old deals round-trip.
    new_deal = deal.model_copy(update={
        "pp": float(pp), "noi": float(noi), "dp": float(dp), "ir": float(ir),
        "vac": float(vac), "rg": float(rg), "eg": float(eg), "xc": float(xc),
        "hp": int(hp), "am": int(config.AMORT_YEARS), "io": int(io),
        "amf": float(amf),
        "raise_amount": raise_amount if raise_amount != int(default_raise) else None,
        "vacancy_source": new_vacancy_source,
        "tax_reassessment_on": bool(tax_reassess),
        "insurance_escalator_on": bool(ins_esc),
        "vac_spike_pp": float(vac_spike),
        "stabilization_months": int(stab_months),
    })

    # Auto-save on any slider change. Creates the property folder on the
    # first save if one doesn't exist yet — that's how a "no folder yet"
    # property becomes a real deal.
    if new_deal != deal:
        created_folder = False
        if folder is None:
            folder = ensure_property_folder(prop)
            created_folder = True
            st.success(f"📁 Created folder `{folder.folder_name}`")
        # FR-9.3.1: save against the version this session loaded, so a
        # co-worker's save between our load and our write is caught instead
        # of overwritten. `deal` is the state we rendered the dials from.
        res = save_deal(folder.path, new_deal,
                        expected_version=deal.row_version,
                        actor=_current_actor())
        if not res.ok:
            _render_save_conflict(st, folder, new_deal, res)
            return deal
        st.caption("✓ saved")
        # Only a BRAND-NEW folder needs a full rerun (so the sidebar and other
        # tabs discover it via discover_property_folders()). An ordinary dial
        # edit does NOT: the slider interaction already triggered this rerun,
        # and the metrics below recompute from `new_deal` in this same run.
        # Rerunning on every save is what turned a stale-equality bug into an
        # infinite fade loop — keep the rerun scoped to folder creation.
        if created_folder:
            st.rerun()

    return new_deal



# ---------------------------------------------------------------------------
# FR-9.3.2 — save-conflict resolution
# ---------------------------------------------------------------------------

def _current_actor() -> str:
    """Display name for the save stamp; falls back to the OS user locally."""
    try:
        from core import auth
        u = auth.current_user()
        if u is not None:
            return u.display_name
    except Exception:
        pass
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def render_presence_banner(st, prop) -> None:
    """FR-9.3.3: say who else has this deal open, before anyone loses work.

    Degrades to silence when Postgres is not configured (single-user desktop),
    so the local path is unaffected. Presence is advisory only — it never
    blocks an edit; FR-9.3.1 is the thing that actually protects the write.
    """
    pid = str((prop or {}).get("property_id") or "")
    if not pid:
        return
    try:
        from core import auth
        from data.concurrency import acquire_or_refresh_lock
        user = auth.current_user()
        org_id = getattr(user, "org_id", None)
        if user is None or not org_id:
            return
        lock = acquire_or_refresh_lock(org_id, "deal", pid, str(user.oid))
    except Exception:
        # No Postgres / no org context — presence is a nicety, not a gate.
        return
    if lock.held and not lock.mine:
        st.info(
            f"👥 Someone else has this deal open (since {lock.since:%H:%M}). "
            "You can still edit — you'll be told before anything is overwritten."
        )


def _render_save_conflict(st, folder, mine, res) -> None:
    """Show what collided and let the analyst pick — never auto-resolve.

    Auto-merging dial state would silently invent a deal neither person
    underwrote, so both versions are shown and the choice is explicit.
    """
    who = res.conflict_by or "someone else"
    when = f" at {res.conflict_at}" if res.conflict_at else ""
    st.error(
        f"⚠️ **{who}** saved this deal{when} while you were editing. "
        "Your change was **not** written — nothing of theirs was lost."
    )
    theirs = res.their_deal
    if theirs is not None:
        c1, c2 = st.columns(2)
        with c1:
            st.caption(f"**Theirs (v{res.version}, on disk now)**")
            st.write({"Purchase price": f"${theirs.pp:,.0f}",
                      "NOI": f"${theirs.noi:,.0f}",
                      "Down payment": f"{theirs.dp:.0f}%",
                      "Exit cap": f"{theirs.xc:.2f}%"})
        with c2:
            st.caption("**Yours (unsaved)**")
            st.write({"Purchase price": f"${mine.pp:,.0f}",
                      "NOI": f"${mine.noi:,.0f}",
                      "Down payment": f"{mine.dp:.0f}%",
                      "Exit cap": f"{mine.xc:.2f}%"})

    b1, b2 = st.columns(2)
    with b1:
        if st.button("↻ Discard mine, load theirs", use_container_width=True,
                     key="conflict_take_theirs"):
            st.rerun()
    with b2:
        if st.button("⤴ Overwrite with mine", type="primary",
                     use_container_width=True, key="conflict_take_mine"):
            # Re-stamp against what is on disk NOW — a deliberate, logged
            # override rather than a blind write.
            save_deal(folder.path, mine, expected_version=res.version,
                      actor=_current_actor())
            st.rerun()

# ---------------------------------------------------------------------------
# Live metrics tile row
# ---------------------------------------------------------------------------

def _kpi_tile(
    label: str,
    value: str,
    *,
    why: str,
    target: str = "",
    accent: str | None = None,
    value_color: str | None = None,
    big: bool = False,
) -> str:
    """Build one KPI tile with explicit large-font value + 'why' tooltip.

    Custom HTML beats `st.metric()` here for two reasons:
      1. We need to lock the value font-size (the metric clamp shrinks
         numbers to ~15px in narrow columns and Brian can't read them).
      2. We want a visible ⓘ glyph that hovers to reveal the formula and
         WHY this metric matters — Brian explicitly asked for the "why"
         next to every number.

    The `why` text shows in a native browser title-tooltip (works
    everywhere, no JS dependency).
    """
    c = config.COLORS
    val_color = value_color or c["tx"]
    border_left = f"border-left:3px solid {accent};" if accent else ""
    val_size = "32px" if big else "24px"
    target_html = (
        f'<div style="color:{c["tx3"]};font-size:10px;margin-top:2px">{target}</div>'
        if target else ""
    )
    return (
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'{border_left}border-radius:6px;padding:10px 14px;'
        f'min-height:{"110px" if big else "92px"};'
        f'display:flex;flex-direction:column;justify-content:space-between"'
        f' title="{why}">'
        f'<div style="display:flex;align-items:baseline;justify-content:space-between">'
        f'<span style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600">{label}</span>'
        f'<span style="color:{c["tx3"]};font-size:11px;cursor:help">ⓘ</span>'
        f'</div>'
        f'<div style="font-size:{val_size};font-weight:700;color:{val_color};'
        f'line-height:1.05;margin-top:4px;font-variant-numeric:tabular-nums">'
        f'{value}</div>'
        f'{target_html}</div>'
    )


def _render_metrics(
    deal: DealState,
    sources: dict[str, Any] | None,
    units: int | None,
    city: str | None = None,
    *,
    render: bool = True,
) -> dict[str, float]:
    """Compute Year-1 KPI metrics; optionally render the KPI grid.

    Per Brian 5/29 v2.0.18 — Year-1 KPIs MOVED from the Underwriting tab to
    the top of the Returns tab. The Underwriting tab still needs the
    computed metrics dict for downstream sections (sensitivity, verdict,
    refi-exit test), so it calls this with ``render=False`` to compute
    without painting. The Returns tab calls with default ``render=True``.

    Layout (when render=True):
      Row 1 — HEADLINE (big tiles, 32px values): Cap Rate · Project IRR ·
              Untrended Return on Cost · Equity Multiple
      Row 2 — Returns & risk: Y1 CoC · DSCR · Debt Yield · Breakeven Occ
      Row 3 — Sizing & leverage: Price/Unit · Loan · Equity Raise · Cap-vs-Constant

    Every tile carries a `title=` tooltip that hovers to reveal the formula
    and WHY this metric matters (Brian's "list the why" ask).
    """
    c = config.COLORS

    # ---- Build full cash flow projection so we can compute IRR + EM + stabilized NOI ----
    gpr, expenses = _derive_year1_inputs(deal, sources, units, city=city)
    debt_terms = DebtTerms(
        loan_amount=deal.loan_amount,
        annual_rate=deal.interest_rate,
        amort_months=config.AMORT_MONTHS,
        io_years=deal.io,
    )
    debt_sched = build_debt_schedule(debt_terms, deal.hp)
    # Apply B3+B4: year-1 vacancy spike + stabilization ramp.
    # year1_eff_vac = base + spike-weighted-by-stabilization-period.
    year1_eff_vac = effective_year1_vacancy(
        base_vac=deal.vacancy_frac,
        spike_pp=deal.vac_spike_pp / 100.0,
        stabilization_months=deal.stabilization_months,
    )
    cf = build_cashflow(
        year1_gpr=gpr,
        year1_vacancy_pct=year1_eff_vac,
        year1_expenses=expenses,
        rent_growth=deal.rent_growth,
        expense_growth=deal.expense_growth,
        am_fee_pct=deal.am_fee_pct,
        debt=debt_sched,
        hold_years=deal.hp,
        exit_cap=deal.exit_cap,
        equity_raise=deal.equity_raise,
        stabilized_vacancy_pct=deal.vacancy_frac,
        # If stabilization completes within year 1, year-2+ uses stabilized vac.
        stabilization_year_break=1 if deal.stabilization_months <= 12 else 2,
    )

    # Headline metrics
    cap = cap_rate(deal.noi, deal.pp)
    stabilized_noi = max(r.noi for r in cf.rows) if cf.rows else deal.noi
    roc = return_on_cost(stabilized_noi, deal.pp)  # Eight Rock: basis = pp until capex tracking added
    irr_v = project_irr(
        equity_raise=deal.equity_raise,
        annual_cashflows=[r.cash_flow for r in cf.rows],
        exit_proceeds_net=cf.exit_proceeds_net,
    )
    em = cf.equity_multiple

    # Risk + return metrics
    ads_y1 = debt_sched.annual_payment[0]
    am_fee_y1 = cf.rows[0].am_fee if cf.rows else 0.0
    noi_after_am_y1 = deal.noi - am_fee_y1
    dscr_v = dscr(noi_after_am_y1, ads_y1)
    cf_y1 = noi_after_am_y1 - ads_y1
    coc = cash_on_cash(cf_y1, deal.equity_raise)
    dy = debt_yield(deal.noi, deal.loan_amount)
    egi_y1 = cf.rows[0].egi if cf.rows else 0.0
    breakeven = breakeven_occupancy(expenses, ads_y1, gpr) if gpr > 0 else 0.0

    # Sizing
    ppu = deal.pp / units if units else 0.0
    debt_const = amortized_debt_constant(deal.interest_rate, config.AMORT_MONTHS)
    cap_const_spread = cap - debt_const  # positive = positive leverage post-IO

    # Color thresholds — green if hits target, red if below NO-GO bar, else gold
    def _color_for(value: float, go: float, no_go: float, higher_is_better: bool = True) -> str:
        if higher_is_better:
            if value >= go: return c["gn"]
            if value < no_go: return c["rd"]
            return c["yw"]
        else:
            if value <= go: return c["gn"]
            if value > no_go: return c["rd"]
            return c["yw"]

    cap_color = _color_for(cap, config.GO_CAP, config.NOGO_CAP)
    irr_color = (
        _color_for(irr_v or 0, config.PROJECT_IRR_TARGET, 0.10)
        if irr_v is not None else c["tx3"]
    )
    roc_color = _color_for(roc, 0.08, 0.06)
    em_color = _color_for(em, 1.8, 1.3)
    dscr_color = _color_for(dscr_v, config.GO_DSCR, 1.10)
    coc_color = _color_for(coc, config.GO_COC, config.WATCH_COC)
    dy_color = _color_for(dy, 0.07, 0.06)
    breakeven_color = _color_for(breakeven, 0.80, 0.90, higher_is_better=False)
    cap_const_color = _color_for(cap_const_spread, 0.005, 0.0)

    # ---- Rendering — skipped entirely when called from the Underwriting
    # ---- tab (which only needs the metrics dict at the bottom). ----------
    if not render:
        # Recompute the few values the return dict needs that are normally
        # computed during rendering (er for expense_ratio). Then short-circuit.
        er = expense_ratio(expenses, egi_y1)
        return {
            "cap": cap,
            "dscr": dscr_v,
            "coc": coc,
            "ppu": ppu,
            "ads": ads_y1,
            "irr": irr_v or 0.0,
            "em": em,
            "roc": roc,
            "debt_yield": dy,
            "breakeven": breakeven,
            "stabilized_noi": stabilized_noi,
            "expense_ratio": er,
            "exit_loan_payoff": cf.exit_loan_payoff,
            "exit_noi": cf.exit_noi,
        }

    # ---- Row 1: HEADLINE big tiles ----
    st.markdown("###### Headline KPIs (Eight Rock GO bars: Cap ≥ 7.5% · DSCR ≥ 1.30x · CoC ≥ 6.0% · IRR ≥ 18% · EM ≥ 1.8x)")
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    with r1c1:
        st.markdown(_kpi_tile(
            "Cap Rate (Going-In)",
            f"{cap*100:.2f}%",
            why=(
                "Going-in Cap Rate = T-12 NOI ÷ Purchase Price. "
                "WHY: the headline yield indicator — what you earn before "
                "any growth or value-add. Eight Rock GO ≥ 7.5%, NO-GO < 6.85%."
            ),
            target=f"GO ≥ {config.GO_CAP*100:.1f}%",
            accent=cap_color,
            value_color=cap_color,
            big=True,
        ), unsafe_allow_html=True)
    with r1c2:
        irr_str = f"{irr_v*100:.2f}%" if irr_v is not None else "—"
        st.markdown(_kpi_tile(
            "Project IRR",
            irr_str,
            why=(
                "Project (gross) IRR — internal rate of return on the deal "
                "before the GP promote. Cash flow vector: [-equity, "
                "yr1_CF, yr2_CF, ..., yrN_CF + exit_proceeds_net]. "
                "WHY: industry-standard total-return measure. "
                "Eight Rock target ≥ 18%; LP IRR target ≥ 15% after waterfall."
            ),
            target=f"Target ≥ {config.PROJECT_IRR_TARGET*100:.0f}%",
            accent=irr_color,
            value_color=irr_color,
            big=True,
        ), unsafe_allow_html=True)
    with r1c3:
        st.markdown(_kpi_tile(
            "Untrended Return on Cost",
            f"{roc*100:.2f}%",
            why=(
                "Stabilized NOI ÷ All-in Basis (purchase + capex + closing). "
                "WHY: Beardsley's #1 metric — NOT manipulable by growth/exit "
                "assumptions. Most robust quality-of-deal indicator. "
                "Target ≥ 8% (= 150–250 bps over market cap)."
            ),
            target="Target ≥ 8.0%",
            accent=roc_color,
            value_color=roc_color,
            big=True,
        ), unsafe_allow_html=True)
    with r1c4:
        st.markdown(_kpi_tile(
            "Equity Multiple",
            f"{em:.2f}x",
            why=(
                "Total cash distributions ÷ Total equity invested. "
                "WHY: how many times your money multiplies over the hold. "
                "Pairs with IRR — IRR can be high with weak EM (short hold) "
                "or low with strong EM (long hold). Eight Rock target ≥ 1.8x."
            ),
            target=f"Target ≥ {config.LP_EQUITY_MULTIPLE_TARGET}x",
            accent=em_color,
            value_color=em_color,
            big=True,
        ), unsafe_allow_html=True)

    # ---- Row 2: Returns & Risk ----
    st.markdown('<div style="margin-top:6px"></div>', unsafe_allow_html=True)
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    with r2c1:
        st.markdown(_kpi_tile(
            "Y1 Cash on Cash",
            f"{coc*100:.2f}%",
            why=(
                "Year-1 cash flow ÷ LP equity raise. "
                "WHY: the LP's first-year yield — the 'how much income do "
                "I get on my $X invested' number. Eight Rock GO ≥ 6.0%."
            ),
            target=f"GO ≥ {config.GO_COC*100:.1f}%",
            accent=coc_color,
        ), unsafe_allow_html=True)
    with r2c2:
        st.markdown(_kpi_tile(
            "DSCR (Y1)",
            f"{dscr_v:.2f}x",
            why=(
                "Debt Service Coverage Ratio = (NOI – AM fee) ÷ Annual Debt "
                "Service. WHY: lender's primary credit test. Below 1.20x = "
                "the loan won't fund. Eight Rock GO ≥ 1.30x; Norfolk "
                "overlay ≥ 1.25x; stress 1.10x. Watch the post-IO drop."
            ),
            target=f"GO ≥ {config.GO_DSCR}x",
            accent=dscr_color,
        ), unsafe_allow_html=True)
    with r2c3:
        st.markdown(_kpi_tile(
            "Debt Yield",
            f"{dy*100:.2f}%",
            why=(
                "NOI ÷ Loan Amount. WHY: lender-side metric independent "
                "of valuation. Below 7% = effectively over-leveraged "
                "regardless of DSCR (cap compression evaporates LTV "
                "cushion). Lender minimum 7%."
            ),
            target="Lender min ≥ 7.0%",
            accent=dy_color,
        ), unsafe_allow_html=True)
    with r2c4:
        st.markdown(_kpi_tile(
            "Breakeven Occupancy",
            f"{breakeven*100:.1f}%",
            why=(
                "(Operating expenses + Debt service) ÷ Total potential rent. "
                "WHY: the minimum occupancy needed to cover all costs. "
                "Below 80% = comfortable buffer; above 90% = thin deal "
                "with little room for vacancy spikes."
            ),
            target="Target ≤ 80%",
            accent=breakeven_color,
        ), unsafe_allow_html=True)

    # ---- Row 3: Sizing & Leverage ----
    st.markdown('<div style="margin-top:6px"></div>', unsafe_allow_html=True)
    r3c1, r3c2, r3c3, r3c4 = st.columns(4)
    with r3c1:
        st.markdown(_kpi_tile(
            "Price / Unit",
            f"${ppu:,.0f}" if ppu else "—",
            why=(
                "Purchase price ÷ unit count. WHY: quick comp benchmark. "
                "Hampton Roads Class C value-add typically lands $80K–"
                "$150K/unit depending on vintage and condition. Compare "
                "to record comps in your Performance & Market tab."
            ),
        ), unsafe_allow_html=True)
    with r3c2:
        st.markdown(_kpi_tile(
            "Loan Amount",
            f"${deal.loan_amount:,.0f}",
            why=(
                f"Purchase price × ({deal.dp:.1f}% down implies "
                f"{(100-deal.dp):.1f}% LTV). WHY: senior debt sizing. "
                "Real loan max is the lowest of (DSCR, LTV, debt yield) "
                "tests — see those tiles for which is binding."
            ),
            target=f"{(100-deal.dp):.1f}% LTV",
        ), unsafe_allow_html=True)
    with r3c3:
        st.markdown(_kpi_tile(
            "LP Equity Raise",
            f"${deal.equity_raise:,.0f}",
            why=(
                "Cash from LPs (Eight Rock convention: 100% from LPs, no "
                "GP co-invest). Default = down payment dollars; bump up "
                "for closing costs, capex, working capital. Per LP raise "
                "rule in feedback_underwriting_conventions.md."
            ),
        ), unsafe_allow_html=True)
    with r3c4:
        spread_str = f"{cap_const_spread*100:+.2f}pp"
        st.markdown(_kpi_tile(
            "Cap − Debt Constant",
            spread_str,
            why=(
                f"Going-in Cap ({cap*100:.2f}%) − Amortizing Debt Constant "
                f"({debt_const*100:.2f}%). WHY: must be POSITIVE for "
                "positive leverage after the IO period burns off. "
                "Negative spread = the loan eats more cash than the "
                "asset produces post-amortization."
            ),
            target="Must be > 0",
            accent=cap_const_color,
            value_color=cap_const_color,
        ), unsafe_allow_html=True)

    # ---- Sanity flags strip ----
    er = expense_ratio(expenses, egi_y1)
    flags = []
    if er and er < 0.40:
        flags.append((
            c["rd"],
            f"⚠️  Expense ratio {er*100:.1f}% is below 40% — seller may be "
            f"under-reporting expenses. Investigate aggressively (request "
            f"actual T-12, AR aging, operating statements)."
        ))
    elif er and er > 0.60:
        flags.append((
            c["yw"],
            f"⚠️  Expense ratio {er*100:.1f}% is above 60% — vintage burn "
            f"OR opportunity to compress with better management."
        ))
    if cap_const_spread < 0:
        flags.append((
            c["rd"],
            f"⚠️  Negative leverage: cap rate {cap*100:.2f}% < debt constant "
            f"{debt_const*100:.2f}%. The loan eats more cash than the asset "
            f"yields post-IO. Either lower price, stretch amort, or pass."
        ))
    if dscr_v and dscr_v < 1.10:
        flags.append((
            c["rd"],
            f"⚠️  DSCR {dscr_v:.2f}x is below 1.10 stress floor — lender "
            f"will not fund this loan size at this NOI."
        ))
    if dy and dy < 0.06:
        flags.append((
            c["rd"],
            f"⚠️  Debt Yield {dy*100:.2f}% is below 6% — over-leveraged "
            f"even by aggressive lender standards."
        ))

    # Brian 5/29 v2.0.30 — sanity flags RENDERING moved to the bottom of
    # the Year-1 KPIs section (was here, between Row 3 and NOI Trend).
    # Computation stays here so `er` is available for the return dict.

    # ---- NOI trend strip: T-12 vs T-3 vs Year-1 Forecast vs Stabilized ----
    # WHY: sophisticated underwriting compares trailing actuals (12 month
    # AND 3 month annualized run rate) to what the forecast says year-1 and
    # stabilized will be. T-3 catches NEAR-TERM deceleration that gets
    # masked by the smoother T-12. Big jumps between T-3 and T-12 are red
    # flags (vacancy spike, eviction wave, mgmt change in last quarter).
    st.markdown('<div style="margin-top:14px"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600;margin-bottom:6px">'
        f'NOI Trend (T-12 → T-3 run rate → forecast → stabilized)</div>',
        unsafe_allow_html=True,
    )
    y1_noi = cf.rows[0].noi if cf.rows else 0.0
    # Pull source provenance for the T-12 NOI from sources.json if available
    t12_source = "User input"
    t12_color = c["src_user"]
    if sources:
        noi_src = sources.get("noi") or sources.get("t12_netOperatingIncome")
        if isinstance(noi_src, dict):
            t12_source = noi_src.get("source") or "T-12"
            if "T-12" in t12_source:
                t12_color = c["src_t12"]
            elif "Rent Roll" in t12_source:
                t12_color = c["src_rr"]

    # T-3 NOI — annualize the rent roll's current monthly run rate.
    # Convention: T-3 NOI ≈ (totalActualRent + ancillary_estimate) × 12
    # − (T-12 expense ÷ 12 × 12) ≈ rent roll income annualized, T-12 expenses
    # held constant. Big T-3 vs T-12 gap = recent occupancy/rent shift.
    t3_noi: float | None = None
    if sources and isinstance(sources.get("rentRoll"), dict):
        rr_summary = (sources["rentRoll"].get("summary") or {})
        monthly_actual = rr_summary.get("totalActualRent")
        if monthly_actual:
            # Annualized rental income from current rent roll
            t3_revenue = float(monthly_actual) * 12
            # Use T-12 ancillary income from t12_income if available
            t12_income = sources.get("t12_income") or {}
            other_income = 0.0
            if isinstance(t12_income, dict) and isinstance(t12_income.get("otherIncome"), dict):
                other_income = float(
                    t12_income["otherIncome"].get("totalOtherIncome") or 0
                )
            t3_revenue_total = t3_revenue + other_income
            # Use T-12 opex (ETL or sources)
            t12_opex = 0.0
            opex_src = sources.get("totalOpex") or {}
            if isinstance(opex_src, dict) and opex_src.get("value"):
                t12_opex = float(opex_src["value"])
            t12_fixed = sources.get("fixedCharges") or {}
            if isinstance(t12_fixed, dict) and t12_fixed.get("value"):
                t12_opex += float(t12_fixed["value"])
            t3_noi = t3_revenue_total - t12_opex

    y1_delta = ((y1_noi - deal.noi) / deal.noi * 100) if deal.noi else 0.0
    stab_delta = ((stabilized_noi - deal.noi) / deal.noi * 100) if deal.noi else 0.0
    t3_delta = ((t3_noi - deal.noi) / deal.noi * 100) if (t3_noi and deal.noi) else None

    nc1, nc2, nc3, nc4 = st.columns(4)
    with nc1:
        st.markdown(_kpi_tile(
            f"T-12 NOI ({t12_source})",
            f"${deal.noi:,.0f}",
            why=(
                "Trailing-12-month NOI — the actual operating performance "
                "for the past year. WHY: BASELINE every forecast is measured "
                "against. If forecast yr-1 differs materially, document why."
            ),
            target="Baseline",
            accent=t12_color,
        ), unsafe_allow_html=True)
    with nc2:
        if t3_noi is not None:
            t3_color = c["gn"] if t3_delta and t3_delta >= 0 else c["rd"]
            t3_target = f"{t3_delta:+.1f}% vs T-12" if t3_delta is not None else ""
            st.markdown(_kpi_tile(
                "T-3 Run-Rate NOI",
                f"${t3_noi:,.0f}",
                why=(
                    f"Current rent-roll annualized: total monthly actual rent "
                    f"× 12 + T-12 ancillary − T-12 opex. WHY: catches NEAR-"
                    f"TERM trend (last 3 months) that the smoother T-12 hides. "
                    f"Big T-3 vs T-12 gap = recent occupancy or rent change. "
                    f"{(t3_delta or 0):+.1f}% vs T-12."
                ),
                target=t3_target,
                accent=t3_color,
            ), unsafe_allow_html=True)
        else:
            st.markdown(_kpi_tile(
                "T-3 Run-Rate NOI",
                "—",
                why=(
                    "T-3 NOI requires rent-roll data with `totalActualRent` "
                    "in the property's sources.json. Upload a current rent "
                    "roll to populate this tile."
                ),
                target="No rent roll loaded",
                accent=c["src_unknown"],
            ), unsafe_allow_html=True)
    with nc3:
        delta_str = f"{y1_delta:+.1f}% vs T-12"
        delta_color = c["gn"] if y1_delta >= 0 else c["rd"]
        st.markdown(_kpi_tile(
            "Year-1 Forecast NOI",
            f"${y1_noi:,.0f}",
            why=(
                f"Year-1 NOI from the 5-yr CF projection (after vacancy, "
                f"opex, post-sale tax/insurance). WHY: what the property "
                f"earns in YEAR ONE after close. {y1_delta:+.1f}% vs T-12."
            ),
            target=delta_str,
            accent=delta_color,
        ), unsafe_allow_html=True)
    with nc4:
        stab_delta_str = f"{stab_delta:+.1f}% vs T-12"
        stab_delta_color = c["gn"] if stab_delta >= 0 else c["rd"]
        st.markdown(_kpi_tile(
            "Stabilized NOI (peak yr)",
            f"${stabilized_noi:,.0f}",
            why=(
                f"Highest NOI year in the 5-yr forecast — value-add plan "
                f"realized. WHY: feeds the Untrended Return on Cost above. "
                f"{stab_delta:+.1f}% above T-12 = projected upside."
            ),
            target=stab_delta_str,
            accent=stab_delta_color,
        ), unsafe_allow_html=True)

    # ---- Sanity flag callouts (moved here per Brian 5/29 v2.0.30) ----
    # Was rendered between Row 3 and NOI Trend; now sits at the BOTTOM of
    # the Year-1 KPIs section so the analyst reads the data first and the
    # warnings last (the "what to investigate" panel).
    if flags:
        st.markdown('<div style="margin-top:14px"></div>', unsafe_allow_html=True)
        for color, msg in flags:
            st.markdown(
                f'<div style="background:rgba(239,68,68,0.08);'
                f'border-left:3px solid {color};'
                f'padding:8px 12px;border-radius:4px;color:{c["tx"]};'
                f'font-size:13px;margin-bottom:4px">{msg}</div>',
                unsafe_allow_html=True,
            )

    return {
        "cap": cap,
        "dscr": dscr_v,
        "coc": coc,
        "ppu": ppu,
        "ads": ads_y1,
        "irr": irr_v or 0.0,
        "em": em,
        "roc": roc,
        "debt_yield": dy,
        "breakeven": breakeven,
        "stabilized_noi": stabilized_noi,
        "expense_ratio": er,
        "exit_loan_payoff": cf.exit_loan_payoff,
        "exit_noi": cf.exit_noi,
    }


# ---------------------------------------------------------------------------
# Refi / Exit Stress Test (Beardsley's 4-scenario panel)
# ---------------------------------------------------------------------------

def _render_refi_exit_test(
    deal: DealState,
    metrics: dict[str, float],
) -> None:
    """Beardsley's single most-important risk metric: can the deal absorb
    a refi/exit at 4 stress points without becoming a forced sale?

    Each scenario simulates a hypothetical lender re-underwriting the deal
    at exit/refi, applies the 3-test max loan (DSCR/LTV/Debt-Yield), and
    PASS/FAIL based on whether the new loan covers the existing balance.
    """
    c = config.COLORS

    if metrics.get("exit_loan_payoff") is None or metrics.get("exit_noi") is None:
        return

    # Refi at exit-year market rate (we use the user's IR + a small premium
    # as a base assumption — the test stresses upward from there)
    base_refi_rate = max(deal.interest_rate, 0.06)  # min 6% as floor

    results = run_refi_exit_test(
        base_noi_at_exit=metrics["exit_noi"],
        base_exit_cap=deal.exit_cap,
        base_interest_rate=base_refi_rate,
        amort_months=config.AMORT_MONTHS,
        existing_loan_balance=metrics["exit_loan_payoff"],
    )

    st.caption(
        f"At Year {deal.hp} exit, can a stressed lender refi out the existing "
        f"${metrics['exit_loan_payoff']:,.0f} balance? Failure = forced sale "
        f"at the wrong time. WHY this matters: 2020-22 vintage value-add "
        f"operators are getting crushed RIGHT NOW because they didn't run "
        f"this test (Matrix Feb 2026)."
    )

    cols = st.columns(4)
    scenario_order = ["Base", "Op Shock", "Capital Markets", "Both"]
    for col, name in zip(cols, scenario_order):
        result = results[name]
        scen = result.scenario
        passes = result.passes
        color = c["gn"] if passes else c["rd"]
        verdict = "✓ PASS" if passes else "✗ FAIL"
        cushion_str = (
            f"+${result.cushion:,.0f} cushion ({result.cushion_pct*100:+.0f}%)"
            if passes
            else f"-${abs(result.cushion):,.0f} short ({result.cushion_pct*100:+.0f}%)"
        )
        max_loan_str = f"${scen.max_loan:,.0f}"
        # Build a card per scenario
        with col:
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:4px solid {color};border-radius:6px;'
                f'padding:10px 14px;min-height:170px"'
                f' title="{scen.description}">'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
                f'<span style="color:{c["tx"]};font-size:13px;font-weight:600">{name}</span>'
                f'<span style="color:{color};font-size:13px;font-weight:700">{verdict}</span>'
                f'</div>'
                f'<div style="color:{c["tx3"]};font-size:10px;margin-top:2px">{scen.description}</div>'
                # NOI + Cap + Rate stats
                f'<div style="margin-top:8px;font-size:11px;color:{c["tx2"]};line-height:1.6">'
                f'<div>NOI: <b style="color:{c["tx"]}">${scen.noi_at_exit:,.0f}</b></div>'
                f'<div>Exit cap: <b style="color:{c["tx"]}">{scen.cap_rate*100:.2f}%</b></div>'
                f'<div>Refi rate: <b style="color:{c["tx"]}">{scen.interest_rate*100:.2f}%</b></div>'
                f'</div>'
                f'<div style="margin-top:8px;padding-top:6px;border-top:1px solid {c["bdr"]}">'
                f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
                f'letter-spacing:0.5px">Max Refi Loan</div>'
                f'<div style="color:{c["tx"]};font-size:16px;font-weight:700;'
                f'font-variant-numeric:tabular-nums">{max_loan_str}</div>'
                f'<div style="color:{color};font-size:10px;font-weight:600;margin-top:2px">'
                f'{cushion_str}</div>'
                f'<div style="color:{c["tx3"]};font-size:10px;margin-top:2px">'
                f'binding: {result.binding_constraint}</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Summary verdict
    n_pass = sum(1 for r in results.values() if r.passes)
    if n_pass == 4:
        verdict_msg = (
            "✓ All 4 scenarios pass — deal is resilient through plausible "
            "refi/exit stress."
        )
        verdict_color = c["gn"]
    elif n_pass >= 2:
        verdict_msg = (
            f"⚠ {n_pass} of 4 pass — deal is fragile to severe shocks. "
            f"Consider lowering price or shortening hold."
        )
        verdict_color = c["yw"]
    else:
        verdict_msg = (
            f"✗ Only {n_pass} of 4 pass — deal is over-leveraged for any "
            f"realistic exit. Re-underwrite or pass."
        )
        verdict_color = c["rd"]
    st.markdown(
        f'<div style="margin-top:10px;padding:8px 12px;border-radius:4px;'
        f'background:rgba(255,255,255,0.04);border-left:3px solid {verdict_color};'
        f'color:{c["tx"]};font-size:13px;font-weight:500">{verdict_msg}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Full amortization schedule (entire 25-yr term, year-by-year)
# ---------------------------------------------------------------------------

def _render_amortization_schedule(deal: DealState) -> None:
    """Render the full 25-year debt amortization table inside an expander.

    `core.calc.build_debt_schedule` truncates at `hold_years` (because the
    cash-flow projection only needs the hold-period slice). For the
    Underwriting tab we want the FULL term so the analyst can see how the
    loan amortizes well past the planned exit — useful for assessing how
    much principal pay-down the deal earns over 5 vs 10 vs 15 years, and
    for stress-testing what happens if the hold extends.

    The schedule honors the IO toggle: years 1..io are interest-only.
    """
    c = config.COLORS
    if deal.loan_amount <= 0:
        st.caption("No loan amount on the current dial — nothing to amortize.")
        return

    # Build full-term schedule by reusing the same monthly-loop logic as
    # `core.calc.build_debt_schedule` but iterating over ALL amortization
    # years (not just the hold period).
    annual_rate = float(deal.interest_rate)
    amort_months = int(config.AMORT_MONTHS)
    io_months = int(deal.io) * 12
    monthly_rate = annual_rate / 12.0
    amort_remaining = max(amort_months - io_months, 0)

    if deal.loan_amount <= 0 or amort_remaining <= 0:
        am_monthly = 0.0
    elif annual_rate == 0.0:
        am_monthly = deal.loan_amount / amort_remaining
    else:
        r = monthly_rate
        n = amort_remaining
        am_monthly = (
            deal.loan_amount * r * (1.0 + r) ** n / ((1.0 + r) ** n - 1.0)
        )

    total_years = amort_months // 12  # 25 for the locked Eight Rock term
    rows: list[dict[str, Any]] = []
    balance = float(deal.loan_amount)
    for year in range(1, total_years + 1):
        beg_balance = balance
        yr_interest = 0.0
        yr_principal = 0.0
        for month in range(12):
            month_index = (year - 1) * 12 + month
            if month_index < io_months:
                m_interest = balance * monthly_rate
                m_principal = 0.0
            else:
                m_interest = balance * monthly_rate
                m_principal = am_monthly - m_interest
                if m_principal > balance:
                    m_principal = balance
                balance -= m_principal
            yr_interest += m_interest
            yr_principal += m_principal
        annual_pmt = yr_interest + yr_principal
        rows.append({
            "Year": year,
            "Type": "IO" if year <= int(deal.io) else "P&I",
            "Beg. Balance": beg_balance,
            "Annual Payment": annual_pmt,
            "Interest": yr_interest,
            "Principal": yr_principal,
            "End Balance": balance,
            "% Paid Down": (
                (deal.loan_amount - balance) / deal.loan_amount
                if deal.loan_amount > 0 else 0.0
            ),
        })

    # Compute aggregates for the summary line above the expander
    total_interest = sum(r["Interest"] for r in rows)
    total_principal = sum(r["Principal"] for r in rows)
    bal_at_exit = next(
        (r["End Balance"] for r in rows if r["Year"] == int(deal.hp)),
        None,
    )
    paid_at_exit = (
        (deal.loan_amount - bal_at_exit) / deal.loan_amount
        if bal_at_exit is not None and deal.loan_amount > 0 else 0.0
    )

    # Summary callout — one-liner facts that matter for the underwriting
    # decision, BEFORE the user has to expand the table.
    bal_at_exit_str = (
        f"${bal_at_exit:,.0f}" if bal_at_exit is not None else "—"
    )
    st.markdown(
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {c["ac"]};border-radius:6px;padding:10px 14px;'
        f'margin-bottom:10px;font-size:13px;color:{c["tx"]};line-height:1.6">'
        f'<b>${deal.loan_amount:,.0f}</b> loan · '
        f'<b>{annual_rate*100:.2f}%</b> rate · '
        f'<b>{total_years}-yr</b> amort'
        f'{" · " + str(int(deal.io)) + "-yr IO" if int(deal.io) > 0 else ""}'
        f'<br/>'
        f'<span style="color:{c["tx2"]}">Total interest over full term: '
        f'<b>${total_interest:,.0f}</b> · '
        f'Total principal: <b>${total_principal:,.0f}</b></span><br/>'
        f'<span style="color:{c["tx2"]}">At year-{int(deal.hp)} exit: '
        f'balance <b>{bal_at_exit_str}</b> · '
        f'principal paid down <b>{paid_at_exit*100:.1f}%</b></span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.expander(
        f"Show full {total_years}-year amortization schedule "
        f"(year-by-year P&I split)",
        expanded=False,
    ):
        df = pd.DataFrame(rows)
        # Format dollar columns with $X,XXX,XXX (per Brian's
        # feedback_financial_formatting.md rule).
        for col in ("Beg. Balance", "Annual Payment", "Interest",
                    "Principal", "End Balance"):
            df[col] = df[col].apply(lambda v: f"${v:,.0f}")
        df["% Paid Down"] = df["% Paid Down"].apply(lambda v: f"{v*100:.1f}%")
        # Highlight the exit-year row so the analyst can see WHERE in the
        # table the planned hold ends.
        def _highlight_exit(row):
            if row["Year"] == int(deal.hp):
                return [f"background-color: rgba(200, 144, 10, 0.12)"] * len(row)
            return [""] * len(row)
        styled = df.style.apply(_highlight_exit, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)
        st.caption(
            f"Gold-tinted row marks the planned year-{int(deal.hp)} exit. "
            "Rows above that = principal pay-down captured during the hold; "
            "rows below = future amortization the buyer of the deal would "
            "inherit."
        )


# ---------------------------------------------------------------------------
# 5-year CF table
# ---------------------------------------------------------------------------

def _render_cashflow_table(
    deal: DealState,
    sources: dict[str, Any] | None,
    units: int | None,
    city: str | None = None,
) -> None:
    gpr, expenses = _derive_year1_inputs(deal, sources, units, city=city)

    debt_terms = DebtTerms(
        loan_amount=deal.loan_amount,
        annual_rate=deal.interest_rate,
        amort_months=config.AMORT_MONTHS,
        io_years=deal.io,
    )
    debt_sched = build_debt_schedule(debt_terms, deal.hp)

    # Apply B3+B4: year-1 vacancy spike + stabilization ramp.
    # year1_eff_vac = base + spike-weighted-by-stabilization-period.
    year1_eff_vac = effective_year1_vacancy(
        base_vac=deal.vacancy_frac,
        spike_pp=deal.vac_spike_pp / 100.0,
        stabilization_months=deal.stabilization_months,
    )
    cf = build_cashflow(
        year1_gpr=gpr,
        year1_vacancy_pct=year1_eff_vac,
        year1_expenses=expenses,
        rent_growth=deal.rent_growth,
        expense_growth=deal.expense_growth,
        am_fee_pct=deal.am_fee_pct,
        debt=debt_sched,
        hold_years=deal.hp,
        exit_cap=deal.exit_cap,
        equity_raise=deal.equity_raise,
        stabilized_vacancy_pct=deal.vacancy_frac,
        # If stabilization completes within year 1, year-2+ uses stabilized vac.
        stabilization_year_break=1 if deal.stabilization_months <= 12 else 2,
    )

    # Build dataframe: rows = line items, columns = years
    rows = {
        "GPR": [r.gpr for r in cf.rows],
        "Vacancy loss": [-r.vacancy_loss for r in cf.rows],
        "EGI": [r.egi for r in cf.rows],
        "Expenses": [-r.expenses for r in cf.rows],
        "NOI": [r.noi for r in cf.rows],
        "AM fee": [-r.am_fee for r in cf.rows],
        "NOI after AM": [r.noi_after_am for r in cf.rows],
        "Debt service": [-r.debt_service for r in cf.rows],
        "Cash flow": [r.cash_flow for r in cf.rows],
        "CoC": [r.coc for r in cf.rows],
    }
    cols = [f"Year {r.year}{' (IO)' if r.is_io else ''}" for r in cf.rows]
    df = pd.DataFrame(rows, index=cols).T

    # Format columns
    for c in df.columns:
        if c == "CoC":
            df[c] = df[c].apply(lambda v: f"{v*100:.2f}%")
        else:
            df[c] = df[c].apply(lambda v: f"${v:,.0f}")

    st.dataframe(df, use_container_width=True)

    # ---- Exit + return summary, in clean metric tiles ----
    st.markdown(f"###### Exit (Year {deal.hp + 1} NOI / exit cap @ {deal.exit_cap*100:.2f}%)")
    col_g, col_l, col_n = st.columns(3)
    col_g.metric("Gross sale proceeds", f"${cf.exit_proceeds_gross:,.0f}")
    col_l.metric(
        "Loan payoff",
        f"−${cf.exit_loan_payoff:,.0f}",
    )
    col_n.metric("Net to equity", f"${cf.exit_proceeds_net:,.0f}")

    st.markdown("###### Project-level returns (gross, pre-waterfall)")
    proj_rate = _proj_irr(cf, deal.equity_raise) or 0
    col_irr, col_em = st.columns(2)
    col_irr.metric("Project IRR", f"{proj_rate*100:.2f}%")
    col_em.metric("Equity Multiple", f"{cf.equity_multiple:.2f}x")


def _proj_irr(cf, equity_raise: float) -> float | None:
    annual = [r.cash_flow for r in cf.rows]
    return project_irr(
        equity_raise=equity_raise,
        annual_cashflows=annual,
        exit_proceeds_net=cf.exit_proceeds_net,
    )


# ---------------------------------------------------------------------------
# Sensitivity matrix
# ---------------------------------------------------------------------------

def _render_sensitivity(
    deal: DealState,
    sources: dict[str, Any] | None,
    units: int | None,
    city: str | None = None,
) -> None:
    gpr, expenses = _derive_year1_inputs(deal, sources, units, city=city)

    base = SensitivityBase(
        purchase_price=deal.pp,
        year1_gpr=gpr,
        year1_expenses=expenses,
        am_fee_pct=deal.am_fee_pct,
        loan_amount=deal.loan_amount,
        annual_rate=deal.interest_rate,
        amort_months=config.AMORT_MONTHS,
        io_years=deal.io,
        hold_years=deal.hp,
        exit_cap=deal.exit_cap,
        equity_raise=deal.equity_raise,
    )
    grid = build_sensitivity(base)

    # Build a tidy DataFrame: rows = (vacancy, rent_growth), columns = expense_growth_label
    records = []
    for cell in grid.cells:
        records.append({
            "Vacancy": f"{cell.vacancy*100:.0f}%",
            "Rent growth": f"{cell.rent_growth*100:.1f}%",
            "Expense growth": cell.expense_growth_label,
            "Project IRR": f"{(cell.project_irr or 0)*100:.1f}%" if cell.project_irr is not None else "—",
            "LP IRR": f"{(cell.lp_irr or 0)*100:.1f}%" if cell.lp_irr is not None else "—",
            "Y1 CoC": f"{cell.coc_year1*100:.1f}%",
            "Flagged": "🔴" if cell.flagged else "",
        })
    df = pd.DataFrame(records)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        f"{grid.flagged_count} cell(s) below the {config.SENSITIVITY_LP_IRR_FLAG*100:.0f}% LP IRR threshold."
    )


# ---------------------------------------------------------------------------
# Verdict bar
# ---------------------------------------------------------------------------

def _render_verdict(
    deal: DealState,
    metrics: dict[str, float],
    city: str,
) -> None:
    c = config.COLORS
    result = evaluate(
        cap=metrics["cap"],
        dscr=metrics["dscr"],
        coc=metrics["coc"],
        ppu=metrics["ppu"],
        city=city,
    )

    color_map = {
        "GO": (c["gn"], c["gnbg"], c["gnbrd"]),
        "WATCH": (c["yw"], c["bg3"], c["bdr2"]),
        "FINANCING-CONSTRAINED-WATCH": (c["yw"], c["bg3"], c["bdr2"]),
        "NO-GO": (c["rd"], c["rdbg"], c["rdbrd"]),
    }
    fg, bg, brd = color_map.get(result.verdict, (c["tx2"], c["bg3"], c["bdr"]))

    rationale_html = "".join(
        f'<li style="color:{c["tx2"]};font-size:11px;line-height:1.5">{r}</li>'
        for r in result.rationale
    )

    st.markdown(
        f"""
<div style="background:{bg};border:1px solid {brd};border-radius:8px;
            padding:16px 20px;margin-top:14px">
  <div style="display:flex;align-items:center;justify-content:space-between">
    <div style="font-size:24px;font-weight:700;color:{fg};letter-spacing:1px">
      {result.verdict}
    </div>
    <div style="color:{c['tx3']};font-size:10px;text-transform:uppercase;letter-spacing:1px">
      Eight Rock Hurdles &nbsp;·&nbsp; Cap ≥ 7.5%  ·  DSCR ≥ 1.30x  ·  CoC ≥ 6.0%
    </div>
  </div>
  <ul style="margin:8px 0 0 18px;padding:0">{rationale_html}</ul>
</div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Top-level renderer
# ---------------------------------------------------------------------------

def build_seed_for(prop: dict[str, Any]):
    """The asset-aware seed behind build_default_deal, with its evidence.

    Surfaces that render the seeded numbers call this directly so they can
    print the basis next to the field (core.deal_seed.seed_caption).
    """
    from core import deal_seed
    return deal_seed.build_seed(prop)


def build_default_deal(prop: dict[str, Any]) -> DealState:
    """Build a sensible default DealState from the property record.

    Shared by the Underwriting tab (when a property has no saved deal.json yet)
    and the Input tab's quick-start form, so the seeded first numbers are
    identical no matter which surface creates the deal.

    Price is ASSET-ANCHORED as of 2026-08-13 (owner ask): this parcel's own
    recent sale, else the assessor's value, and only then the legacy market
    $/unit - see core.deal_seed, which also reports WHICH basis it used so
    the surfaces can say so inline. Everything else stays on the ratified
    config defaults.
    """
    seed = build_seed_for(prop)
    return DealState.model_validate({
        "s-pp": seed.purchase_price, "s-noi": int(seed.noi),
        "s-dp": int(config.DOWN_PAYMENT_DEFAULT * 100),
        "s-ir": int(config.INTEREST_RATE_DEFAULT * 100 * 10) / 10,
        "s-vac": int(config.VACANCY_DEFAULT * 100),
        "s-rg": int(config.RENT_GROWTH_DEFAULT * 100),
        "s-eg": int(config.EXPENSE_GROWTH_DEFAULT * 100),
        "s-xc": int(config.EXIT_CAP_DEFAULT * 100 * 10) / 10,
        "s-hp": config.HOLD_PERIOD_DEFAULT,
        "s-am": config.AMORT_YEARS, "s-io": 0,
        "s-amf": int(config.AM_FEE_PCT * 100),
    })


def render_underwriting(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    units = prop.get("units")
    city = prop.get("city") or ""

    # FR-9.3.3 presence — surfaced before the dials so a second editor sees it
    # while there is still time to coordinate.
    render_presence_banner(st, prop)

    # Load existing deal.json or build a sensible default from the property record
    deal = None
    if folder is not None:
        deal = load_deal(folder.path)
    if deal is None:
        deal = build_default_deal(prop)
        # Name the anchor, don't just say "derived from property record"
        # (owner ask 2026-08-13) - and keep the warning styling for a
        # market placeholder, which is the seed most likely to mislead.
        from core import deal_seed
        _seed = build_seed_for(prop)
        _msg = ("No saved dial yet — "
                f"{deal_seed.seed_caption(_seed)} Adjust sliders to save.")
        (st.info if _seed.is_anchored else st.warning)(_msg)

    # Load sources for T-12 inputs (if available)
    from data.property_io import load_sources
    sources = load_sources(folder.path) if folder else None

    with section_card("Deal Dials", icon="🎛️"):
        deal = _render_dials(deal, folder, prop)

    # Year-1 KPIs MOVED to the top of the Returns tab per Brian 5/29 v2.0.18.
    # We still need the metrics dict here for downstream sections (sensitivity,
    # verdict, refi-exit test), so compute-without-render via render=False.
    metrics = _render_metrics(deal, sources, units, city=city, render=False)

    # Brian 5/29 v2.0.23 — Value-Add CAPEX moved UP under Deal Dials. The
    # renovation schedule drives a lot of analyst intuition about how the
    # rent forecast lands; surfacing it early keeps the Underwriting tab's
    # narrative coherent (dials → CAPEX plan → unit-level reality →
    # incremental levers → amortization → sensitivity → stress → verdict).
    with section_card():
        _render_value_add_capex(deal, folder)

    # Rent roll directly under live metrics — analyst sees the dials AND
    # the unit-level reality on the same screen. Vacant/Notice rows are
    # tinted so the eye finds them without scrolling. `render_rent_roll`
    # emits its OWN heading (it's shared with property_detail + comps), so
    # we wrap with a title-less card to avoid a duplicate header.
    if folder is not None:
        from ui.rent_roll import render_rent_roll
        with section_card():
            render_rent_roll(folder, section_title="Rent Roll", expand_units=False)

    # Per-unit-type rent gap (B2) + Value-add levers (B1) + Cost-seg (B6).
    # Each helper emits its own h5 heading inside, so we wrap with
    # title-less cards.
    with section_card():
        _render_unit_rent_gap(folder)
    with section_card():
        _render_value_add_levers(deal, folder, units, prop=prop)
    with section_card():
        _render_cost_seg_hook(deal, units)

    # 5-Year Cash Flow MOVED to the top of the Summary tab per Brian 5/29
    # v2.0.20. It's the canonical "what does the deal look like over the
    # hold" view, which fits the Summary narrative more than this tab.

    # Full 25-year amortization schedule (per Brian 2026-05-08): summary
    # callout always visible; the year-by-year P&I table sits inside an
    # expander so it's one click away when needed but doesn't clutter.
    with section_card(
        "Amortization Schedule",
        icon="🏦",
        subtitle=f"Full {config.AMORT_YEARS}-year term · year-by-year P&I split",
    ):
        _render_amortization_schedule(deal)

    with section_card(
        "Sensitivity",
        icon="🎚️",
        subtitle="vacancy × rent growth × expense growth",
    ):
        _render_sensitivity(deal, sources, units, city=city)

    # Brian 5/29 v2.0.23 — Refi / Exit Stress Test moved BELOW Sensitivity.
    # Sensitivity surfaces "where does this deal break" in continuous
    # space; the 4-scenario stress test then quantifies the lender re-
    # underwrite risk at exit. Reads as: dials → CAPEX → operating reality
    # → cost basis → schedule → sensitivity surface → exit-stress drill-in
    # → verdict.
    with section_card("Refi / Exit Stress Test", icon="🔬", subtitle="Beardsley 4-scenario"):
        _render_refi_exit_test(deal, metrics)

    with section_card("Verdict", icon="🚦"):
        _render_verdict(deal, metrics, city)

    # Market Calibration moved here per Brian 5/29 v2.0.14 — it's reference
    # data (the thresholds we're underwriting AGAINST), not part of the
    # active dials. Putting it at the bottom keeps the active workflow
    # (dials → metrics → stress test → cash flow → verdict) above the fold,
    # with the calibration panel available as a "what are my bars?" lookup.
    with section_card("Market Calibration", icon="📐",
                       subtitle="Live thresholds (floor / market / override)"):
        render_market_calibration_panel(subject_city=city or None)
