"""Module E - named stress-test overlays (spec 6.3).

Three named scenarios wired into the same deterministic deal pipeline the
sensitivity grid uses (calc -> waterfall -> IRR). Each overlay perturbs the
deal's own base-case inputs rather than substituting invented absolutes, so a
conservative deal stays conservative under stress and an aggressive one shows
its downside honestly.

Calibration of the shocks (kept deliberately round; sources noted so the bars
can be defended in an IC memo):

  * **2008-style** - GFC multifamily experience: effective rents fell roughly
    5-8% peak-to-trough, national vacancy rose ~2-3 points, and exit cap rates
    decompressed on the order of 100+ bps with transaction markets frozen.
    Overlay: rent growth 0 in years 1-2 then half of base; vacancy +300 bps;
    exit cap +100 bps.
  * **COVID-style** - 2020-21: a shorter shock - collections/bad-debt stress
    and a year of flat rents, but cap rates ultimately COMPRESSED. Overlay:
    year-1 rent growth 0 (base thereafter), vacancy +150 bps, exit cap
    unchanged, year-1 other-loss equivalent modeled as +150 bps extra vacancy.
  * **Insurance shock** - the 2022-24 coastal reality (Hampton Roads is
    coastal): property insurance premiums repriced 30-50%+ in a single
    renewal. Insurance is roughly 5-8% of opex on Class B/C multifamily.
    Overlay: one-time +40% on the insurance share of expenses (modeled as a
    permanent +3% step in total opex) plus expense growth +100 bps.

The verdict tie-in (`stressed_verdict_adjustment`) implements the spec's
"named overlays wired into the sensitivity grids": if the deal's LP IRR under
a named overlay falls below the sensitivity red-flag bar, the exec summary
shows the failure by name next to the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import config
from core.calc import DebtTerms, build_cashflow, build_debt_schedule
from core.irr import lp_irr, project_irr
from core.sensitivity import SensitivityBase
from core.waterfall import run_waterfall


@dataclass(frozen=True)
class StressOverlay:
    """A named scenario as deltas against the deal's own base case."""
    key: str
    label: str
    description: str
    vacancy_delta: float = 0.0            # added to base vacancy (fraction)
    rent_growth_by_year: dict[int, float] | None = None  # year -> absolute rate
    rent_growth_scale: float = 1.0        # applied to base growth in other years
    expense_growth_delta: float = 0.0     # added to base expense growth
    expense_level_shock: float = 0.0      # one-time multiplier on year-1 opex
    exit_cap_delta: float = 0.0           # added to base exit cap (fraction)


OVERLAYS: tuple[StressOverlay, ...] = (
    StressOverlay(
        key="gfc_2008",
        label="2008-style downturn",
        description=("Two years of zero rent growth then half-speed recovery, "
                     "vacancy +300 bps, exit cap +100 bps (GFC multifamily "
                     "experience: rents -5-8%, caps decompressed >100 bps)."),
        vacancy_delta=0.03,
        rent_growth_by_year={1: 0.0, 2: 0.0},
        rent_growth_scale=0.5,
        exit_cap_delta=0.01,
    ),
    StressOverlay(
        key="covid_2020",
        label="COVID-style shock",
        description=("One year of flat rents and collection stress "
                     "(+300 bps effective vacancy in year 1, +150 bps after), "
                     "exit cap unchanged - 2020-21 pattern where caps "
                     "ultimately compressed."),
        vacancy_delta=0.015,
        rent_growth_by_year={1: 0.0},
        rent_growth_scale=1.0,
        # extra year-1 collections stress is folded into vacancy_delta via
        # `year1_extra_vacancy` below
    ),
    StressOverlay(
        key="insurance_shock",
        label="Insurance shock",
        description=("Coastal insurance repricing: +40% on the insurance line "
                     "(about +3% total opex, permanent) and expense growth "
                     "+100 bps - the 2022-24 coastal-market renewal reality."),
        expense_level_shock=0.03,
        expense_growth_delta=0.01,
    ),
)

# COVID overlay: additional year-1-only vacancy standing in for bad debt /
# concession stress. Kept out of the dataclass to keep it serializable-simple.
_COVID_YEAR1_EXTRA_VACANCY = 0.015


@dataclass(frozen=True)
class StressResult:
    overlay: StressOverlay
    project_irr: float | None
    lp_irr: float | None
    coc_year1: float
    dscr_year1: float | None
    failed: bool                      # LP IRR below the sensitivity flag bar
    base_lp_irr: float | None
    lp_irr_delta: float | None        # stressed minus base


@dataclass(frozen=True)
class StressReport:
    results: list[StressResult] = field(default_factory=list)

    @property
    def failures(self) -> list[StressResult]:
        return [r for r in self.results if r.failed]

    @property
    def any_failed(self) -> bool:
        return bool(self.failures)


def _year_rates(overlay: StressOverlay, base_growth: float, hold_years: int) -> list[float]:
    """Per-year rent growth under the overlay."""
    named = overlay.rent_growth_by_year or {}
    return [named.get(year, base_growth * overlay.rent_growth_scale)
            for year in range(1, hold_years + 1)]


def _run_case(base: SensitivityBase, *, vacancy: float, rent_growth: float,
              expense_growth: float, year1_expenses: float, exit_cap: float,
              debt_schedule) -> tuple[float | None, float | None, float, float | None]:
    """One pipeline run; returns (project_irr, lp_irr, year-1 CoC, year-1 DSCR)."""
    cf = build_cashflow(
        year1_gpr=base.year1_gpr,
        year1_vacancy_pct=vacancy,
        year1_expenses=year1_expenses,
        rent_growth=rent_growth,
        expense_growth=expense_growth,
        am_fee_pct=base.am_fee_pct,
        debt=debt_schedule,
        hold_years=base.hold_years,
        exit_cap=exit_cap,
        equity_raise=base.equity_raise,
    )
    annual_pots = [r.cash_flow for r in cf.rows[:-1]]
    annual_pots.append(cf.rows[-1].cash_flow + cf.exit_proceeds_net)
    wf = run_waterfall(equity_raise=base.equity_raise, annual_pots=annual_pots)
    year1 = cf.rows[0]
    # DSCR on NOI after AM fee, matching core.calc.dscr's convention.
    dscr = (year1.noi_after_am / year1.debt_service) if year1.debt_service > 0 else None
    return (
        project_irr(equity_raise=base.equity_raise,
                    annual_cashflows=[r.cash_flow for r in cf.rows],
                    exit_proceeds_net=cf.exit_proceeds_net),
        lp_irr(wf.lp_cashflows),
        year1.coc,
        dscr,
    )


def run_stress_overlays(
    base: SensitivityBase,
    *,
    base_vacancy: float = config.VACANCY_DEFAULT,
    base_rent_growth: float = config.RENT_GROWTH_DEFAULT,
    base_expense_growth: float = config.EXPENSE_GROWTH_DEFAULT,
    overlays: tuple[StressOverlay, ...] = OVERLAYS,
) -> StressReport:
    """Run every named overlay against the deal.

    A multi-year overlay needs per-year growth rates, but `build_cashflow`
    takes a single rate - so overlay years are approximated by the geometric
    mean of the per-year rates, which preserves the terminal rent level (what
    the exit value and late-year cash flows depend on).
    """
    debt_terms = DebtTerms(loan_amount=base.loan_amount, annual_rate=base.annual_rate,
                           amort_months=base.amort_months, io_years=base.io_years)
    debt_schedule = build_debt_schedule(debt_terms, base.hold_years)

    base_proj, base_lp, _base_coc, _ = _run_case(
        base, vacancy=base_vacancy, rent_growth=base_rent_growth,
        expense_growth=base_expense_growth, year1_expenses=base.year1_expenses,
        exit_cap=base.exit_cap, debt_schedule=debt_schedule)

    results: list[StressResult] = []
    for overlay in overlays:
        rates = _year_rates(overlay, base_rent_growth, base.hold_years)
        # Geometric mean preserves terminal rent: prod(1+r_i)^(1/n) - 1.
        growth_factor = 1.0
        for r in rates:
            growth_factor *= (1.0 + r)
        effective_growth = growth_factor ** (1.0 / max(1, len(rates))) - 1.0

        vacancy = min(0.95, base_vacancy + overlay.vacancy_delta
                      + (_COVID_YEAR1_EXTRA_VACANCY if overlay.key == "covid_2020" else 0.0))
        proj, lp, coc, dscr = _run_case(
            base,
            vacancy=vacancy,
            rent_growth=effective_growth,
            expense_growth=base_expense_growth + overlay.expense_growth_delta,
            year1_expenses=base.year1_expenses * (1.0 + overlay.expense_level_shock),
            exit_cap=base.exit_cap + overlay.exit_cap_delta,
            debt_schedule=debt_schedule,
        )
        # An incomputable LP IRR under stress means LPs never get their money
        # back at all (no sign change in the cashflows) - that is the worst
        # possible outcome, not a pass.
        failed = lp is None or lp < config.SENSITIVITY_LP_IRR_FLAG
        results.append(StressResult(
            overlay=overlay, project_irr=proj, lp_irr=lp, coc_year1=coc,
            dscr_year1=dscr, failed=failed, base_lp_irr=base_lp,
            lp_irr_delta=(lp - base_lp) if (lp is not None and base_lp is not None) else None,
        ))
    return StressReport(results=results)


def stress_rationale(report: StressReport) -> list[str]:
    """Rationale lines for the verdict panel - named failures only."""
    lines: list[str] = []
    for r in report.failures:
        if r.lp_irr is None:
            lines.append(
                f"Stress overlay '{r.overlay.label}': LP capital is not "
                "returned (IRR undefined) - fails the "
                f"{config.SENSITIVITY_LP_IRR_FLAG:.0%} downside bar")
        else:
            lines.append(
                f"Stress overlay '{r.overlay.label}': LP IRR {r.lp_irr:.1%} "
                f"falls below the {config.SENSITIVITY_LP_IRR_FLAG:.0%} downside bar")
    return lines
