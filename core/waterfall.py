"""3-tier waterfall: 8% pref → ROC → 70/30 promote.

Convention (locked by Brian on 2026-05-06; see memory file
`feedback_underwriting_conventions.md`):

- LPs fund 100% of equity. No GP co-invest.
- Pref is **cumulative, non-compounded**, on **unreturned LP capital**.
  Pref does not earn pref. The accrual base shrinks as ROC pays down.
- Sale-year operating CF and net sale proceeds are combined into a single
  pot that runs through all three tiers in sequence.

Per-year algorithm:
    1. Accrue pref:     pref_accrued = pref_rate × lp_capital_remaining_at_start
    2. Pay pref:        pref_paid = min(pot, carryforward + pref_accrued)
                        unpaid → next year's carryforward
    3. Pay ROC:         roc_paid = min(remaining_pot, lp_capital_remaining)
                        lp_capital_remaining decreases for next year's pref base
    4. Split residual:  lp gets `lp_split` × residual, gp gets `gp_split` × residual

Per-year LP distribution = pref_paid + roc_paid + (lp_split × residual)
Per-year GP distribution = gp_split × residual
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class WaterfallYear:
    """One row of the waterfall schedule."""

    year: int                       # 1-indexed
    pot: float                      # cash entering the waterfall this year
    pref_owed_start: float          # carryforward balance at start of year
    pref_accrued_this_year: float   # pref_rate × lp_capital_remaining_at_start
    pref_paid: float                # actually paid out of `pot`
    pref_owed_end: float            # carryforward to next year
    roc_paid: float                 # return of LP capital this year
    lp_capital_remaining_end: float
    residual: float                 # what flowed into the 70/30 split
    lp_distribution: float          # pref_paid + roc_paid + lp_split × residual
    gp_distribution: float          # gp_split × residual


@dataclass(frozen=True)
class WaterfallResult:
    years: list[WaterfallYear]
    lp_cashflows: list[float]   # length = N+1: [-equity_raise, lp_yr1, ..., lp_yrN]
    gp_cashflows: list[float]   # length = N+1: [0,             gp_yr1, ..., gp_yrN]
    total_lp_distributions: float   # sum of LP distributions across all years
    total_gp_distributions: float   # sum of GP distributions across all years


def run_waterfall(
    *,
    equity_raise: float,
    annual_pots: list[float],
    pref_rate: float = config.LP_PREF,
    lp_split: float = config.LP_RESIDUAL_SPLIT,
    gp_split: float = config.GP_RESIDUAL_SPLIT,
) -> WaterfallResult:
    """Run the 3-tier waterfall over a hold period.

    `annual_pots[i]` is the cash available in year (i+1):
      - For years 1..N-1: the operating cash flow for that year.
      - For year N: operating cash flow PLUS net sale proceeds (combined
        per Brian's confirmed sale-year ordering — single pot through all
        three tiers).

    Args:
        equity_raise: Total LP capital invested at year 0.
        annual_pots: Per-year cash pots (length N = hold period).
        pref_rate: Annual pref rate (default 8% per config.LP_PREF).
        lp_split: LP share of Tier 3 residual (default 0.70).
        gp_split: GP share of Tier 3 residual (default 0.30).

    Returns:
        WaterfallResult with year-by-year breakdown and IRR-ready CF vectors.
    """
    if equity_raise <= 0:
        raise ValueError(f"equity_raise must be positive, got {equity_raise}")
    if not annual_pots:
        raise ValueError("annual_pots cannot be empty")
    if abs((lp_split + gp_split) - 1.0) > 1e-9:
        raise ValueError(
            f"lp_split + gp_split must equal 1.0, got {lp_split + gp_split}"
        )

    years: list[WaterfallYear] = []
    lp_capital_remaining = equity_raise
    pref_carryforward = 0.0

    lp_cashflows: list[float] = [-equity_raise]
    gp_cashflows: list[float] = [0.0]
    total_lp = 0.0
    total_gp = 0.0

    for i, pot in enumerate(annual_pots, start=1):
        pot = max(pot, 0.0)  # negative pots zero out — debt covers shortfall

        # 1. Accrue pref on START-of-year unreturned capital balance.
        #    Non-compounded: pref base is just unreturned principal, NOT
        #    principal + accrued unpaid pref.
        pref_accrued = pref_rate * lp_capital_remaining
        pref_owed_start = pref_carryforward
        pref_total_owed = pref_carryforward + pref_accrued

        # 2. Pay pref out of pot. Unpaid amount carries forward (no compounding).
        pref_paid = min(pot, pref_total_owed)
        remaining = pot - pref_paid
        pref_carryforward = pref_total_owed - pref_paid

        # 3. Pay ROC out of what's left. Reduces LP capital → reduces next
        #    year's pref accrual base.
        roc_paid = min(remaining, lp_capital_remaining)
        remaining -= roc_paid
        lp_capital_remaining -= roc_paid

        # 4. Split residual between LP and GP per the promote.
        residual = remaining
        lp_from_residual = residual * lp_split
        gp_from_residual = residual * gp_split

        lp_dist = pref_paid + roc_paid + lp_from_residual
        gp_dist = gp_from_residual

        years.append(
            WaterfallYear(
                year=i,
                pot=pot,
                pref_owed_start=pref_owed_start,
                pref_accrued_this_year=pref_accrued,
                pref_paid=pref_paid,
                pref_owed_end=pref_carryforward,
                roc_paid=roc_paid,
                lp_capital_remaining_end=lp_capital_remaining,
                residual=residual,
                lp_distribution=lp_dist,
                gp_distribution=gp_dist,
            )
        )

        lp_cashflows.append(lp_dist)
        gp_cashflows.append(gp_dist)
        total_lp += lp_dist
        total_gp += gp_dist

    return WaterfallResult(
        years=years,
        lp_cashflows=lp_cashflows,
        gp_cashflows=gp_cashflows,
        total_lp_distributions=total_lp,
        total_gp_distributions=total_gp,
    )
