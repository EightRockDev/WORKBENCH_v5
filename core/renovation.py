"""Measure a value-add renovation program with the returns engine itself.

The Value-Add CAPEX panel used to compute its own closed-form headline
(`value_at_exit / total_capex`), which reduced to a per-unit ratio — the
unit count cancelled, so the tile read $2.30 whether the schedule covered
2 units or 200 (owner repro, Forrest Pines 2026-08-31). This module replaces
that math with the only measurement that cannot disagree with the header
tiles: run the deal twice through `build_cashflow` — once with the plan,
once without — and report the difference.

Conventions:
  - IRR is measured on PROJECT equity (LP raise + GP fee), matching the
    header tile and `_render_metrics`.
  - `profit_delta` is funding-mode invariant: escrowing CAPEX at close vs
    paying it from cash flow changes *timing* (and therefore IRR), never
    total profit. That invariance is the correctness check the tests pin.
  - `irr_delta` may legitimately be NEGATIVE — a program returning ~1.7x
    over 5 years dilutes a deal already compounding faster than that.
    Report it as it computes; never clamp or hide it.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.calc import (
    CashFlowProjection,
    DebtSchedule,
    RenovationPlan,
    build_cashflow,
)
from core.irr import project_irr


@dataclass(frozen=True)
class RenovationImpact:
    """The renovation program's effect on the deal, engine-measured."""

    plan: RenovationPlan
    with_reno: CashFlowProjection
    without_reno: CashFlowProjection
    total_capex: float
    equity_with: float               # LP raise including escrowed CAPEX
    equity_without: float
    irr_with: float | None           # project IRR (raise + GP fee basis)
    irr_without: float | None
    irr_delta: float | None          # with − without; None if either is None
    em_with: float                   # equity multiple on the LP raise
    em_without: float
    em_delta: float
    exit_value_delta: float          # gross sale value added at the exit cap
    profit_delta: float              # (dist − equity) with, minus same without
    profit_per_capex_dollar: float   # profit_delta / total_capex; 0 if no CAPEX

    @property
    def is_empty(self) -> bool:
        return self.plan.is_empty


def renovation_impact(
    *, plan: RenovationPlan, capex_funding: str,
    equity_without_reno: float, project_equity_without_reno: float,
    year1_gpr: float, year1_vacancy_pct: float, year1_expenses: float,
    rent_growth: float, expense_growth: float, am_fee_pct: float,
    debt: DebtSchedule, hold_years: int, exit_cap: float,
    stabilized_vacancy_pct: float | None = None,
    stabilization_year_break: int = 1,
) -> RenovationImpact:
    """Run the deal with and without the plan; report the difference.

    `equity_without_reno` / `project_equity_without_reno` are the deal's
    raise figures EXCLUDING renovation CAPEX; under 'raise' funding this
    function adds `plan.total_capex` to both, matching what
    `DealState.tracked_raise` does for the live deal.
    """
    escrow = plan.total_capex if capex_funding == "raise" else 0.0
    equity_with = equity_without_reno + escrow
    project_with = project_equity_without_reno + escrow

    common = dict(
        year1_gpr=year1_gpr,
        year1_vacancy_pct=year1_vacancy_pct,
        year1_expenses=year1_expenses,
        rent_growth=rent_growth,
        expense_growth=expense_growth,
        am_fee_pct=am_fee_pct,
        debt=debt,
        hold_years=hold_years,
        exit_cap=exit_cap,
        stabilized_vacancy_pct=stabilized_vacancy_pct,
        stabilization_year_break=stabilization_year_break,
    )

    cf_with = build_cashflow(
        equity_raise=equity_with,
        reno=plan,
        reno_capex_funding=capex_funding,
        **common,
    )
    cf_without = build_cashflow(
        equity_raise=equity_without_reno,
        reno=None,
        **common,
    )

    irr_with = project_irr(
        equity_raise=project_with,
        annual_cashflows=[r.cash_flow for r in cf_with.rows],
        exit_proceeds_net=cf_with.exit_proceeds_net,
    )
    irr_without = project_irr(
        equity_raise=project_equity_without_reno,
        annual_cashflows=[r.cash_flow for r in cf_without.rows],
        exit_proceeds_net=cf_without.exit_proceeds_net,
    )
    irr_delta = (
        irr_with - irr_without
        if irr_with is not None and irr_without is not None
        else None
    )

    profit_delta = (
        (cf_with.total_distributions - equity_with)
        - (cf_without.total_distributions - equity_without_reno)
    )

    return RenovationImpact(
        plan=plan,
        with_reno=cf_with,
        without_reno=cf_without,
        total_capex=plan.total_capex,
        equity_with=equity_with,
        equity_without=equity_without_reno,
        irr_with=irr_with,
        irr_without=irr_without,
        irr_delta=irr_delta,
        em_with=cf_with.equity_multiple,
        em_without=cf_without.equity_multiple,
        em_delta=cf_with.equity_multiple - cf_without.equity_multiple,
        exit_value_delta=(
            cf_with.exit_proceeds_gross - cf_without.exit_proceeds_gross
        ),
        profit_delta=profit_delta,
        profit_per_capex_dollar=(
            profit_delta / plan.total_capex if plan.total_capex > 0 else 0.0
        ),
    )
