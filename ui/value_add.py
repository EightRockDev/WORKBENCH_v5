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
            # FR-9.3.1. Lever toggles touch one field, so a co-worker's
            # concurrent dial edit is safe to merge onto: reload, re-apply
            # just the levers, save. Only warn if even that races.
            res = save_deal(target_folder.path, new_deal,
                            expected_version=deal.row_version)
            if not res.ok and res.their_deal is not None:
                merged = res.their_deal.model_copy(
                    update={"selected_levers": sorted(new_selected_ids)}
                )
                res = save_deal(target_folder.path, merged,
                                expected_version=res.version)
            if not res.ok:
                st.warning(
                    "Someone else is saving this deal right now — your lever "
                    "change didn't stick. Toggle it again."
                )
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
# Value-Add CAPEX (Short Hold) — engine-wired as of 2026-08-31
# ---------------------------------------------------------------------------
#
# The renovation program lives on `DealState` (reno_units_by_year /
# reno_cost_per_unit / reno_monthly_rent_bump / reno_capex_funding), and every
# `build_cashflow` call site passes `deal.renovation_plan()` — so the header
# tiles, the Returns tab, the exec summary and this panel all see the same
# program. The panel's headline is measured by `core.renovation.
# renovation_impact` (the deal run twice through the engine, with and without
# the plan), never by a closed-form formula: the old `value_at_exit /
# total_capex` tile reduced to a per-unit ratio — the unit count cancelled,
# so it read $2.30 for 2 units or 200 (owner repro, Forrest Pines 2026-08-31).
#
# `value_add_capex.json` is LEGACY: read once to migrate an old plan into
# deal.json, then renamed to `.migrated`. `_load_capex_plan` /
# `_save_capex_plan` remain only for that migration and the standalone
# `test_v2_exhaustive.py` checks.

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


def _merge_schedule(stored: list, edited: list[int], hp: int) -> list[int]:
    """Overlay the widget-edited years onto the stored schedule.

    The widgets only show years 1..hp, so persisting the widget list alone
    would DELETE any stored years beyond the current hold — and a hold-dial
    round trip (5 -> 3 -> 5) would silently destroy the program's tail.
    build_renovation_plan already truncates non-destructively at compute
    time, so the stored list keeps its tail and survives A -> B -> A.
    """
    tail = [int(u or 0) for u in (stored or [])][hp:]
    return [int(u) for u in edited[:hp]] + tail


def _legacy_plan_values(legacy: dict) -> tuple[list[int], float, float]:
    """Coerce a legacy value_add_capex.json into (units, cost, bump).

    `x or default` would resurrect the default over a deliberate 0 —
    only an ABSENT value falls back. Negatives clamp to the model's floor.
    """
    units = [max(0, int(u or 0))
             for u in (legacy.get("renovations_per_year") or [])]
    legacy_cost = legacy.get("cost_per_unit")
    legacy_bump = legacy.get("monthly_rent_increase_per_unit")
    cost = 15_000.0 if legacy_cost is None else max(0.0, float(legacy_cost))
    bump = 0.0 if legacy_bump is None else max(0.0, float(legacy_bump))
    return units, cost, bump


def _migrate_legacy_capex_plan(deal, folder: PropertyFolder):
    """One-time import of a legacy `value_add_capex.json` into deal.json.

    Fills an EMPTY destination only, never overwrites (the
    test_property_seed pattern): runs only when the legacy file exists and
    the deal carries no renovation schedule yet. On success the file is
    renamed to `.migrated` — never deleted — so the import cannot repeat.
    Returns the (possibly updated) deal.
    """
    import json

    fp = folder.path / _CAPEX_PLAN_FILENAME
    if not fp.exists() or deal.reno_units_by_year:
        return deal
    try:
        legacy = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return deal
    if not isinstance(legacy, dict):
        return deal

    from data.property_io import save_deal
    units, cost, bump = _legacy_plan_values(legacy)
    updated = deal.model_copy(update={
        "reno_units_by_year": units,
        "reno_cost_per_unit": cost,
        "reno_monthly_rent_bump": bump,
    })
    result = save_deal(folder.path, updated,
                       expected_version=deal.row_version)
    if not result.ok:
        return deal  # concurrent editor holds the file; retry next render
    try:
        fp.rename(fp.with_name(fp.name + ".migrated"))
    except OSError:
        pass  # rename is best-effort; reno_units_by_year now gates the import
    # Overwrite any already-registered widget state (a conflicted first
    # attempt leaves widgets registered at the pre-migration zeros, and a
    # keyed widget ignores value= after first registration - the stale
    # zeros would auto-save right back over the migration).
    fk = folder.folder_name
    for idx, u in enumerate(units, start=1):
        st.session_state[f"capex_renov_yr{idx}_{fk}"] = int(u)
    st.session_state[f"capex_cost_{fk}"] = float(cost)
    st.session_state[f"capex_bump_{fk}"] = float(bump)
    return updated.model_copy(update={"row_version": result.version})


def _render_value_add_capex(
    deal,
    folder: PropertyFolder | None,
    units: int | None = None,
    city: str | None = None,
):
    """Value-add renovation program, wired into the returns engine.

    The schedule lives on `DealState` and is measured by running the deal
    twice through `build_cashflow` (`core.renovation.renovation_impact`) —
    once with the program, once without — so every figure here is the same
    engine the header tiles read. No closed-form headline survives: the old
    `$ per $1 of CAPEX` tile was a per-unit ratio in disguise (unit count
    cancelled) and read $2.30 regardless of the schedule.

    Returns the (possibly updated) deal so the caller's downstream sections
    render this run's edits instead of lagging one rerun behind.
    """
    c = config.COLORS

    if folder is None or deal is None:
        st.info(
            "Open a property first — your CAPEX plan saves automatically "
            "for each property."
        )
        return deal

    st.markdown(v2_strip_icon("##### 🛠️ Value-Add CAPEX (Short Hold)"))
    st.caption(
        "Enter how many units you'll renovate each year, the cost per "
        "unit, and the resulting monthly rent bump. The program flows "
        "into the deal's cash flow — GPR, NOI, exit value, the equity "
        "raise and the IRR all move with it."
    )

    # One-time migration from the legacy per-property JSON (2026-08-31).
    deal = _migrate_legacy_capex_plan(deal, folder)

    folder_key = folder.folder_name

    # Brian 5/29 v2.0.28 — year count is DYNAMIC to the deal's hold
    # period. Hold=5 → 5 boxes. Hold=7 → 7 boxes. Read from the live
    # deal slider, default to 5 if missing, clamp to [1, 15].
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
        existing = [int(u or 0) for u in (deal.reno_units_by_year or [])]
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
        # No `or`-fallbacks here: `deal.reno_cost_per_unit or 15_000` would
        # resurrect the default over a deliberately saved $0 and auto-save
        # it back. The model's own field defaults are the only fallback.
        cost_per = st.number_input(
            "Cost per renovated unit ($)",
            min_value=0.0,
            max_value=500_000.0,
            value=float(deal.reno_cost_per_unit),
            step=1_000.0,
            key=f"capex_cost_{folder_key}",
        )
        rent_bump = st.number_input(
            "Monthly rent bump per renovated unit ($)",
            min_value=0.0,
            max_value=10_000.0,
            value=float(deal.reno_monthly_rent_bump),
            step=25.0,
            key=f"capex_bump_{folder_key}",
        )

    funding_choice = st.radio(
        "Fund CAPEX from",
        ["LP equity raise (escrowed at close)", "Property cash flow"],
        index=0 if deal.reno_capex_funding == "raise" else 1,
        horizontal=True,
        key=f"capex_funding_{folder_key}",
    )
    new_funding = (
        "raise" if funding_choice.startswith("LP equity") else "operations"
    )
    st.caption(
        "Escrowed CAPEX joins the equity raise and the IRR denominator. "
        "Cash-flow funding reduces annual distributions instead."
    )

    # Persist through the SAME save path as the dial board
    # (model_copy + save_deal with expected_version) — never a fresh
    # model_validate of a subset, which is the 2026-08-13 infinite-rerun
    # bug. No st.rerun(): the widget interaction already reran.
    # The comparison baseline is `existing` (the padded/truncated VIEW of
    # the stored list), not the raw stored list — otherwise merely viewing
    # a deal whose stored schedule length differs from the hold period
    # would fire a write, and a hold-dial round trip would persist the
    # truncation. The saved list overlays the edit onto the stored tail
    # (_merge_schedule) so years beyond the current hold survive.
    if (
        new_renov != existing
        or float(cost_per) != float(deal.reno_cost_per_unit)
        or float(rent_bump) != float(deal.reno_monthly_rent_bump)
        or new_funding != deal.reno_capex_funding
    ):
        from data.property_io import save_deal
        updated = deal.model_copy(update={
            "reno_units_by_year": _merge_schedule(
                deal.reno_units_by_year, new_renov, hp),
            "reno_cost_per_unit": float(cost_per),
            "reno_monthly_rent_bump": float(rent_bump),
            "reno_capex_funding": new_funding,
        })
        result = save_deal(folder.path, updated,
                           expected_version=deal.row_version)
        if result.ok:
            deal = updated.model_copy(update={"row_version": result.version})
        else:
            st.warning(
                f"Not saved — {result.conflict_by or 'someone else'} changed "
                "this deal since you loaded it. Reload to pick up their edits."
            )

    plan = deal.renovation_plan()

    # ---- Per-year ramp table (engine figures, not the cumulative naive roll) ----
    cumulative_units = 0
    cumulative_capex = 0.0
    rows = []
    for yr_idx in range(hp):
        units_this_yr = plan.units_by_year[yr_idx]
        cumulative_units += units_this_yr
        cumulative_capex += plan.capex_by_year[yr_idx]
        rows.append({
            "Year": yr_idx + 1,
            "Units renovated": units_this_yr,
            "Cumulative units": cumulative_units,
            "CAPEX this year": f"${plan.capex_by_year[yr_idx]:,.0f}",
            "Cumulative CAPEX": f"${cumulative_capex:,.0f}",
            "Rent ↑ recognized": f"${plan.rent_lift_by_year[yr_idx]:,.0f}",
        })

    st.markdown('<div style="margin-top:10px"></div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "Units renovated during a year earn half a year of the bump in "
        "that year."
    )

    # ---- Engine-measured impact: the deal WITH vs WITHOUT the program ----
    from core.calc import DebtTerms, build_debt_schedule, effective_year1_vacancy
    from core.renovation import renovation_impact
    from ui.underwriting import _derive_year1_inputs  # lazy: avoids circular import

    sources = load_sources(folder.path)
    gpr, expenses = _derive_year1_inputs(deal, sources, units, city=city)
    debt_sched = build_debt_schedule(
        DebtTerms(
            loan_amount=deal.loan_amount,
            annual_rate=deal.interest_rate,
            amort_months=config.AMORT_MONTHS,
            io_years=deal.io,
        ),
        deal.hp,
    )
    year1_eff_vac = effective_year1_vacancy(
        base_vac=deal.vacancy_frac,
        spike_pp=deal.vac_spike_pp / 100.0,
        stabilization_months=deal.stabilization_months,
    )
    # The live raise carries the escrowed CAPEX — via tracked_raise when
    # tracking, and BY CONVENTION inside a custom (analyst-typed) raise
    # too. Strip it back out so renovation_impact adds it to the with-case
    # only; equity_with then equals deal.equity_raise on BOTH paths, so
    # the panel's IRR always agrees with the header tiles.
    equity_without = max(0.0, deal.equity_raise - deal.reno_capex_in_raise)
    impact = renovation_impact(
        plan=plan,
        capex_funding=deal.reno_capex_funding,
        equity_without_reno=equity_without,
        project_equity_without_reno=equity_without + deal.gp_fee,
        year1_gpr=gpr,
        year1_vacancy_pct=year1_eff_vac,
        year1_expenses=expenses,
        rent_growth=deal.rent_growth,
        expense_growth=deal.expense_growth,
        am_fee_pct=deal.am_fee_pct,
        debt=debt_sched,
        hold_years=deal.hp,
        exit_cap=deal.exit_cap,
        stabilized_vacancy_pct=deal.vacancy_frac,
        stabilization_year_break=1 if deal.stabilization_months <= 12 else 2,
    )

    irr_delta_txt = (
        f"{impact.irr_delta * 100:+.2f} pts" if impact.irr_delta is not None else "—"
    )
    irr_delta_color = (
        c["gn"] if (impact.irr_delta or 0) >= 0 else c["rd"]
    )
    em_delta_color = c["gn"] if impact.em_delta >= 0 else c["rd"]
    ppd_color = c["gn"] if impact.profit_per_capex_dollar >= 1.0 else c["rd"]

    st.markdown(
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {c["ac"]};border-radius:6px;'
        f'padding:14px 18px;margin-top:10px">'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:18px">'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Total CAPEX</div>'
        f'<div style="font-size:22px;font-weight:700;color:{c["tx"]};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'${impact.total_capex:,.0f}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'{plan.total_units} units × ${plan.cost_per_unit:,.0f}/unit</div></div>'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Δ Project IRR</div>'
        f'<div style="font-size:22px;font-weight:700;color:{irr_delta_color};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'{irr_delta_txt}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'vs the same deal with no renovation</div></div>'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Δ Equity Multiple</div>'
        f'<div style="font-size:22px;font-weight:700;color:{em_delta_color};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'{impact.em_delta:+.2f}x</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'vs the same deal with no renovation</div></div>'
        f'<div><div style="font-size:11px;color:{c["tx3"]};'
        f'text-transform:uppercase;letter-spacing:0.4px">Profit per $1 of CAPEX</div>'
        f'<div style="font-size:22px;font-weight:700;color:{ppd_color};'
        f'font-variant-numeric:tabular-nums;margin-top:2px">'
        f'${impact.profit_per_capex_dollar:,.2f}</div>'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
        f'net of the equity the program consumes</div></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # The engine's own story — replaces the closed-form "formula sanity
    # check". Dollars escaped (\$): Streamlit renders $...$ as LaTeX.
    irr_from = (
        f"{impact.irr_without:.2%}" if impact.irr_without is not None else "n/a"
    )
    irr_to = (
        f"{impact.irr_with:.2%}" if impact.irr_with is not None else "n/a"
    )
    st.caption(
        f"Running this deal with and without the program: the renovation "
        f"adds \\${impact.exit_value_delta:,.0f} of sale value at the "
        f"{deal.exit_cap * 100:.2f}% exit cap and "
        f"\\${impact.profit_delta:,.0f} of investor profit on "
        f"\\${impact.total_capex:,.0f} of CAPEX, moving project IRR from "
        f"{irr_from} to {irr_to}."
    )
    return deal
