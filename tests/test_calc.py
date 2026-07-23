"""Tests for core.calc — deal-level underwriting math.

Strategy: known-answer tests with values cross-checked against Excel's PMT/PV
or hand math. Each formula gets at least one test pinning expected behavior
plus edge cases (zero values, terminal year semantics, IO front-loading).
"""

from __future__ import annotations

import pytest

import config
from core.calc import (
    DebtTerms,
    am_fee_for_year,
    amortizing_payment,
    build_cashflow,
    build_debt_schedule,
    cap_rate,
    cash_on_cash,
    dscr,
    loan_balance_at,
)

# ---------------------------------------------------------------------------
# Single-year metrics
# ---------------------------------------------------------------------------


def test_cap_rate_basic():
    # $1M NOI / $14M price = 7.14%
    assert cap_rate(1_000_000, 14_000_000) == pytest.approx(0.07142857, rel=1e-5)


def test_cap_rate_zero_price_returns_zero():
    # No purchase price → can't compute; return 0 rather than div-by-zero error
    assert cap_rate(1_000_000, 0) == 0.0
    assert cap_rate(1_000_000, -1) == 0.0


def test_dscr_basic():
    # $1M NOI / $700k debt service = 1.43x
    assert dscr(1_000_000, 700_000) == pytest.approx(1.4286, rel=1e-3)


def test_dscr_zero_debt_returns_zero():
    assert dscr(1_000_000, 0) == 0.0


def test_dscr_uses_post_am_fee_noi():
    # The convention: numerator is NOI AFTER AM fee (not raw NOI).
    # Caller must pass noi_after_am — we just divide.
    noi_after_am = 1_000_000 - 40_000  # $1M NOI minus 4% AM fee on $1M GPR
    assert dscr(noi_after_am, 700_000) == pytest.approx(1.3714, rel=1e-3)


def test_cash_on_cash_basic():
    # $300k CF / $5M equity = 6.0%
    assert cash_on_cash(300_000, 5_000_000) == pytest.approx(0.06)


def test_cash_on_cash_zero_equity_returns_zero():
    assert cash_on_cash(300_000, 0) == 0.0


# ---------------------------------------------------------------------------
# AM fee — the "zero in exit year" rule
# ---------------------------------------------------------------------------


def test_am_fee_default_4pct():
    # $5M GPR, year 1 of 5-year hold → 4% × $5M = $200k
    assert am_fee_for_year(5_000_000, year=1, hold_years=5, am_fee_pct=0.04) == 200_000


def test_am_fee_zero_in_exit_year():
    # Year N (sale year) gets $0 AM fee
    assert am_fee_for_year(5_000_000, year=5, hold_years=5, am_fee_pct=0.04) == 0.0


def test_am_fee_zero_when_pct_is_zero():
    assert am_fee_for_year(5_000_000, year=2, hold_years=5, am_fee_pct=0.0) == 0.0


def test_am_fee_correct_for_all_pre_exit_years():
    # 5-year hold: years 1, 2, 3, 4 charge fee; year 5 does not
    for y in range(1, 5):
        assert am_fee_for_year(1_000_000, y, 5, 0.04) == 40_000
    assert am_fee_for_year(1_000_000, 5, 5, 0.04) == 0.0


# ---------------------------------------------------------------------------
# Amortizing payment formula
# ---------------------------------------------------------------------------


def test_amortizing_payment_known_value_25yr_6pct():
    # $1M loan, 6% annual, 300 months = 25 years.
    # Excel: =PMT(0.06/12, 300, -1000000) = $6,443.014  → annual = $77,316.17
    annual = amortizing_payment(1_000_000, 0.06, 300)
    assert annual == pytest.approx(77_316.17, abs=1.0)


def test_amortizing_payment_zero_rate():
    # 0% rate → straight-line principal: $300k / 300mo = $1,000/mo = $12k/yr
    annual = amortizing_payment(300_000, 0.0, 300)
    assert annual == pytest.approx(12_000.0)


def test_amortizing_payment_zero_loan():
    assert amortizing_payment(0, 0.06, 300) == 0.0


def test_amortizing_payment_zero_months():
    assert amortizing_payment(1_000_000, 0.06, 0) == 0.0


# ---------------------------------------------------------------------------
# Debt schedule — IO + amortization sequencing
# ---------------------------------------------------------------------------


def test_debt_schedule_full_amortization_no_io():
    """25-year amort, 5-year hold, no IO. Balance must decline year over year."""
    terms = DebtTerms(
        loan_amount=10_000_000,
        annual_rate=0.06,
        amort_months=300,
        io_years=0,
    )
    sched = build_debt_schedule(terms, hold_years=5)
    assert len(sched.annual_payment) == 5
    assert len(sched.ending_balance) == 5

    # Each year's payment ≈ same (fully amortizing → constant payment)
    for ap in sched.annual_payment:
        assert ap == pytest.approx(773_161.66, abs=2.0)

    # Balance must strictly decrease year over year
    balances = sched.ending_balance
    for i in range(1, len(balances)):
        assert balances[i] < balances[i - 1], f"Year {i+1} balance not declining"

    # Year 1 ending balance: ~$9.86M (small principal paid early in amort)
    assert balances[0] < 10_000_000
    assert balances[0] > 9_800_000


def test_debt_schedule_io_period_balance_unchanged():
    """3 IO years: balance at end of year 1, 2, 3 must equal original loan."""
    terms = DebtTerms(
        loan_amount=10_000_000,
        annual_rate=0.06,
        amort_months=300,
        io_years=3,
    )
    sched = build_debt_schedule(terms, hold_years=5)
    # Years 1-3 are IO → balance unchanged
    for y in range(3):
        assert sched.ending_balance[y] == pytest.approx(10_000_000, abs=0.01)
        assert sched.principal[y] == pytest.approx(0.0, abs=0.01)
        assert sched.interest[y] == pytest.approx(600_000.0, abs=0.01)  # 10M × 6%
    # Year 4 starts amortizing → balance drops
    assert sched.ending_balance[3] < 10_000_000


def test_debt_schedule_io_year_helper():
    terms = DebtTerms(loan_amount=1_000_000, annual_rate=0.06, amort_months=300, io_years=2)
    sched = build_debt_schedule(terms, hold_years=5)
    assert sched.is_io_year(1) is True
    assert sched.is_io_year(2) is True
    assert sched.is_io_year(3) is False
    assert sched.is_io_year(5) is False


def test_debt_schedule_zero_loan():
    """All-cash deal: every payment is 0, balance is 0."""
    terms = DebtTerms(loan_amount=0, annual_rate=0.06, amort_months=300, io_years=0)
    sched = build_debt_schedule(terms, hold_years=5)
    assert all(ap == 0 for ap in sched.annual_payment)
    assert all(bal == 0 for bal in sched.ending_balance)


def test_loan_balance_at_returns_year_end_balance():
    terms = DebtTerms(loan_amount=10_000_000, annual_rate=0.06, amort_months=300, io_years=0)
    sched = build_debt_schedule(terms, hold_years=5)
    # Year 5 balance via helper == last entry in array
    assert loan_balance_at(sched, 5) == sched.ending_balance[4]


def test_loan_balance_at_out_of_range_raises():
    terms = DebtTerms(loan_amount=1_000_000, annual_rate=0.06, amort_months=300, io_years=0)
    sched = build_debt_schedule(terms, hold_years=5)
    with pytest.raises(ValueError):
        loan_balance_at(sched, 0)
    with pytest.raises(ValueError):
        loan_balance_at(sched, 6)


# ---------------------------------------------------------------------------
# Full 5-year CF builder — Dove Landing-style fixture
# ---------------------------------------------------------------------------


def _dove_landing_debt() -> DebtTerms:
    """Dove Landing: $46.3M asking, 30% down → $32.41M loan at 6.5%."""
    return DebtTerms(
        loan_amount=32_410_000,
        annual_rate=0.065,
        amort_months=config.AMORT_MONTHS,
        io_years=0,
    )


def test_build_cashflow_year_count_and_growth():
    """5-year hold produces 5 rows; GPR and expenses grow at the configured rate."""
    debt = build_debt_schedule(_dove_landing_debt(), hold_years=5)
    cf = build_cashflow(
        year1_gpr=4_879_921,        # Dove T-12 EGI rounded; treat as GPR for fixture
        year1_vacancy_pct=0.07,
        year1_expenses=1_440_479,   # T-12 OpEx
        rent_growth=0.03,
        expense_growth=0.03,
        am_fee_pct=0.04,
        debt=debt,
        hold_years=5,
        exit_cap=0.075,
        equity_raise=13_890_000,    # 30% of $46.3M
    )
    assert len(cf.rows) == 5
    # Year-over-year GPR growth
    assert cf.rows[1].gpr == pytest.approx(cf.rows[0].gpr * 1.03)
    assert cf.rows[4].gpr == pytest.approx(cf.rows[0].gpr * (1.03 ** 4))
    # Year-over-year expense growth
    assert cf.rows[1].expenses == pytest.approx(cf.rows[0].expenses * 1.03)


def test_build_cashflow_am_fee_only_in_pre_exit_years():
    debt = build_debt_schedule(_dove_landing_debt(), hold_years=5)
    cf = build_cashflow(
        year1_gpr=5_000_000, year1_vacancy_pct=0.07, year1_expenses=2_000_000,
        rent_growth=0.03, expense_growth=0.03, am_fee_pct=0.04,
        debt=debt, hold_years=5, exit_cap=0.075, equity_raise=10_000_000,
    )
    # Years 1-4 charge AM fee
    for y in range(4):
        expected = cf.rows[y].gpr * 0.04
        assert cf.rows[y].am_fee == pytest.approx(expected)
    # Year 5 (sale year) charges $0
    assert cf.rows[4].am_fee == 0.0


def test_build_cashflow_exit_uses_forward_noi():
    """Exit cap is applied to NOI grown one year past hold (standard convention)."""
    debt = build_debt_schedule(_dove_landing_debt(), hold_years=5)
    cf = build_cashflow(
        year1_gpr=5_000_000, year1_vacancy_pct=0.07, year1_expenses=2_000_000,
        rent_growth=0.03, expense_growth=0.03, am_fee_pct=0.04,
        debt=debt, hold_years=5, exit_cap=0.075, equity_raise=10_000_000,
    )
    # Manually compute year-6 NOI: GPR(5) × 1.03 × (1-0.07) − Expenses(5) × 1.03
    yr5 = cf.rows[4]
    expected_yr6_gpr = yr5.gpr * 1.03
    expected_yr6_expenses = yr5.expenses * 1.03
    expected_yr6_noi = expected_yr6_gpr * (1 - 0.07) - expected_yr6_expenses
    assert cf.exit_noi == pytest.approx(expected_yr6_noi, rel=1e-9)
    # Exit gross = year-6 NOI / exit cap
    assert cf.exit_proceeds_gross == pytest.approx(expected_yr6_noi / 0.075, rel=1e-9)


def test_build_cashflow_equity_multiple_basics():
    """EM = (sum of yearly CFs + net exit proceeds) / equity raise."""
    debt = build_debt_schedule(_dove_landing_debt(), hold_years=5)
    cf = build_cashflow(
        year1_gpr=5_000_000, year1_vacancy_pct=0.07, year1_expenses=2_000_000,
        rent_growth=0.03, expense_growth=0.03, am_fee_pct=0.04,
        debt=debt, hold_years=5, exit_cap=0.075, equity_raise=10_000_000,
    )
    # Sanity: EM should be > 1 for a viable deal
    assert cf.equity_multiple > 1.0
    # Cross-check: total dist / equity_raise
    expected_em = cf.total_distributions / 10_000_000
    assert cf.equity_multiple == pytest.approx(expected_em)


def test_build_cashflow_with_io_year1_cf_higher():
    """During IO years debt service is lower → year-1 CoC should beat the no-IO case."""
    base_terms = DebtTerms(
        loan_amount=32_410_000, annual_rate=0.065,
        amort_months=300, io_years=0,
    )
    io_terms = DebtTerms(
        loan_amount=32_410_000, annual_rate=0.065,
        amort_months=300, io_years=3,
    )
    common = dict(
        year1_gpr=5_000_000, year1_vacancy_pct=0.07, year1_expenses=2_000_000,
        rent_growth=0.03, expense_growth=0.03, am_fee_pct=0.04,
        hold_years=5, exit_cap=0.075, equity_raise=10_000_000,
    )
    cf_no_io = build_cashflow(debt=build_debt_schedule(base_terms, 5), **common)
    cf_io = build_cashflow(debt=build_debt_schedule(io_terms, 5), **common)
    # Year 1: IO loan has less debt service, so more cash flow → higher CoC
    assert cf_io.rows[0].cash_flow > cf_no_io.rows[0].cash_flow
    assert cf_io.rows[0].coc > cf_no_io.rows[0].coc
    # IO version year 1 marked as IO
    assert cf_io.rows[0].is_io is True
    assert cf_no_io.rows[0].is_io is False


def test_build_cashflow_invalid_inputs_raise():
    debt = build_debt_schedule(_dove_landing_debt(), hold_years=5)
    common = dict(
        year1_gpr=5_000_000, year1_vacancy_pct=0.07, year1_expenses=2_000_000,
        rent_growth=0.03, expense_growth=0.03, am_fee_pct=0.04,
        exit_cap=0.075, equity_raise=10_000_000,
    )
    # hold_years 0 should raise
    with pytest.raises(ValueError):
        build_cashflow(debt=debt, hold_years=0, **common)
    # hold_years > debt schedule length should raise
    with pytest.raises(ValueError):
        build_cashflow(debt=debt, hold_years=10, **common)
