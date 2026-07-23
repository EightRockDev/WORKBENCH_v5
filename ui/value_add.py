"""Value-add levers + per-unit-type rent gap + cost-segregation hook.

Three sections rendered inline on the Underwriting tab:

  - **B1 — 21-Lever Value-Add Menu**: toggleable checkboxes for the major
    income/expense levers from Murray's Bonus Supplements I/II/III. Each
    enabled lever shows its dollarized GPR or expense impact and a
    cumulative "Total annual NOI lift" tile at the bottom.

  - **B2 — Per-unit-type rent gap**: groups the rent roll by unitType and
    shows in-place actual rent vs. market rent per floorplan, computing
    the loss-to-lease ($) and the upside ($) at full mark-to-market.

  - **B6 — Cost-segregation hook**: when hold ≤ 7 yrs, shows the estimated
    bonus-depreciation tax shield ($/unit and % of basis) so the analyst
    can flag the LP-facing tax pitch.

These are READ-ONLY illustrative panels — they don't currently flow back
into the underwriting model's GPR/expense calcs. Phase 2 of B1 (future)
would let the analyst commit toggled levers as actual model adjustments.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

import config
from data.property_io import PropertyFolder, load_sources
from ui.components import v2_strip_icon


# ---------------------------------------------------------------------------
# B1 — 21-Lever Value-Add Menu
# ---------------------------------------------------------------------------

# Murray's value-add lever taxonomy (B1 = Income, B2 = Ancillary, B3 = Expense).
# Each lever has a default $/unit/yr or %-of-GPR impact, plus a "why" string.
# Defaults are conservative midpoints — analyst can override per deal.
VALUE_ADD_LEVERS = [
    # ---- Income levers (B1) ----
    {
        "id": "rent_market_adj",
        "category": "Income",
        "name": "Rent → market (mark-to-market)",
        "default_per_unit_yr": 1200,
        "default_pct_gpr": None,
        "why": "Phase rents to market on turnover. Avg $100/mo per unit lift = $1,200/u/yr. Per-unit-type gap shown below.",
    },
    {
        "id": "biweekly_rent",
        "category": "Income",
        "name": "Bi-weekly rent payment option",
        "default_per_unit_yr": None,
        "default_pct_gpr": 0.0833,
        "why": "Charging biweekly = 26 payments × half-rent vs. 12 monthly. WHY: 8.33% revenue lift. Murray B1.",
    },
    {
        "id": "lease_term_premium",
        "category": "Income",
        "name": "Lease term premium (12+ mo concessions)",
        "default_per_unit_yr": 240,
        "default_pct_gpr": None,
        "why": "$20/mo premium on 12+ month leases over 6-mo. WHY: stabilizes turnover + premium pricing.",
    },
    {
        "id": "retention",
        "category": "Income",
        "name": "Tenant retention program (save 10% of leavers)",
        "default_per_unit_yr": 320,
        "default_pct_gpr": None,
        "why": "$200 turnover cost × 40% turnover × 0.10 saved × 4 prevented turns = ~$320/u/yr. Murray B1.",
    },
    # ---- Ancillary income (B2) ----
    {
        "id": "rubs",
        "category": "Ancillary",
        "name": "RUBS / submeter (utility billback)",
        "default_per_unit_yr": 360,
        "default_pct_gpr": None,
        "why": "$30/u/mo NOI lift typical. WHY: 20%+ consumption drop + $360/u/yr revenue. At 8% cap = $4.5K/u value.",
    },
    {
        "id": "wd_retrofit",
        "category": "Ancillary",
        "name": "In-unit W/D retrofit ($75-$150/mo premium)",
        "default_per_unit_yr": 1200,
        "default_pct_gpr": None,
        "why": "$100/mo rent premium per retrofitted unit. WHY: B/C unmet demand. ~$3K capex / unit, ~3yr payback.",
    },
    {
        "id": "pet_rent",
        "category": "Ancillary",
        "name": "Pet rent + nonrefundable deposit",
        "default_per_unit_yr": 180,
        "default_pct_gpr": None,
        "why": "$25-$35/mo pet rent × 50% pet ownership = ~$15/u/mo blended × 12. Plus $250 nonref deposit on turn.",
    },
    {
        "id": "fees",
        "category": "Ancillary",
        "name": "Fee program (move-in, late, transfer, parking, etc.)",
        "default_per_unit_yr": None,
        "default_pct_gpr": 0.05,
        "why": "Penalty + admin fees can total 5% of property income (Murray). Hold/admin/late/transfer/parking/pest control.",
    },
    {
        "id": "garages_storage",
        "category": "Ancillary",
        "name": "Garages / storage rental",
        "default_per_unit_yr": 240,
        "default_pct_gpr": None,
        "why": "$50/mo garage rent × 30% take rate + $20/mo storage × 50% take rate = ~$240/u/yr blended.",
    },
    {
        "id": "laundry_rate",
        "category": "Ancillary",
        "name": "Common laundry rate hike (+20%)",
        "default_per_unit_yr": 60,
        "default_pct_gpr": None,
        "why": "$2.50→$3.00 wash + dry. WHY: 20% rate lift + low elasticity. ~$5/u/mo blended.",
    },
    # ---- Expense reduction (B3) — negative numbers = savings ----
    {
        "id": "low_flow_water",
        "category": "Expense",
        "name": "Low-flow toilets + aerators (vintage props)",
        "default_per_unit_yr": -180,
        "default_pct_gpr": None,
        "why": "Pre-1994 3.5GPF toilets → 1.2GPF = 65% water cut. ~$15/u/mo savings on water-billed properties.",
    },
    {
        "id": "led_lighting",
        "category": "Expense",
        "name": "LED retrofit + motion sensors (common areas)",
        "default_per_unit_yr": -90,
        "default_pct_gpr": None,
        "why": "~50% common-area electric reduction. Utility rebates often pay for the lamps. ~$7.50/u/mo savings.",
    },
    {
        "id": "trash_rightsizing",
        "category": "Expense",
        "name": "Trash service right-sizing + waste broker",
        "default_per_unit_yr": -60,
        "default_pct_gpr": None,
        "why": "Audit dumpster size + frequency vs. actual usage. Brokered hauler bid often saves 20-30%.",
    },
    {
        "id": "remove_features",
        "category": "Expense",
        "name": "Remove garbage disposals / ice makers on turn",
        "default_per_unit_yr": -45,
        "default_pct_gpr": None,
        "why": "Vintage tenants don't value, costly to maintain. Eliminates 3-4 service calls/u/yr × ~$15 each.",
    },
    {
        "id": "deadbolts_only",
        "category": "Expense",
        "name": "Doorknob locks → deadbolts only (slash lockouts)",
        "default_per_unit_yr": -30,
        "default_pct_gpr": None,
        "why": "Lockout calls drop ~80% with deadbolt-only locks. ~$30/u/yr maintenance savings.",
    },
    {
        "id": "tax_appeal",
        "category": "Expense",
        "name": "Property tax assessment appeal",
        "default_per_unit_yr": None,
        "default_pct_gpr": -0.005,
        "why": "Aggressive appeal of post-sale reassessment. ~5-10% tax line reduction = ~0.5% of GPR savings.",
    },
    {
        "id": "insurance_shop",
        "category": "Expense",
        "name": "Insurance competitive bid (high deductibles)",
        "default_per_unit_yr": -75,
        "default_pct_gpr": None,
        "why": "Competing brokers + raising deductibles to $25K-$50K typically drops premiums 10-20%.",
    },
    {
        "id": "stagger_leases",
        "category": "Expense",
        "name": "Stagger lease start dates (smooth turnovers)",
        "default_per_unit_yr": -45,
        "default_pct_gpr": None,
        "why": "Avoids month-1 turn cascade. Reduces overtime + temp labor + vacant-unit utility costs.",
    },
    {
        "id": "renters_insurance",
        "category": "Expense",
        "name": "Mandatory renter's insurance (reduce LL claims)",
        "default_per_unit_yr": -60,
        "default_pct_gpr": None,
        "why": "Tenants carry first-loss → LL claim frequency drops ~30%. Savings on premium + deductible exposure.",
    },
    {
        "id": "competitive_bids",
        "category": "Expense",
        "name": "Always 2+ competitive bids on contracts",
        "default_per_unit_yr": -120,
        "default_pct_gpr": None,
        "why": "Landscaping, pest, fire-life-safety, painting — incumbent renewals run 10-15% over market. ~$10/u/mo blended.",
    },
    {
        "id": "preventive_maint",
        "category": "Expense",
        "name": "Preventive maintenance program (HVAC filters etc.)",
        "default_per_unit_yr": -90,
        "default_pct_gpr": None,
        "why": "Spending $30/u/yr on PM saves ~$120/u/yr in unplanned repairs. 4× ROI per Murray B3.",
    },
]


def _render_value_add_levers(
    deal,
    folder: PropertyFolder | None,
    units: int | None,
    prop: dict | None = None,
) -> None:
    """B1 — 21-Lever Value-Add Menu rendered as toggleable checklist.

    Selections persist per-property in `deal.selected_levers` (saved to
    `deal.json`). Widget keys use the property_id so:
      - Switching properties doesn't carry over selections from the prior one.
      - Tweaking the price slider doesn't reset checkboxes (which it did
        previously when keys used `pp`).
    """
    c = config.COLORS
    if not units or units < 1:
        return

    # Per-property widget key prefix. Use property_id (stable across slider
    # tweaks); fall back to a generic key if no property record (shouldn't
    # happen on the Underwriting tab in practice).
    if prop is None:
        prop = {}
    pid = str(prop.get("property_id") or "noid").replace("-", "_")

    # Estimate GPR for %-of-GPR levers: use deal.noi / 0.55 as rough proxy
    # (Class C ~45% expense ratio implies NOI/GPR ≈ 0.55 - vacancy)
    est_gpr = float(getattr(deal, "noi", 0)) / 0.55 if getattr(deal, "noi", 0) else 0.0

    st.markdown(v2_strip_icon("##### 🛠️ Value-Add Lever Menu (21 Income / Ancillary / Expense levers)"))
    st.caption(
        "Toggle levers you intend to execute. Each enabled lever shows its "
        "annualized $ impact and contributes to a cumulative NOI-lift total. "
        "Per Murray's Multifamily Bonus Supplements (B1/B2/B3). Selections "
        "save per-property to `deal.json`."
    )

    # Pre-fill checkbox state from saved deal.selected_levers
    saved_levers = set(getattr(deal, "selected_levers", []) or [])

    # Group by category
    by_category: dict[str, list[dict]] = {}
    for lever in VALUE_ADD_LEVERS:
        by_category.setdefault(lever["category"], []).append(lever)

    selected_levers: dict[str, float] = {}
    new_selected_ids: set[str] = set()
    for category, levers in by_category.items():
        n_in_cat_selected = sum(1 for L in levers if L["id"] in saved_levers)
        # Auto-expand the category if any levers are toggled (so Brian sees
        # what's saved on this property without having to click).
        with st.expander(
            f"{category} ({len(levers)} levers"
            + (f", {n_in_cat_selected} selected" if n_in_cat_selected else "")
            + ")",
            expanded=n_in_cat_selected > 0,
        ):
            for lever in levers:
                key = f"vlever_{pid}_{lever['id']}"
                cols = st.columns([1, 4, 2])
                with cols[0]:
                    enabled = st.checkbox(
                        " ", value=(lever["id"] in saved_levers),
                        key=key, label_visibility="collapsed",
                        help=lever["why"],
                    )
                with cols[1]:
                    st.markdown(
                        f'<div style="color:{c["tx"]};font-size:13px;'
                        f'font-weight:500">{lever["name"]}</div>'
                        f'<div style="color:{c["tx3"]};font-size:11px;'
                        f'line-height:1.4">{lever["why"]}</div>',
                        unsafe_allow_html=True,
                    )
                with cols[2]:
                    if lever["default_per_unit_yr"] is not None:
                        impact = lever["default_per_unit_yr"] * units
                    elif lever["default_pct_gpr"] is not None:
                        impact = est_gpr * lever["default_pct_gpr"]
                    else:
                        impact = 0
                    impact_color = c["gn"] if impact > 0 else c["rd"] if impact < 0 else c["tx2"]
                    label = "income" if impact > 0 else "savings" if impact < 0 else ""
                    st.markdown(
                        f'<div style="color:{impact_color};font-size:13px;'
                        f'font-weight:600;text-align:right;font-variant-numeric:tabular-nums">'
                        f'${abs(int(impact)):,}/yr<br>'
                        f'<span style="font-size:10px;font-weight:400;'
                        f'text-transform:uppercase">{label}</span></div>',
                        unsafe_allow_html=True,
                    )
                if enabled:
                    selected_levers[lever["id"]] = impact
                    new_selected_ids.add(lever["id"])

    # Total tile
    if selected_levers:
        total = sum(selected_levers.values())
        per_unit = total / units if units else 0
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:4px solid {c["ac"]};'
            f'border-radius:6px;padding:12px 16px;margin-top:10px">'
            f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600">Total Annual NOI Lift '
            f'({len(selected_levers)} levers selected)</div>'
            f'<div style="font-size:28px;font-weight:700;color:{c["ac2"]};'
            f'font-variant-numeric:tabular-nums;line-height:1.05;margin-top:4px">'
            f'${total:,.0f}<span style="font-size:14px;font-weight:400;'
            f'color:{c["tx2"]};margin-left:12px">${per_unit:,.0f} / unit / yr</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # Persist selection changes back to deal.json (idempotent — only writes
    # when the set actually changed). Uses pydantic's model_copy + the same
    # save path the dials use.
    if new_selected_ids != saved_levers:
        from data.property_io import ensure_property_folder, save_deal
        target_folder = folder
        if target_folder is None:
            # Auto-create folder if needed (consistent with dial-save behavior)
            try:
                target_folder = ensure_property_folder(prop)
            except Exception:
                target_folder = None
        if target_folder is not None:
            new_deal = deal.model_copy(
                update={"selected_levers": sorted(new_selected_ids)}
            )
            save_deal(target_folder.path, new_deal)
            # Don't st.rerun() here — checkbox state is already correct on
            # this render. Rerunning would just flicker the page.


# ---------------------------------------------------------------------------
# B2 — Per-Unit-Type Rent Gap (in-place vs market by floorplan)
# ---------------------------------------------------------------------------

def _render_unit_rent_gap(folder: PropertyFolder | None) -> None:
    """B2 — Per-unit-type rent gap table.

    Groups the rent roll by `unitType` and computes, per floorplan:
      - Number of units (occupied + vacant + notice)
      - Avg in-place actual rent (occupied units only)
      - Avg market rent (across all units in the type)
      - Gap $/mo (market − actual) AND % of market
      - Annualized upside if all units rent at market

    WHY: Loss-to-lease quantified BY FLOORPLAN. The 2BR-renovated units
    might already be at market while the 1BR untouched units are 20%
    under. Phased renovation (Lindahl) targets the biggest gaps first.
    """
    if folder is None:
        return
    sources = load_sources(folder.path)
    if not sources or "rentRoll" not in sources:
        return
    rr = sources.get("rentRoll", {})
    units = rr.get("units") or []
    if not units:
        return

    c = config.COLORS
    st.markdown(v2_strip_icon("##### 📐 Per-Unit-Type Rent Gap (in-place vs market)"))
    st.caption(
        "Loss-to-lease by floorplan — shows EXACTLY where the upside lives. "
        "Lindahl-style phased renovation targets the biggest gaps first."
    )

    df = pd.DataFrame(units)
    if "unitType" not in df.columns or "marketRent" not in df.columns:
        st.caption("Rent roll missing `unitType` or `marketRent` fields.")
        return

    # Coerce numerics
    df["marketRent"] = pd.to_numeric(df.get("marketRent"), errors="coerce")
    df["actualRent"] = pd.to_numeric(df.get("actualRent"), errors="coerce")
    df["sqft"] = pd.to_numeric(df.get("sqft"), errors="coerce")

    occupied_mask = df["actualRent"].fillna(0) > 0

    grouped = df.groupby("unitType").agg(
        n_units=("unit", "count"),
        n_occupied=("actualRent", lambda s: (s.fillna(0) > 0).sum()),
        avg_sqft=("sqft", "mean"),
        avg_market=("marketRent", "mean"),
    ).reset_index()
    # In-place avg rent — occupied only
    occupied_avg = (
        df[occupied_mask].groupby("unitType")["actualRent"].mean().reset_index()
    )
    occupied_avg.columns = ["unitType", "avg_actual"]
    grouped = grouped.merge(occupied_avg, on="unitType", how="left")

    grouped["gap_per_mo"] = grouped["avg_market"] - grouped["avg_actual"]
    grouped["gap_pct"] = grouped["gap_per_mo"] / grouped["avg_market"]
    grouped["upside_per_yr"] = grouped["gap_per_mo"] * grouped["n_units"] * 12
    grouped["sqft_avg_int"] = grouped["avg_sqft"].apply(
        lambda v: int(v) if pd.notna(v) else 0
    )

    # Build display
    display = pd.DataFrame({
        "Floorplan":   grouped["unitType"],
        "Units":       grouped["n_units"],
        "Occupied":    grouped["n_occupied"],
        "Avg sqft":    grouped["sqft_avg_int"].apply(lambda v: f"{v:,}" if v else "—"),
        "In-place $":  grouped["avg_actual"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        ),
        "Market $":    grouped["avg_market"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        ),
        "Gap $/mo":    grouped["gap_per_mo"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        ),
        "Gap %":       grouped["gap_pct"].apply(
            lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—"
        ),
        "Annual upside": grouped["upside_per_yr"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        ),
    })
    st.dataframe(display, use_container_width=True, hide_index=True)

    # Total upside callout
    total_upside = grouped["upside_per_yr"].fillna(0).sum()
    if total_upside > 0:
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:4px solid {c["gn"]};'
            f'border-radius:6px;padding:10px 14px;margin-top:6px">'
            f'<b>Total mark-to-market upside: '
            f'<span style="color:{c["gn"]};font-size:18px;'
            f'font-variant-numeric:tabular-nums">${total_upside:,.0f}/yr</span></b><br>'
            f'<span style="color:{c["tx2"]};font-size:12px">'
            f'If every unit re-leased at market today. Realistic capture '
            f'happens phased over the stabilization timeline (turnover-driven).'
            f'</span></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# B6 — Cost-Segregation Hook
# ---------------------------------------------------------------------------

def _render_cost_seg_hook(deal, units: int | None) -> None:
    """B6 — Cost-segregation tax-shield illustrative panel.

    For multifamily acquisitions, a cost-segregation study reclassifies
    25-30% of the basis from 27.5-yr SL into 5/7/15-yr buckets, allowing
    bonus depreciation to accelerate. With current bonus-dep schedules
    (40% in 2025, 20% in 2026, 0% in 2027 unless extended), the first-year
    tax shield can dwarf the year-1 cash flow.

    This panel is read-only — meant as an LP-pitch illustration. Real cost
    seg requires a study from a qualified engineer; this is a planning
    estimate.
    """
    c = config.COLORS
    pp = float(getattr(deal, "pp", 0))
    hp = int(getattr(deal, "hp", 5))
    if pp <= 0:
        return

    # Cost-seg conventional assumptions
    BUILDING_BASIS_PCT = 0.85   # ~85% of price is building (15% land, non-depreciable)
    SHORT_LIFE_PCT = 0.275      # 27.5% reclassified to 5/7/15yr (Beardsley/Han)
    BONUS_DEP_2026 = 0.40       # currently 40% bonus dep through 2026
    LP_TAX_RATE = 0.37          # top-bracket fed; LP-facing illustration

    short_life_basis = pp * BUILDING_BASIS_PCT * SHORT_LIFE_PCT
    yr1_bonus_dep = short_life_basis * BONUS_DEP_2026
    yr1_tax_shield = yr1_bonus_dep * LP_TAX_RATE
    per_unit_shield = yr1_tax_shield / units if units else 0
    pct_of_basis = yr1_tax_shield / pp * 100

    st.markdown(v2_strip_icon("##### 💼 Cost Segregation — Year-1 Tax Shield (Illustrative)"))
    st.caption(
        "If your LPs do a cost-segregation study at close, reclassifying "
        f"~{SHORT_LIFE_PCT*100:.0f}% of building basis to 5/7/15-yr lives "
        f"unlocks {BONUS_DEP_2026*100:.0f}% bonus depreciation in 2026. "
        "Real numbers require an engineering study — this is a planning estimate."
    )

    only_mf_holds = hp <= 7
    color = c["gn"] if only_mf_holds else c["yw"]
    hold_note = (
        "✓ Hold ≤7 yrs makes accelerated depreciation worth it"
        if only_mf_holds else
        "⚠️ Hold > 7 yrs reduces cost-seg benefit (more recapture risk)"
    )

    cols = st.columns(4)
    with cols[0]:
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {color};'
            f'border-radius:6px;padding:10px 14px;min-height:96px">'
            f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600">Short-life basis</div>'
            f'<div style="font-size:24px;font-weight:700;color:{c["tx"]};line-height:1.05;'
            f'margin-top:4px;font-variant-numeric:tabular-nums">'
            f'${short_life_basis:,.0f}</div>'
            f'<div style="color:{c["tx3"]};font-size:10px;margin-top:4px">'
            f'85% × {pp:,.0f} × {SHORT_LIFE_PCT*100:.0f}%</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {color};'
            f'border-radius:6px;padding:10px 14px;min-height:96px">'
            f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600">Yr-1 Bonus Dep</div>'
            f'<div style="font-size:24px;font-weight:700;color:{c["tx"]};line-height:1.05;'
            f'margin-top:4px;font-variant-numeric:tabular-nums">'
            f'${yr1_bonus_dep:,.0f}</div>'
            f'<div style="color:{c["tx3"]};font-size:10px;margin-top:4px">'
            f'{BONUS_DEP_2026*100:.0f}% × short-life basis (2026 rate)</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {c["ac"]};'
            f'border-radius:6px;padding:10px 14px;min-height:96px">'
            f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600">Yr-1 LP Tax Shield</div>'
            f'<div style="font-size:24px;font-weight:700;color:{c["ac2"]};'
            f'line-height:1.05;margin-top:4px;font-variant-numeric:tabular-nums">'
            f'${yr1_tax_shield:,.0f}</div>'
            f'<div style="color:{c["tx3"]};font-size:10px;margin-top:4px">'
            f'{LP_TAX_RATE*100:.0f}% top-bracket fed rate</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with cols[3]:
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {color};'
            f'border-radius:6px;padding:10px 14px;min-height:96px">'
            f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600">Shield / Unit</div>'
            f'<div style="font-size:24px;font-weight:700;color:{c["tx"]};line-height:1.05;'
            f'margin-top:4px;font-variant-numeric:tabular-nums">'
            f'${per_unit_shield:,.0f}</div>'
            f'<div style="color:{c["tx3"]};font-size:10px;margin-top:4px">'
            f'{pct_of_basis:.1f}% of purchase basis</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="margin-top:8px;color:{color};font-size:12px;font-weight:600">'
        f'{hold_note}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Value-Add CAPEX (Short Hold) — per Brian 5/29 v2.0.19
# ---------------------------------------------------------------------------
#
# Inputs:
#   • Year-by-year unit renovation count (5 years)
#   • Cost per renovated unit ($)
#   • Monthly rent bump per renovated unit ($)
#   • Exit cap rate (pulled from deal.json)
#
# Math:
#   • Total CAPEX            = sum(units_per_year) × cost_per_unit
#   • Cumulative units yr N  = running sum through year N
#   • Annual rent ↑ (yr N)   = cumulative_units(N) × monthly_bump × 12
#   • Value created at exit  = stabilized_annual_rent_↑ ÷ exit_cap
#   • $ value per $1 CAPEX   = value_at_exit ÷ total_capex
#
# Brian's sanity-check note: his off-the-cuff formula was `4800 × exit_cap`
# but value-at-exit is `annual_rent_increase ÷ exit_cap` (capitalization).
# Multiplying by the cap rate would yield ~$264 instead of ~$87K — the
# inverse of what we want.

_CAPEX_PLAN_FILENAME = "value_add_capex.json"


def _load_capex_plan(folder_path) -> dict[str, Any]:
    """Read the per-property CAPEX plan from disk. Returns defaults when
    the file is missing or unreadable so the UI always renders."""
    import json
    fp = folder_path / _CAPEX_PLAN_FILENAME
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {
        "cost_per_unit": 15000.0,
        "monthly_rent_increase_per_unit": 200.0,
        "renovations_per_year": [2, 3, 2, 0, 0],
    }


def _save_capex_plan(folder_path, plan: dict[str, Any]) -> None:
    import json
    fp = folder_path / _CAPEX_PLAN_FILENAME
    fp.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def _render_value_add_capex(deal, folder: PropertyFolder | None) -> None:
    """Per Brian 5/29 v2.0.19 — placeholder calculator that models a
    year-by-year unit-renovation ramp and projects the value created at
    exit from the resulting rent bumps."""
    c = config.COLORS

    if folder is None:
        st.info(
            "Open a property first — your CAPEX plan saves automatically "
            "for each property."
        )
        return

    st.markdown(v2_strip_icon("##### 🛠️ Value-Add CAPEX (Short Hold)"))
    st.caption(
        "Enter how many units you'll renovate each year, the cost per "
        "unit, and the resulting monthly rent bump. The model rolls "
        "rent forward cumulatively and capitalizes the stabilized lift "
        "at the deal's exit cap to estimate value created at sale."
    )

    plan = _load_capex_plan(folder.path)
    folder_key = folder.folder_name

    # Brian 5/29 v2.0.28 — year count is now DYNAMIC to the deal's
    # hold period. Hold=5 → 5 boxes. Hold=7 → 7 boxes. Hold=10 → 10
    # boxes. Read from the live deal slider, default to 5 if missing,
    # clamp to a reasonable [1, 15] range.
    hp = int(getattr(deal, "hp", 5) or 5)
    hp = max(1, min(15, hp))

    # ---- Inputs ----
    col_l, col_r = st.columns([2, 1.2])

    with col_l:
        st.markdown(
            f'<div style="font-size:12px;color:{c["tx2"]};'
            f'font-weight:600;margin:6px 0 6px 0;text-transform:uppercase;'
            f'letter-spacing:0.4px">Renovation Schedule ({hp}-year hold)</div>',
            unsafe_allow_html=True,
        )
        existing = list(plan.get("renovations_per_year") or [])
        # Pad to hp years (preserves prior values for years that still
        # exist; truncates entries past the new hold if hold shrank).
        while len(existing) < hp:
            existing.append(0)
        existing = existing[:hp]

        renov_cols = st.columns(hp)
        new_renov: list[int] = []
        for yr_idx in range(hp):
            with renov_cols[yr_idx]:
                v = st.number_input(
                    f"Yr {yr_idx + 1}",
                    min_value=0,
                    max_value=999,
                    value=int(existing[yr_idx]),
                    step=1,
                    key=f"capex_renov_yr{yr_idx + 1}_{folder_key}",
                )
                new_renov.append(int(v))

    with col_r:
        st.markdown(
            f'<div style="font-size:12px;color:{c["tx2"]};'
            f'font-weight:600;margin:6px 0 6px 0;text-transform:uppercase;'
            f'letter-spacing:0.4px">Per-Unit Economics</div>',
            unsafe_allow_html=True,
        )
        cost_per = st.number_input(
            "Cost per renovated unit ($)",
            min_value=0.0,
            max_value=500_000.0,
            value=float(plan.get("cost_per_unit") or 15_000.0),
            step=1_000.0,
            key=f"capex_cost_{folder_key}",
        )
        rent_bump = st.number_input(
            "Monthly rent bump per renovated unit ($)",
            min_value=0.0,
            max_value=10_000.0,
            value=float(plan.get("monthly_rent_increase_per_unit") or 200.0),
            step=25.0,
            key=f"capex_bump_{folder_key}",
        )

    # Persist whenever the user touches a value (the rerun captures
    # whatever's in the widgets — no Save button needed).
    new_plan = {
        "cost_per_unit": float(cost_per),
        "monthly_rent_increase_per_unit": float(rent_bump),
        "renovations_per_year": new_renov,
    }
    if new_plan != plan:
        try:
            _save_capex_plan(folder.path, new_plan)
        except OSError as exc:
            st.warning(f"Could not save CAPEX plan: {exc}")

    # ---- Per-year ramp table ----
    cumulative_units = 0
    cumulative_capex = 0.0
    rows = []
    for yr_idx, units_this_yr in enumerate(new_renov, start=1):
        cumulative_units += int(units_this_yr)
        capex_this_yr = float(units_this_yr) * float(cost_per)
        cumulative_capex += capex_this_yr
        annual_rent_inc = cumulative_units * float(rent_bump) * 12.0
        rows.append({
            "Year": yr_idx,
            "Units renovated": int(units_this_yr),
            "Cumulative units": int(cumulative_units),
            "CAPEX this year": f"${capex_this_yr:,.0f}",
            "Cumulative CAPEX": f"${cumulative_capex:,.0f}",
            "Annual rent ↑ (cum.)": f"${annual_rent_inc:,.0f}",
        })

    st.markdown('<div style="margin-top:10px"></div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ---- Summary callout ----
    total_units = sum(new_renov)
    total_capex = float(total_units) * float(cost_per)
    stabilized_annual_rent_inc = float(total_units) * float(rent_bump) * 12.0
    exit_cap = getattr(deal, "exit_cap", 0.055) or 0.055
    value_at_exit = (
        stabilized_annual_rent_inc / exit_cap if exit_cap > 0 else 0.0
    )
    value_per_capex = (
        value_at_exit / total_capex if total_capex > 0 else 0.0
    )

    st.markdown(
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {c["ac"]};border-radius:6px;'
        f'padding:14px 18px;margin-top:10px">'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:18px">'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Total CAPEX</div>'
        f'<div style="font-size:22px;font-weight:700;color:{c["tx"]};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'${total_capex:,.0f}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'{total_units} units × ${cost_per:,.0f}/unit</div></div>'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Stabilized Annual Rent ↑</div>'
        f'<div style="font-size:22px;font-weight:700;color:{c["tx"]};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'${stabilized_annual_rent_inc:,.0f}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'{total_units} units × ${rent_bump:.0f}/mo × 12</div></div>'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Value Created at Exit</div>'
        f'<div style="font-size:22px;font-weight:700;color:{c["gn"]};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'${value_at_exit:,.0f}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'= rent ↑ ÷ exit cap ({exit_cap * 100:.2f}%)</div></div>'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">$ per $1 of CAPEX</div>'
        f'<div style="font-size:22px;font-weight:700;color:{c["gn"]};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'${value_per_capex:,.2f}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'$1 spent → ${value_per_capex:,.2f} at sale</div></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Formula correction note for Brian
    per_unit_value = (
        (float(rent_bump) * 12.0) / exit_cap if exit_cap > 0 else 0.0
    )
    per_unit_return = (
        per_unit_value / float(cost_per) if cost_per > 0 else 0.0
    )
    st.caption(
        f"**Formula sanity check:** value created at exit = "
        f"`annual rent ↑ ÷ exit cap`. At "
        f"${rent_bump:.0f}/mo × 12 = ${rent_bump * 12:,.0f}/unit/yr of "
        f"rent bump and a {exit_cap * 100:.2f}% exit cap, **each renovated "
        f"unit creates ${per_unit_value:,.0f} of value at sale on "
        f"${cost_per:,.0f} of CAPEX → a {per_unit_return:.2f}× return "
        f"on CAPEX**."
    )
