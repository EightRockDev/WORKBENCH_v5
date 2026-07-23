"""Sensitivity matrix: vacancy × rent growth × expense growth.

Per `SUMMARY-FORMAT.md` (Hampton Roads exec summary spec):
- Vacancy:        5%, 7%, 10%
- Rent growth:    2%, 3.5%, 5%
- Expense growth: conservative (2.5%), aggressive (4.5%)

For each combination, re-run the deal: build CF → run waterfall → compute
project IRR, LP IRR, year-1 CoC. Any cell where LP IRR drops below the
12% downside threshold is flagged as a red flag (`SENSITIVITY_LP_IRR_FLAG`
in config).

This module is the integration point between calc + waterfall + irr — it's
the most expensive operation per deal (18 cell evaluations), so we build the
debt schedule once and reuse it across cells (debt service doesn't depend on
the three flexed variables).
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from core.calc import DebtTerms, build_cashflow, build_debt_schedule
from core.irr import lp_irr, project_irr
from core.waterfall import run_waterfall


@dataclass(frozen=True)
class SensitivityBase:
    """Fixed deal inputs that don't flex across the sensitivity grid.

    Vacancy, rent growth, and expense growth are NOT included — those are
    the variables the grid sweeps. Year-1 GPR and year-1 expenses come from
    `sources.json` (T-12 actuals when present, otherwise derived defaults).
    """
    # Deal economics
    purchase_price: float
    year1_gpr: float
    year1_expenses: float
    am_fee_pct: float

    # Debt
    loan_amount: float
    annual_rate: float
    amort_months: int
    io_years: int

    # Hold + exit
    hold_years: int
    exit_cap: float

    # Equity (LP raise — denominator for CoC, IRR, EM)
    equity_raise: float


@dataclass(frozen=True)
class SensitivityCell:
    """One result in the 18-cell grid."""
    vacancy: float                 # fraction (e.g. 0.07 for 7%)
    rent_growth: float             # fraction
    expense_growth: float          # fraction
    expense_growth_label: str      # 'conservative' | 'aggressive'
    project_irr: float | None
    lp_irr: float | None
    coc_year1: float
    flagged: bool                  # LP IRR < SENSITIVITY_LP_IRR_FLAG


@dataclass(frozen=True)
class SensitivityGrid:
    cells: list[SensitivityCell]
    flagged_count: int


def _evaluate_cell(
    base: SensitivityBase,
    debt_schedule,
    vacancy: float,
    rent_growth: float,
    expense_growth: float,
    expense_growth_label: str,
) -> SensitivityCell:
    """Run the full deal pipeline for one cell of the sensitivity grid."""
    cf = build_cashflow(
        year1_gpr=base.year1_gpr,
        year1_vacancy_pct=vacancy,
        year1_expenses=base.year1_expenses,
        rent_growth=rent_growth,
        expense_growth=expense_growth,
        am_fee_pct=base.am_fee_pct,
        debt=debt_schedule,
        hold_years=base.hold_years,
        exit_cap=base.exit_cap,
        equity_raise=base.equity_raise,
    )

    # Assemble waterfall pots: years 1..N-1 = operating CF, year N = combined
    # (operating + net sale proceeds). Matches Brian's confirmed sale-year
    # ordering in `feedback_underwriting_conventions.md`.
    annual_pots = [r.cash_flow for r in cf.rows[:-1]]
    annual_pots.append(cf.rows[-1].cash_flow + cf.exit_proceeds_net)

    wf = run_waterfall(
        equity_raise=base.equity_raise,
        annual_pots=annual_pots,
    )
    lp_rate = lp_irr(wf.lp_cashflows)
    proj_rate = project_irr(
        equity_raise=base.equity_raise,
        annual_cashflows=[r.cash_flow for r in cf.rows],
        exit_proceeds_net=cf.exit_proceeds_net,
    )

    flagged = lp_rate is not None and lp_rate < config.SENSITIVITY_LP_IRR_FLAG

    return SensitivityCell(
        vacancy=vacancy,
        rent_growth=rent_growth,
        expense_growth=expense_growth,
        expense_growth_label=expense_growth_label,
        project_irr=proj_rate,
        lp_irr=lp_rate,
        coc_year1=cf.rows[0].coc,
        flagged=flagged,
    )


def build_sensitivity(base: SensitivityBase) -> SensitivityGrid:
    """Run the full 3×3×2 sensitivity grid.

    Iterates vacancy × rent growth × expense growth in the order specified
    in config (so cell index 0 is the lowest-vacancy / lowest-growth corner).
    Debt schedule is computed once and reused — none of the flexed variables
    affect debt service.
    """
    if base.hold_years <= 0:
        raise ValueError(f"hold_years must be positive, got {base.hold_years}")

    debt_terms = DebtTerms(
        loan_amount=base.loan_amount,
        annual_rate=base.annual_rate,
        amort_months=base.amort_months,
        io_years=base.io_years,
    )
    debt_schedule = build_debt_schedule(debt_terms, base.hold_years)

    cells: list[SensitivityCell] = []
    for vacancy in config.SENSITIVITY_VACANCIES:
        for rent_growth in config.SENSITIVITY_RENT_GROWTHS:
            for label, expense_growth in config.SENSITIVITY_EXPENSE_GROWTHS.items():
                cells.append(
                    _evaluate_cell(
                        base,
                        debt_schedule,
                        vacancy=vacancy,
                        rent_growth=rent_growth,
                        expense_growth=expense_growth,
                        expense_growth_label=label,
                    )
                )

    flagged_count = sum(1 for c in cells if c.flagged)
    return SensitivityGrid(cells=cells, flagged_count=flagged_count)
