"""Reverse-engineer seller's economic floor.

Given a property's purchase history (from assessor data), estimate:

  1. Current loan balance (assumes typical 70% LTV at acquisition,
     5-year IO + 25-year amort for 2020-22 vintage, etc.)
  2. Prepay penalty (yield maintenance / defeasance estimate based on
     origination rate vs current 10Y treasury)
  3. Tax basis at sale (cost segregation accelerated depreciation +
     27.5-year straight-line for residential rental)
  4. Capital gains + depreciation recapture liability (20% LTCG +
     25% recapture; ignores state)
  5. Net to seller at various sale prices

Output: "Seller's economic floor ≈ $X,XXX,XXX (range $A-$B); ask is $C;
gap is $D." Plus LOI-ready anchor-offer language.

All estimates carry explicit assumptions Brian can override. The point
isn't pinpoint accuracy — it's to convert "ask is $33.6M" from a
take-it-or-leave-it into "the seller needs $26.5M to break even and
$29.2M for a 5% IRR; our $27.5M offer is at the bottom of their
acceptable range."
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse the existing 10Y treasury lookup from calibration
_HERE = Path(__file__).resolve()
ETL_DB = _HERE.parent.parent.parent / "hampton-roads-etl" / "hampton_roads.db"


# ---------------------------------------------------------------------------
# Era-specific financing assumptions (typical multifamily)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinancingEra:
    """Typical financing terms by acquisition era."""
    label: str
    typical_ltv: float           # e.g. 0.70 = 70% LTV
    typical_rate: float          # the era's all-in mortgage rate
    typical_io_years: int        # interest-only period before amort kicks in
    amort_years: int = 25
    prepay_structure: str = "yield_maintenance"   # "yield_maintenance" | "step_down" | "defeasance" | "open"


# Mapped by year range. Conservative defaults; Brian can override at deal level.
ERAS = (
    FinancingEra("Cheap-money era", 0.70, 0.035, 5,
                 amort_years=25, prepay_structure="yield_maintenance"),
    FinancingEra("Rising-rate era", 0.65, 0.060, 2,
                 amort_years=25, prepay_structure="yield_maintenance"),
    FinancingEra("Post-COVID stable", 0.65, 0.060, 0,
                 amort_years=25, prepay_structure="step_down"),
    FinancingEra("Pre-2020 stabilized", 0.65, 0.045, 0,
                 amort_years=25, prepay_structure="step_down"),
)


def _era_for_year(year: int) -> FinancingEra:
    if 2020 <= year <= 2022:
        return ERAS[0]   # Cheap money era — bridge/agency at sub-4%
    if 2023 <= year <= 2024:
        return ERAS[1]   # Rising-rate era
    if 2017 <= year <= 2019:
        return ERAS[3]   # Pre-COVID stabilized
    return ERAS[2]       # Post-COVID stable (default)


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class SellerFloorAnalysis:
    """The full reverse-engineered seller economics."""
    purchase_price: float
    purchase_year: int
    years_held: float
    asking_price: float | None = None

    # Financing era + estimates
    era_label: str = ""
    est_loan_at_origination: float = 0.0
    est_current_loan_balance: float = 0.0
    est_origination_rate: float = 0.0
    est_prepay_penalty: float = 0.0
    prepay_structure: str = ""

    # Tax estimates
    est_total_depreciation_taken: float = 0.0
    est_adjusted_basis: float = 0.0
    est_capital_gain: float = 0.0
    est_depreciation_recapture_tax: float = 0.0
    est_capital_gain_tax: float = 0.0
    est_total_tax: float = 0.0
    est_closing_costs: float = 0.0

    # Floor estimates
    economic_floor_breakeven: float = 0.0      # exact zero net to seller
    economic_floor_5pct_irr: float = 0.0       # 5% IRR floor (won't sell below)
    economic_floor_10pct_irr: float = 0.0      # what seller really wants

    # Ask-vs-floor analysis
    ask_minus_floor: float | None = None
    ask_premium_pct: float | None = None
    negotiation_room: str = ""                  # "tight", "moderate", "loose"

    # LOI language
    rationale: list[str] = field(default_factory=list)
    loi_anchor_offer: float = 0.0
    loi_justification: str = ""


# ---------------------------------------------------------------------------
# Component calculators
# ---------------------------------------------------------------------------

def estimate_loan_balance(
    purchase_price: float,
    purchase_year: int,
    today_year: int | None = None,
    ltv_override: float | None = None,
    rate_override: float | None = None,
    io_years_override: int | None = None,
) -> tuple[float, float, FinancingEra]:
    """Return (loan_at_origination, est_balance_now, era).

    Uses era defaults unless overridden. Standard 25-yr amort post-IO.
    """
    today_year = today_year or dt.date.today().year
    era = _era_for_year(purchase_year)
    ltv = ltv_override if ltv_override is not None else era.typical_ltv
    rate = rate_override if rate_override is not None else era.typical_rate
    io_years = io_years_override if io_years_override is not None else era.typical_io_years

    loan_at_origination = purchase_price * ltv
    years_elapsed = today_year - purchase_year
    if years_elapsed <= 0:
        return loan_at_origination, loan_at_origination, era

    # Years of IO (no amort)
    io_actual = min(io_years, years_elapsed)
    amort_elapsed = max(0, years_elapsed - io_actual)

    if amort_elapsed == 0:
        return loan_at_origination, loan_at_origination, era

    # Standard amortization formula
    monthly_rate = rate / 12.0
    n_months_total = era.amort_years * 12
    n_months_paid = amort_elapsed * 12
    if monthly_rate <= 0:
        balance = loan_at_origination * (1 - n_months_paid / n_months_total)
    else:
        # Loan balance after n_months_paid out of n_months_total
        if n_months_paid >= n_months_total:
            balance = 0.0
        else:
            factor = (
                (1 + monthly_rate) ** n_months_total - (1 + monthly_rate) ** n_months_paid
            ) / (
                (1 + monthly_rate) ** n_months_total - 1
            )
            balance = loan_at_origination * factor

    return loan_at_origination, max(0.0, balance), era


def estimate_prepay_penalty(
    current_balance: float,
    origination_rate: float,
    current_market_rate: float,
    years_remaining_in_term: float = 5.0,
    structure: str = "yield_maintenance",
) -> float:
    """Yield-maintenance estimate.

    YM penalty ≈ present value of the rate differential × balance × years
    remaining. Simplified version of the standard agency YM formula.

    For "open" prepay (no penalty), returns 0. For "step_down", uses a
    flat 3% of balance for the first 3 years post-origination, declining
    thereafter.
    """
    if structure == "open":
        return 0.0
    if current_balance <= 0:
        return 0.0

    if structure == "yield_maintenance":
        # If current rate > origination rate, no economic loss to lender → no penalty
        rate_diff = max(0.0, origination_rate - current_market_rate)
        if rate_diff == 0:
            # Some YM structures still have a 1% minimum; we'll use that floor
            return current_balance * 0.01
        # Approximate PV: balance × rate_diff × years_remaining
        # (ignores discount — gives the lender's max expected interest loss)
        return current_balance * rate_diff * years_remaining_in_term

    if structure == "step_down":
        # Typical agency step-down: 5/4/3/2/1% over 5 years
        if years_remaining_in_term >= 5:
            return current_balance * 0.05
        if years_remaining_in_term >= 4:
            return current_balance * 0.04
        if years_remaining_in_term >= 3:
            return current_balance * 0.03
        if years_remaining_in_term >= 2:
            return current_balance * 0.02
        if years_remaining_in_term >= 1:
            return current_balance * 0.01
        return 0.0

    if structure == "defeasance":
        # Defeasance ≈ YM but with explicit treasury purchase cost (1-3% higher)
        return estimate_prepay_penalty(
            current_balance, origination_rate, current_market_rate,
            years_remaining_in_term, structure="yield_maintenance"
        ) * 1.15

    return 0.0


def estimate_depreciation(
    purchase_price: float,
    years_held: float,
    land_fraction: float = 0.20,
    use_cost_segregation: bool = True,
) -> float:
    """Total depreciation taken to date.

    Standard residential rental: 27.5-yr straight-line on the building portion.
    Cost segregation: accelerates ~30% of building basis to 5-15 year schedule.

    For HR Class C 1980s-90s vintage, cost seg is industry standard — assume True.
    """
    building_basis = purchase_price * (1 - land_fraction)
    if not use_cost_segregation:
        annual_depr = building_basis / 27.5
        return min(building_basis, annual_depr * years_held)

    # Cost seg approximation: split building into:
    #   30% 5-year accelerated (fully depreciated by year 5)
    #   20% 15-year accelerated (fully by year 15)
    #   50% 27.5-year straight-line
    seg_5yr = building_basis * 0.30
    seg_15yr = building_basis * 0.20
    seg_275yr = building_basis * 0.50

    depr_5yr = min(seg_5yr, seg_5yr * years_held / 5)
    depr_15yr = min(seg_15yr, seg_15yr * years_held / 15)
    depr_275yr = min(seg_275yr, seg_275yr * years_held / 27.5)

    return depr_5yr + depr_15yr + depr_275yr


def estimate_tax_liability(
    sale_price: float,
    purchase_price: float,
    total_depreciation: float,
    closing_costs_pct: float = 0.02,
) -> tuple[float, float, float, float]:
    """Returns (capital_gain, recapture_tax, capital_gain_tax, total_tax).

    Assumptions:
      - 25% depreciation recapture rate (federal)
      - 20% long-term capital gains rate (federal, top bracket)
      - Ignores state taxes (varies)
      - Ignores 1031 exchange potential (typically reduces effective tax to ~0)
    """
    closing_costs = sale_price * closing_costs_pct
    adjusted_basis = purchase_price - total_depreciation
    net_sale = sale_price - closing_costs
    total_gain = net_sale - adjusted_basis

    # Split gain: depreciation recapture portion + true capital gain portion
    recapture_gain = min(total_depreciation, max(0, total_gain))
    cap_gain = max(0.0, total_gain - recapture_gain)

    recapture_tax = recapture_gain * 0.25
    cap_gain_tax = cap_gain * 0.20
    total_tax = recapture_tax + cap_gain_tax

    return total_gain, recapture_tax, cap_gain_tax, total_tax


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def _get_current_10y() -> float | None:
    if not ETL_DB.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT value FROM fred_series WHERE series_id='DGS10' "
                "AND value IS NOT NULL ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return float(row[0]) / 100.0 if row else None
    except Exception:
        return None


def analyze_seller_floor(
    purchase_price: float,
    purchase_year: int,
    asking_price: float | None = None,
    today: dt.date | None = None,
    use_cost_segregation: bool = True,
    ltv_override: float | None = None,
    rate_override: float | None = None,
) -> SellerFloorAnalysis:
    """Compute the full seller-floor analysis for a single property.

    Args:
      purchase_price: Seller's original acquisition price (from assessor data)
      purchase_year: Year of acquisition
      asking_price: Current ask (optional — drives gap analysis)
      today: Reference date (default today)
      use_cost_segregation: Whether to assume the seller used cost seg
        (industry standard for HR Class C; default True)
      ltv_override / rate_override: Override era defaults if you know them
    """
    today = today or dt.date.today()
    years_held = (today - dt.date(purchase_year, 6, 30)).days / 365.25
    years_held = max(0.0, years_held)

    # 1. Loan
    loan_at_orig, est_balance_now, era = estimate_loan_balance(
        purchase_price, purchase_year, today.year,
        ltv_override, rate_override,
    )
    origination_rate = rate_override if rate_override is not None else era.typical_rate

    # 2. Prepay penalty
    current_10y = _get_current_10y() or 0.045
    # Estimate "years remaining in term" — typical 5-year locked term, decay
    years_in_term = max(0.0, 5.0 - max(0, years_held - era.typical_io_years))
    prepay = estimate_prepay_penalty(
        est_balance_now,
        origination_rate,
        current_10y + 0.020,    # current mortgage spread ~ 200 bps over 10Y
        years_in_term,
        era.prepay_structure,
    )

    # 3. Depreciation + tax
    total_depr = estimate_depreciation(
        purchase_price, years_held, use_cost_segregation=use_cost_segregation,
    )
    adjusted_basis = purchase_price - total_depr

    # 4. Economic floor at different target return levels
    # Floor = loan_balance + prepay + closing_costs + tax_at_that_price + target_irr_dollars
    # Solve iteratively for each target.

    def required_price_for_target_return(target_irr: float) -> float:
        """What sale price does seller need for target IRR on equity?
        Iterates because tax depends on sale price."""
        equity_invested = purchase_price - loan_at_orig
        target_proceeds = equity_invested * (1 + target_irr) ** years_held

        # Start with a guess + iterate twice
        guess = purchase_price * 1.30
        for _ in range(8):
            _, _, _, tax = estimate_tax_liability(guess, purchase_price, total_depr)
            closing = guess * 0.02
            # net_to_seller = guess - loan - prepay - tax - closing
            # solve guess so net_to_seller == target_proceeds
            guess = est_balance_now + prepay + tax + closing + target_proceeds

        return guess

    floor_breakeven = required_price_for_target_return(0.0)
    floor_5pct = required_price_for_target_return(0.05)
    floor_10pct = required_price_for_target_return(0.10)

    # 5. Tax at the breakeven price (for display)
    cap_gain, recapture_tax, cap_gain_tax, total_tax = estimate_tax_liability(
        floor_breakeven, purchase_price, total_depr,
    )
    closing_costs = floor_breakeven * 0.02

    # 6. Ask gap analysis
    ask_minus_floor = None
    ask_premium_pct = None
    negotiation_room = "unknown"
    rationale: list[str] = []

    rationale.append(
        f"Seller bought at ${purchase_price:,.0f} in {purchase_year} "
        f"({years_held:.1f} yr hold to date)."
    )
    rationale.append(
        f"Estimated loan: ${loan_at_orig:,.0f} at origination, "
        f"${est_balance_now:,.0f} balance now "
        f"(@ {era.typical_ltv*100:.0f}% LTV, {origination_rate*100:.1f}% rate, "
        f"{era.typical_io_years}-yr IO + {era.amort_years}-yr amort)."
    )
    if prepay > 0:
        rationale.append(
            f"Prepay penalty: ${prepay:,.0f} (structure: {era.prepay_structure})."
        )
    rationale.append(
        f"Cost segregation depreciation to date: ${total_depr:,.0f}; "
        f"adjusted basis: ${adjusted_basis:,.0f}."
    )
    rationale.append(
        f"At breakeven sale price ${floor_breakeven:,.0f}: "
        f"tax = ${total_tax:,.0f} "
        f"(${recapture_tax:,.0f} recapture + ${cap_gain_tax:,.0f} LTCG)."
    )

    loi_justification = ""
    loi_anchor_offer = floor_5pct
    if asking_price is not None:
        ask_minus_floor = asking_price - floor_breakeven
        ask_premium_pct = (asking_price / floor_breakeven - 1.0) if floor_breakeven > 0 else None

        rationale.append(
            f"Seller ask ${asking_price:,.0f} is "
            f"${ask_minus_floor:,.0f} above breakeven "
            f"({ask_premium_pct*100:.0f}% premium)."
        )

        # Negotiation room classification
        if ask_premium_pct is not None:
            if ask_premium_pct < 0.05:
                negotiation_room = "tight"
                loi_anchor_offer = max(floor_breakeven, asking_price * 0.95)
                loi_justification = (
                    f"Anchor at ${loi_anchor_offer:,.0f} — only 5% off ask. "
                    f"Seller has limited room (debt + tax = ${est_balance_now + prepay + total_tax:,.0f}); "
                    f"this offer leaves them ~0% IRR. Use this only if comps support."
                )
            elif ask_premium_pct < 0.15:
                negotiation_room = "moderate"
                loi_anchor_offer = floor_5pct
                loi_justification = (
                    f"Anchor at ${loi_anchor_offer:,.0f} — leaves seller a "
                    f"5% annualized return on their equity over the hold. "
                    f"Reasonable opening offer that's defensible if challenged."
                )
            else:
                negotiation_room = "loose"
                loi_anchor_offer = floor_breakeven * 1.03   # 3% above breakeven
                loi_justification = (
                    f"Anchor at ${loi_anchor_offer:,.0f} — close to seller's "
                    f"breakeven price. Ask appears aggressively priced "
                    f"({ask_premium_pct*100:.0f}% over breakeven). "
                    f"Hold the line; they may have to come back down."
                )

    return SellerFloorAnalysis(
        purchase_price=purchase_price,
        purchase_year=purchase_year,
        years_held=years_held,
        asking_price=asking_price,
        era_label=era.label,
        est_loan_at_origination=loan_at_orig,
        est_current_loan_balance=est_balance_now,
        est_origination_rate=origination_rate,
        est_prepay_penalty=prepay,
        prepay_structure=era.prepay_structure,
        est_total_depreciation_taken=total_depr,
        est_adjusted_basis=adjusted_basis,
        est_capital_gain=cap_gain,
        est_depreciation_recapture_tax=recapture_tax,
        est_capital_gain_tax=cap_gain_tax,
        est_total_tax=total_tax,
        est_closing_costs=closing_costs,
        economic_floor_breakeven=floor_breakeven,
        economic_floor_5pct_irr=floor_5pct,
        economic_floor_10pct_irr=floor_10pct,
        ask_minus_floor=ask_minus_floor,
        ask_premium_pct=ask_premium_pct,
        negotiation_room=negotiation_room,
        rationale=rationale,
        loi_anchor_offer=loi_anchor_offer,
        loi_justification=loi_justification,
    )


# ---------------------------------------------------------------------------
# Property lookup helper — pulls from assessor data
# ---------------------------------------------------------------------------

def lookup_property_purchase(
    prop_address: str | None,
    prop_city: str | None,
    prop_name: str | None = None,
) -> tuple[float, int] | None:
    """Try to find the seller's purchase price + year from va_multifamily_inventory.

    Returns (purchase_price, purchase_year) or None if no match.
    """
    if not prop_city or not prop_address:
        return None
    if not ETL_DB.is_file():
        return None

    with sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True) as conn:
        # Try exact address match first
        rows = conn.execute(
            "SELECT last_sale_price, last_sale_date FROM va_multifamily_inventory "
            "WHERE city = ? AND last_sale_price > 100000 AND last_sale_date IS NOT NULL "
            "AND last_sale_date != ''",
            (prop_city,),
        ).fetchall()

    # Address normalization
    import re
    def norm(a: str) -> str:
        if not a:
            return ""
        s = re.sub(r"[#.,]", "", a.upper())
        return " ".join(s.split())[:30]

    prop_norm = norm(prop_address)
    # ... best-effort match. For now, return None and let UI provide manual entry.
    # This is a placeholder; richer matching can be added later.
    return None
