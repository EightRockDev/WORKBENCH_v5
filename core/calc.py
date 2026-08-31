"""Deal-level underwriting math: cap, DSCR, CoC, debt schedule, 5-year cash flow.

Pure functions — no I/O, no Streamlit. Every formula must be reproducible from
inputs alone so `tests/test_calc.py` can pin behavior.

Conventions (locked by Brian on 2026-05-06):
- Amortization is always 25 years (300 months) with optional 0–10 year IO front.
- AM fee is 4% of GPR for years 1..N-1; **$0 in the exit (sale) year**.
- Equity raise (LP raise) is the denominator for CoC and equity multiple.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DebtTerms:
    loan_amount: float
    annual_rate: float       # e.g. 0.06 for 6%
    amort_months: int        # 300 for 25 years (Eight Rock standard)
    io_years: int            # 0..10


@dataclass(frozen=True)
class DebtSchedule:
    """Per-year arrays of length = hold_years.

    `interest`, `principal`, and `ending_balance` are all annual aggregates.
    During IO years, principal == 0 and interest == loan * rate.
    """

    terms: DebtTerms
    annual_payment: list[float]
    interest: list[float]
    principal: list[float]
    ending_balance: list[float]

    def is_io_year(self, year: int) -> bool:
        """1-indexed: year 1 is the first hold year."""
        return year <= self.terms.io_years


# ---------------------------------------------------------------------------
# Debt math
# ---------------------------------------------------------------------------

def amortizing_payment(loan: float, annual_rate: float, amort_months: int) -> float:
    """Standard fully-amortizing monthly payment, returned as an ANNUAL figure.

    Formula:  M = L · r·(1+r)^n / ((1+r)^n − 1),  where r = annual_rate / 12.
    Returns M × 12. Returns 0 for non-positive loan or amort_months.
    Handles the 0% rate edge case (linear principal repayment).
    """
    if loan <= 0 or amort_months <= 0:
        return 0.0
    if annual_rate == 0.0:
        return (loan / amort_months) * 12
    r = annual_rate / 12.0
    n = amort_months
    monthly = loan * r * (1.0 + r) ** n / ((1.0 + r) ** n - 1.0)
    return monthly * 12.0


def build_debt_schedule(terms: DebtTerms, hold_years: int) -> DebtSchedule:
    """Year-by-year amortization with IO at the front.

    During IO months (1..io_years*12): payment = balance × monthly_rate, no principal.
    After IO: re-amortize the original principal over (amort_months − io_months) months.
    The schedule covers exactly `hold_years` years (12 months × hold_years iterations).
    """
    if hold_years <= 0:
        raise ValueError(f"hold_years must be positive, got {hold_years}")

    monthly_rate = terms.annual_rate / 12.0
    io_months = terms.io_years * 12
    amort_remaining = max(terms.amort_months - io_months, 0)

    # Amortizing monthly payment, applied AFTER the IO period
    if terms.loan_amount <= 0 or amort_remaining <= 0:
        am_monthly = 0.0
    elif terms.annual_rate == 0.0:
        am_monthly = terms.loan_amount / amort_remaining
    else:
        r = monthly_rate
        n = amort_remaining
        am_monthly = terms.loan_amount * r * (1.0 + r) ** n / ((1.0 + r) ** n - 1.0)

    annual_payment: list[float] = []
    interest: list[float] = []
    principal: list[float] = []
    ending_balance: list[float] = []

    balance = terms.loan_amount
    for year in range(1, hold_years + 1):
        yr_interest = 0.0
        yr_principal = 0.0
        for month in range(12):
            month_index = (year - 1) * 12 + month  # 0-indexed across the entire hold
            if month_index < io_months:
                # Interest-only month: pay interest on current balance, no principal
                m_interest = balance * monthly_rate
                m_principal = 0.0
            else:
                # Amortizing month: payment is split between interest and principal
                m_interest = balance * monthly_rate
                m_principal = am_monthly - m_interest
                # Don't pay more principal than remaining balance (last-month edge)
                if m_principal > balance:
                    m_principal = balance
                balance -= m_principal
            yr_interest += m_interest
            yr_principal += m_principal

        annual_payment.append(yr_interest + yr_principal)
        interest.append(yr_interest)
        principal.append(yr_principal)
        ending_balance.append(balance)

    return DebtSchedule(
        terms=terms,
        annual_payment=annual_payment,
        interest=interest,
        principal=principal,
        ending_balance=ending_balance,
    )


def loan_balance_at(schedule: DebtSchedule, year: int) -> float:
    """Outstanding principal at the END of `year` (1-indexed)."""
    if year < 1 or year > len(schedule.ending_balance):
        raise ValueError(
            f"year {year} out of range (schedule covers 1..{len(schedule.ending_balance)})"
        )
    return schedule.ending_balance[year - 1]


# ---------------------------------------------------------------------------
# Single-year metrics
# ---------------------------------------------------------------------------

def cap_rate(noi: float, purchase_price: float) -> float:
    """NOI / purchase price. Returns 0 if purchase_price ≤ 0."""
    if purchase_price <= 0:
        return 0.0
    return noi / purchase_price


def dscr(noi_after_am_fee: float, annual_debt_service: float) -> float:
    """(NOI − AM fee) / annual debt service. Returns 0 if no debt service."""
    if annual_debt_service <= 0:
        return 0.0
    return noi_after_am_fee / annual_debt_service


def cash_on_cash(annual_cash_flow: float, equity_raise: float) -> float:
    """Annual CF / LP equity raise. Returns 0 if no equity raised."""
    if equity_raise <= 0:
        return 0.0
    return annual_cash_flow / equity_raise


def return_on_cost(stabilized_noi: float, all_in_basis: float) -> float:
    """Untrended Return on Cost = stabilized NOI ÷ (purchase + capex + closing).

    Beardsley's #1 metric — not manipulable by growth/exit assumptions. Target
    ≥ 8% (= 150–250 bps over market cap). Single most robust quality-of-deal
    indicator because it ignores exit cap and rent growth.
    """
    if all_in_basis <= 0:
        return 0.0
    return stabilized_noi / all_in_basis


def debt_yield(noi: float, loan_amount: float) -> float:
    """Debt Yield = NOI ÷ Loan Amount.

    Lender-side metric — independent of valuation. Lender minimum is typically
    7%. Below 7% means the loan is effectively over-leveraged regardless of
    DSCR (because cap-rate compression could quickly evaporate the LTV cushion).
    """
    if loan_amount <= 0:
        return 0.0
    return noi / loan_amount


def amortized_debt_constant(annual_rate: float, amort_months: int) -> float:
    """Amortizing debt constant = annual P&I payment ÷ loan amount.

    For an amortizing loan, this is what % of the loan balance you pay each
    year as P+I combined. Your in-place yield (NOI ÷ price) MUST be above this
    constant to have positive leverage after the IO period burns off.

    Reference: 5% rate / 25 yr amort ≈ 6.44% constant
                6% rate / 25 yr amort ≈ 7.19% constant
                7% rate / 25 yr amort ≈ 7.98% constant
    """
    if amort_months <= 0 or annual_rate < 0:
        return 0.0
    payment = amortizing_payment(loan=1.0, annual_rate=annual_rate, amort_months=amort_months)
    return payment  # because loan=1.0


def breakeven_occupancy(
    operating_expenses: float,
    annual_debt_service: float,
    total_potential_rent: float,
) -> float:
    """Breakeven occupancy = (opex + debt service) / total potential rent.

    The minimum occupancy needed to cover all costs. Lower is better.
    Eight Rock target: breakeven below 80% (so you can absorb a 10-15%
    occupancy drop without losing money). Above 90% breakeven = thin deal.
    """
    if total_potential_rent <= 0:
        return 0.0
    return (operating_expenses + annual_debt_service) / total_potential_rent


def expense_ratio(operating_expenses: float, egi: float) -> float:
    """Operating expenses ÷ Effective Gross Income.

    Sanity-check ranges (per Murray, MM2):
      < 40%: seller likely under-reporting — investigate aggressively
      40-50%: typical Class A
      50-60%: typical Class B/C (Eight Rock sweet spot)
      > 60%: vintage burn OR opportunity to compress via management

    300+ unit properties typically run 5-10pp lower than smaller properties
    due to staffing leverage.
    """
    if egi <= 0:
        return 0.0
    return operating_expenses / egi


def am_fee_for_year(
    gpr: float,
    year: int,
    hold_years: int,
    am_fee_pct: float,
) -> float:
    """Asset Management fee for a given year.

    Brian's convention: am_fee_pct × GPR for years 1..N-1, **$0 in the sale year**
    (year == hold_years). This matches the Eight Rock waterfall mechanics — the
    GP is already taking the promote on disposition, so no fee on the way out.
    """
    if year >= hold_years:
        return 0.0
    return am_fee_pct * gpr


# ---------------------------------------------------------------------------
# 5-year cash flow projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class YearRow:
    year: int
    is_io: bool
    gpr: float
    vacancy_loss: float
    egi: float
    expenses: float
    noi: float
    am_fee: float
    noi_after_am: float
    debt_service: float
    cash_flow: float
    coc: float
    reno_rent_lift: float = 0.0   # GPR uplift from renovations, inside `gpr`
    reno_capex: float = 0.0       # renovation dollars spent this year


@dataclass(frozen=True)
class CashFlowProjection:
    rows: list[YearRow]
    exit_noi: float                  # NOI grown one more year, fed to exit cap
    exit_proceeds_gross: float       # exit_noi / exit_cap
    exit_loan_payoff: float          # principal balance at end of year N
    exit_proceeds_net: float         # exit_gross − exit_loan_payoff
    final_year_total_distribution: float  # year-N CF + exit_proceeds_net (project-level)
    total_distributions: float       # sum(year CFs) + exit_proceeds_net
    equity_multiple: float           # total_distributions / equity_raise
    reno_total_capex: float = 0.0    # renovation dollars over the hold
    reno_capex_funding: str = "raise"  # 'raise' | 'operations'


@dataclass(frozen=True)
class RenovationPlan:
    """A value-add renovation program, expressed in the terms the cash flow needs.

    Built by `build_renovation_plan` from the three numbers the analyst types
    on the Value-Add CAPEX panel (units/year, cost/unit, monthly rent bump).
    Every field is an ANNUAL dollar figure aligned to hold years 1..N.
    """

    units_by_year: list[int]
    capex_by_year: list[float]        # dollars spent in each hold year
    rent_lift_by_year: list[float]    # annual GPR uplift recognized in each year
    exit_rent_lift: float             # uplift embedded in the year N+1 exit NOI
    total_capex: float
    total_units: int
    cost_per_unit: float
    monthly_rent_bump: float

    @property
    def is_empty(self) -> bool:
        return self.total_units == 0 or self.total_capex <= 0


EMPTY_RENOVATION_PLAN = RenovationPlan(
    units_by_year=[], capex_by_year=[], rent_lift_by_year=[],
    exit_rent_lift=0.0, total_capex=0.0, total_units=0,
    cost_per_unit=0.0, monthly_rent_bump=0.0,
)


def build_renovation_plan(
    *,
    renovations_per_year: list[int] | None,
    cost_per_unit: float,
    monthly_rent_bump: float,
    hold_years: int,
    rent_growth: float = 0.0,
    first_year_factor: float = 0.5,
) -> RenovationPlan:
    """Turn a renovation schedule into per-year CAPEX and GPR uplift.

    Conventions (locked 2026-08-31):
      - A unit renovated in year k earns `first_year_factor` of a full year of
        bumped rent in year k (0.5 = the standard mid-year convention: units
        turn throughout the year), then a full year from k+1 onward.
      - The bump itself grows at `rent_growth` once placed — a renovated unit's
        rent escalates like every other unit.
      - `exit_rent_lift` is the uplift present in the year N+1 NOI the exit cap
        is applied to: every renovated unit, a full year, grown one more year.

    The list is padded/truncated to exactly `hold_years` entries so a hold-period
    change can never index out of range.
    """
    if hold_years <= 0:
        raise ValueError(f"hold_years must be positive, got {hold_years}")
    if not 0.0 <= first_year_factor <= 1.0:
        raise ValueError(
            f"first_year_factor must be in [0, 1], got {first_year_factor}"
        )

    units = [int(u or 0) for u in (renovations_per_year or [])][:hold_years]
    units += [0] * (hold_years - len(units))
    if any(u < 0 for u in units):
        raise ValueError("renovations_per_year cannot contain negative counts")

    cost_per_unit = max(0.0, float(cost_per_unit or 0.0))
    monthly_rent_bump = max(0.0, float(monthly_rent_bump or 0.0))
    annual_per_unit = monthly_rent_bump * 12.0

    capex_by_year = [u * cost_per_unit for u in units]

    rent_lift_by_year: list[float] = []
    for year in range(1, hold_years + 1):
        lift = 0.0
        for k in range(1, year + 1):
            factor = first_year_factor if k == year else 1.0
            lift += (
                units[k - 1] * annual_per_unit * factor
                * (1.0 + rent_growth) ** (year - k)
            )
        rent_lift_by_year.append(lift)

    exit_rent_lift = sum(
        units[k - 1] * annual_per_unit * (1.0 + rent_growth) ** (hold_years + 1 - k)
        for k in range(1, hold_years + 1)
    )

    return RenovationPlan(
        units_by_year=units,
        capex_by_year=capex_by_year,
        rent_lift_by_year=rent_lift_by_year,
        exit_rent_lift=exit_rent_lift,
        total_capex=float(sum(capex_by_year)),
        total_units=int(sum(units)),
        cost_per_unit=cost_per_unit,
        monthly_rent_bump=monthly_rent_bump,
    )


def replan_rent_growth(
    plan: "RenovationPlan | None",
    hold_years: int,
    rent_growth: float,
) -> "RenovationPlan | None":
    """Rebuild a plan's lift under a different rent-growth assumption.

    The lift schedule bakes in the growth rate it was built with, so a
    sweep that flexes rent growth (sensitivity grid, stress overlays) must
    rebuild the plan per cell — otherwise the renovated units' rent grows
    at the base-case rate while everything else is stressed. None in,
    None out.
    """
    if plan is None:
        return None
    return build_renovation_plan(
        renovations_per_year=plan.units_by_year,
        cost_per_unit=plan.cost_per_unit,
        monthly_rent_bump=plan.monthly_rent_bump,
        hold_years=hold_years,
        rent_growth=rent_growth,
    )


def effective_year1_vacancy(
    base_vac: float,
    spike_pp: float,
    stabilization_months: int,
) -> float:
    """Time-weighted average year-1 vacancy when a reposition spike + linear
    ramp back to base_vac is in play.

    Both base_vac and spike_pp are FRACTIONS (e.g. 0.05 + 0.10).

    Math: ramp linearly from (base + spike) at month 0 down to base at month
    stab_months. Average over the first 12 months:
      stab=0:       base (no ramp time, no spike applied)
      stab<=12:     base + spike × (stab/24)
      stab>12:      base + spike × (1 - 6/stab)

    Per Beardsley: 12-mo stab + 10pp spike → year-1 effective +5pp; 24-mo
    stab + 10pp spike → year-1 effective +7.5pp.
    """
    if stabilization_months <= 0 or spike_pp <= 0:
        return base_vac
    if stabilization_months <= 12:
        return base_vac + spike_pp * (stabilization_months / 24.0)
    return base_vac + spike_pp * (1.0 - 6.0 / stabilization_months)


def build_cashflow(
    *,
    year1_gpr: float,
    year1_vacancy_pct: float,
    year1_expenses: float,
    rent_growth: float,
    expense_growth: float,
    am_fee_pct: float,
    debt: DebtSchedule,
    hold_years: int,
    exit_cap: float,
    equity_raise: float,
    stabilized_vacancy_pct: float | None = None,
    stabilization_year_break: int = 1,
    reno: RenovationPlan | None = None,
    reno_capex_funding: str = "raise",
) -> CashFlowProjection:
    """Build the year-by-year cash flow projection for the hold period.

    Year 1 inputs are taken as-is (typically T-12 actuals from sources.json,
    or derived from defaults when no T-12 is uploaded).
    Years 2..N grow GPR by `rent_growth` and expenses by `expense_growth`.
    AM fee = am_fee_pct × GPR for years 1..N-1, $0 in year N (sale year).
    Exit proceeds = NOI(N+1) / exit_cap, less remaining loan balance.
    Equity multiple = (sum of CFs + exit_proceeds_net) / equity_raise.

    Vacancy:
      - `year1_vacancy_pct` applies for years 1..`stabilization_year_break`.
      - `stabilized_vacancy_pct` applies for subsequent years. If None,
        the same `year1_vacancy_pct` is used for every year (backward-compat).

    This split lets us model a reposition disruption: year 1 carries the
    going-in vacancy spike, year 2+ runs at the stabilized rate.

    Renovations (`reno`, from `build_renovation_plan`):
      - `reno.rent_lift_by_year[i]` is ADDED to that year's GPR, so vacancy,
        the AM fee and the expense ratio all see it, and it carries into the
        year N+1 exit NOI via `reno.exit_rent_lift`.
      - `reno_capex_funding` decides where the spend lands:
          'raise'      — escrowed at close. Callers must have already added
                         `reno.total_capex` to `equity_raise`; annual cash
                         flow is untouched. (Eight Rock default.)
          'operations' — funded out of the property's cash flow; each year's
                         CAPEX is deducted from that year's levered cash flow.
        Charging both would double-count the same dollars.
    """
    if hold_years <= 0:
        raise ValueError(f"hold_years must be positive, got {hold_years}")
    if len(debt.annual_payment) < hold_years:
        raise ValueError(
            f"debt schedule covers {len(debt.annual_payment)} years, "
            f"need {hold_years}"
        )
    if reno_capex_funding not in ("raise", "operations"):
        raise ValueError(
            f"reno_capex_funding must be 'raise' or 'operations', "
            f"got {reno_capex_funding!r}"
        )
    if reno is not None and len(reno.capex_by_year) != hold_years:
        raise ValueError(
            f"renovation plan covers {len(reno.capex_by_year)} years, "
            f"need {hold_years} — rebuild it with build_renovation_plan()"
        )

    stab_vac = stabilized_vacancy_pct if stabilized_vacancy_pct is not None else year1_vacancy_pct

    rows: list[YearRow] = []
    gpr = year1_gpr
    expenses = year1_expenses

    for year in range(1, hold_years + 1):
        if year > 1:
            gpr = gpr * (1.0 + rent_growth)
            expenses = expenses * (1.0 + expense_growth)

        # Year-1 vacancy (with reposition spike) for the first
        # `stabilization_year_break` years; stabilized vac thereafter.
        vac_for_year = year1_vacancy_pct if year <= stabilization_year_break else stab_vac

        # Renovation uplift joins GPR, so vacancy and the AM fee both see it.
        reno_lift = reno.rent_lift_by_year[year - 1] if reno else 0.0
        reno_capex = reno.capex_by_year[year - 1] if reno else 0.0
        gpr_total = gpr + reno_lift

        vacancy_loss = gpr_total * vac_for_year
        egi = gpr_total - vacancy_loss
        noi = egi - expenses
        am_fee = am_fee_for_year(gpr_total, year, hold_years, am_fee_pct)
        noi_after_am = noi - am_fee
        debt_service = debt.annual_payment[year - 1]
        cf = noi_after_am - debt_service
        if reno_capex_funding == "operations":
            cf -= reno_capex
        coc = cash_on_cash(cf, equity_raise)

        rows.append(
            YearRow(
                year=year,
                is_io=debt.is_io_year(year),
                gpr=gpr_total,
                vacancy_loss=vacancy_loss,
                egi=egi,
                expenses=expenses,
                noi=noi,
                am_fee=am_fee,
                noi_after_am=noi_after_am,
                debt_service=debt_service,
                cash_flow=cf,
                coc=coc,
                reno_rent_lift=reno_lift,
                reno_capex=reno_capex,
            )
        )

    # Exit-year cap is applied to forward-looking NOI (year N+1) — standard
    # convention. Buyer underwrites to next year's NOI. Use the STABILIZED
    # vacancy (post-reposition) for exit since the buyer takes over after
    # any reposition is complete.
    exit_gpr = gpr * (1.0 + rent_growth) + (reno.exit_rent_lift if reno else 0.0)
    exit_expenses = expenses * (1.0 + expense_growth)
    exit_vac_loss = exit_gpr * stab_vac
    exit_egi = exit_gpr - exit_vac_loss
    exit_noi = exit_egi - exit_expenses

    exit_proceeds_gross = exit_noi / exit_cap if exit_cap > 0 else 0.0
    exit_loan_payoff = loan_balance_at(debt, hold_years)
    exit_proceeds_net = exit_proceeds_gross - exit_loan_payoff

    final_year_total = rows[-1].cash_flow + exit_proceeds_net
    total_dist = sum(r.cash_flow for r in rows) + exit_proceeds_net
    em = total_dist / equity_raise if equity_raise > 0 else 0.0

    return CashFlowProjection(
        rows=rows,
        exit_noi=exit_noi,
        exit_proceeds_gross=exit_proceeds_gross,
        exit_loan_payoff=exit_loan_payoff,
        exit_proceeds_net=exit_proceeds_net,
        final_year_total_distribution=final_year_total,
        total_distributions=total_dist,
        equity_multiple=em,
        reno_total_capex=(reno.total_capex if reno else 0.0),
        reno_capex_funding=reno_capex_funding,
    )
