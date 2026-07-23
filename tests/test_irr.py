"""Tests for core.irr — IRR, NPV, equity multiple.

Strategy:
  - Trivially-checkable IRR cases (1-year deal, etc.) confirm the math.
  - End-to-end: feed `core.waterfall.run_waterfall` output into `lp_irr` and
    pin the LP IRR for the value-add scenario hand-computed in test_waterfall.
  - Edge cases (no sign change, empty, all positive) → return None, not NaN.
"""

from __future__ import annotations

import math

import pytest

from core.irr import equity_multiple, irr, lp_irr, npv, project_irr
from core.waterfall import run_waterfall


# ---------------------------------------------------------------------------
# NPV
# ---------------------------------------------------------------------------


def test_npv_at_zero_rate_equals_sum():
    assert npv(0.0, [-1000, 200, 200, 200, 200, 200]) == pytest.approx(0.0)


def test_npv_basic_known_value():
    # PV of $100 received in 1 year at 10% = $90.91
    assert npv(0.10, [0, 100]) == pytest.approx(90.9091, abs=0.001)


def test_npv_invalid_rate_raises():
    with pytest.raises(ValueError):
        npv(-1.0, [100, 100])


# ---------------------------------------------------------------------------
# IRR — known trivial cases
# ---------------------------------------------------------------------------


def test_irr_one_year_10pct():
    """Invest $1000, get $1100 in 1 year → IRR = 10%."""
    assert irr([-1000, 1100]) == pytest.approx(0.10, abs=1e-6)


def test_irr_doubles_in_5_years():
    """Invest $1000, get $2000 in 5 years → IRR = 2^(1/5) - 1 = 14.87%."""
    expected = 2.0 ** 0.2 - 1.0
    assert irr([-1000, 0, 0, 0, 0, 2000]) == pytest.approx(expected, abs=1e-6)


def test_irr_uniform_annuity():
    """$1000 invested, $300 for 5 years → IRR ≈ 15.238%."""
    # Cross-checked against Excel IRR(-1000; 300; 300; 300; 300; 300) = 15.238%
    assert irr([-1000, 300, 300, 300, 300, 300]) == pytest.approx(0.15238, abs=1e-4)


def test_irr_zero_when_break_even():
    """Invest $1000, get $1000 back in 1 year → IRR = 0%."""
    assert irr([-1000, 1000]) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# IRR — degenerate / edge cases return None
# ---------------------------------------------------------------------------


def test_irr_empty_returns_none():
    assert irr([]) is None


def test_irr_single_value_returns_none():
    assert irr([1000]) is None


def test_irr_all_positive_returns_none():
    """No sign change → no IRR exists."""
    assert irr([100, 200, 300]) is None


def test_irr_all_negative_returns_none():
    assert irr([-100, -200, -300]) is None


def test_irr_no_solution_returns_none():
    """Cash flows where IRR can't be found (e.g. all zeros after sign change)."""
    # numpy_financial returns NaN here; our wrapper returns None
    result = irr([-1000, 0, 0, 0, 0])
    assert result is None or result < -0.99  # extreme negative is also valid "no return"


# ---------------------------------------------------------------------------
# Project IRR
# ---------------------------------------------------------------------------


def test_project_irr_basic():
    """5-year deal, $1M annual ops, $15M net exit on $10M equity.

    Cash flows: [-10M, 1M, 1M, 1M, 1M, 1M+15M=16M]
    """
    result = project_irr(
        equity_raise=10_000_000,
        annual_cashflows=[1_000_000] * 5,
        exit_proceeds_net=15_000_000,
    )
    # Cross-check via direct IRR — project_irr() should produce the same value
    # as calling irr() directly on the manually-constructed flow vector.
    expected = irr([-10_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000, 16_000_000])
    assert result == pytest.approx(expected, abs=1e-9)
    # Sanity: positive return, somewhere between 15% and 25% for this fixture
    assert result is not None
    assert 0.15 < result < 0.25


def test_project_irr_empty_cashflows_returns_none():
    assert project_irr(
        equity_raise=10_000_000,
        annual_cashflows=[],
        exit_proceeds_net=15_000_000,
    ) is None


def test_project_irr_appends_exit_only_to_final_year():
    """exit_proceeds_net should land on the LAST year, not be spread across years."""
    result_at_5 = project_irr(
        equity_raise=1000,
        annual_cashflows=[100, 100, 100, 100, 100],
        exit_proceeds_net=2000,
    )
    # Direct equivalent
    direct = irr([-1000, 100, 100, 100, 100, 2100])
    assert result_at_5 == pytest.approx(direct, abs=1e-9)


# ---------------------------------------------------------------------------
# LP IRR — fed by waterfall output (the integration test)
# ---------------------------------------------------------------------------


def test_lp_irr_value_add_scenario():
    """Hand-computed: $0/$0/$0/$0/$30M → LP gets $25.2M in year 5 on $10M.

    Equity multiple = 2.52x. IRR = 2.52^(1/5) - 1 ≈ 20.33%.
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[0, 0, 0, 0, 30_000_000],
    )
    lp_rate = lp_irr(result.lp_cashflows)
    expected = (2.52) ** 0.2 - 1.0  # ≈ 0.20327
    assert lp_rate == pytest.approx(expected, abs=1e-4)


def test_lp_irr_below_target_flagged():
    """LP IRR target is 15%. A deal returning only 10% LP IRR should be < target."""
    # Construct: invest $10M, get $1.1M/yr for 5 years, no sale → CF [-10M, 1.1M ×5]
    result = run_waterfall(
        equity_raise=10_000_000,
        # All cash flows go to LP via pref + ROC; no residual since no surplus
        annual_pots=[1_100_000, 1_100_000, 1_100_000, 1_100_000, 1_100_000],
    )
    lp_rate = lp_irr(result.lp_cashflows)
    # This should be a poor return (deeply negative — LP gets pref + small ROC,
    # never recovers principal). We just check it's below the 15% target.
    assert lp_rate is not None
    assert lp_rate < 0.15


def test_lp_irr_combined_pot_5yr_deal():
    """5-year hold, $1M annual ops, $20M net exit. Hand computed in test_waterfall.

    LP cashflows: [-10M, 1M, 1M, 1M, 1M, 17.648M]
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[1_000_000, 1_000_000, 1_000_000, 1_000_000, 21_000_000],
    )
    lp_rate = lp_irr(result.lp_cashflows)
    # Cross-check directly with the irr() function on the same vector
    direct = irr(result.lp_cashflows)
    assert lp_rate == pytest.approx(direct, abs=1e-9)
    # Should comfortably clear the 15% LP target
    assert lp_rate is not None
    assert lp_rate > 0.15


# ---------------------------------------------------------------------------
# Equity multiple
# ---------------------------------------------------------------------------


def test_equity_multiple_break_even():
    assert equity_multiple(10_000_000, 10_000_000) == 1.0


def test_equity_multiple_above_target():
    """Eight Rock target is 1.8x — verify a 2.0x deal."""
    assert equity_multiple(10_000_000, 20_000_000) == 2.0


def test_equity_multiple_zero_equity_returns_zero():
    assert equity_multiple(0, 1_000_000) == 0.0
    assert equity_multiple(-1, 1_000_000) == 0.0


def test_equity_multiple_via_waterfall():
    """End-to-end: waterfall.total_lp_distributions / equity_raise = LP equity multiple."""
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[0, 0, 0, 0, 30_000_000],
    )
    em = equity_multiple(10_000_000, result.total_lp_distributions)
    assert em == pytest.approx(2.52, abs=0.01)


# ---------------------------------------------------------------------------
# Project IRR > LP IRR ordering (sanity — promote can only hurt LP returns)
# ---------------------------------------------------------------------------


def test_project_irr_at_or_above_lp_irr():
    """The promote takes from LPs, so project IRR ≥ LP IRR for any deal where
    GP gets some residual. Verify the inequality holds."""
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[1_000_000, 1_000_000, 1_000_000, 1_000_000, 21_000_000],
    )
    lp_rate = lp_irr(result.lp_cashflows)
    # Project flows = LP flows + GP flows (since LP+GP = pot each year)
    project_flows = [
        result.lp_cashflows[t] + result.gp_cashflows[t]
        for t in range(len(result.lp_cashflows))
    ]
    project_rate = irr(project_flows)
    assert project_rate is not None and lp_rate is not None
    # Project should beat LP because GP residual is being added back in
    assert project_rate >= lp_rate
