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
    """
    if hold_years <= 0:
        raise ValueError(f"hold_years must be positive, got {hold_years}")
    if len(debt.annual_payment) < hold_years:
        raise ValueError(
            f"debt schedule covers {len(debt.annual_payment)} years, "
            f"need {hold_years}"
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
        vacancy_loss = gpr * vac_for_year
        egi = gpr - vacancy_loss
        noi = egi - expenses
        am_fee = am_fee_for_year(gpr, year, hold_years, am_fee_pct)
        noi_after_am = noi - am_fee
        debt_service = debt.annual_payment[year - 1]
        cf = noi_after_am - debt_service
        coc = cash_on_cash(cf, equity_raise)

        rows.append(
            YearRow(
                year=year,
                is_io=debt.is_io_year(year),
                gpr=gpr,
                vacancy_loss=vacancy_loss,
                egi=egi,
                expenses=expenses,
                noi=noi,
                am_fee=am_fee,
                noi_after_am=noi_after_am,
                debt_service=debt_service,
                cash_flow=cf,
                coc=coc,
            )
        )

    # Exit-year cap is applied to forward-looking NOI (year N+1) — standard
    # convention. Buyer underwrites to next year's NOI. Use the STABILIZED
    # vacancy (post-reposition) for exit since the buyer takes over after
    # any reposition is complete.
    exit_gpr = gpr * (1.0 + rent_growth)
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
    )
