"""Tests for core.sensitivity — 3×3×2 grid integration of calc + waterfall + irr.

Strategy:
  - Grid shape tests pin the 18-cell layout and the order of iteration.
  - Sanity tests verify that worse inputs (higher vacancy, lower rent growth,
    higher expense growth) produce worse LP IRRs.
  - Flag-counting tests confirm cells dropping below the 12% LP IRR target
    get marked.
  - One Dove-Landing-style fixture exercises the full pipeline end-to-end.
"""

from __future__ import annotations

import pytest

import config
from core.sensitivity import (
    SensitivityBase,
    SensitivityCell,
    build_sensitivity,
)


def _dove_base() -> SensitivityBase:
    """Dove Landing-ish: $46.3M / $32.41M loan @ 6.5%, 5-yr hold, 7.5% exit cap.
    Year-1 GPR + expenses are picked to produce sensible IRR ranges."""
    return SensitivityBase(
        purchase_price=46_300_000,
        year1_gpr=6_000_000,
        year1_expenses=2_600_000,
        am_fee_pct=0.04,
        loan_amount=32_410_000,
        annual_rate=0.065,
        amort_months=300,
        io_years=0,
        hold_years=5,
        exit_cap=0.075,
        equity_raise=13_890_000,
    )


# ---------------------------------------------------------------------------
# Grid shape
# ---------------------------------------------------------------------------


def test_grid_has_18_cells():
    """3 vacancies × 3 rent growths × 2 expense growths = 18."""
    grid = build_sensitivity(_dove_base())
    assert len(grid.cells) == 18


def test_grid_covers_every_combination():
    """Each (vacancy, rent_growth, expense_growth_label) tuple appears exactly once."""
    grid = build_sensitivity(_dove_base())
    keys = {
        (round(c.vacancy, 4), round(c.rent_growth, 4), c.expense_growth_label)
        for c in grid.cells
    }
    expected = {
        (round(v, 4), round(rg, 4), label)
        for v in config.SENSITIVITY_VACANCIES
        for rg in config.SENSITIVITY_RENT_GROWTHS
        for label in config.SENSITIVITY_EXPENSE_GROWTHS
    }
    assert keys == expected


def test_grid_iteration_order_vacancy_outer():
    """First six cells share the lowest vacancy (5%) — outer loop."""
    grid = build_sensitivity(_dove_base())
    first_six_vacancies = {c.vacancy for c in grid.cells[:6]}
    assert first_six_vacancies == {config.SENSITIVITY_VACANCIES[0]}


def test_grid_uses_config_values():
    """Sanity-check we're using the locked SUMMARY-FORMAT thresholds."""
    grid = build_sensitivity(_dove_base())
    vacancies_seen = {c.vacancy for c in grid.cells}
    rgs_seen = {round(c.rent_growth, 4) for c in grid.cells}
    labels_seen = {c.expense_growth_label for c in grid.cells}
    assert vacancies_seen == set(config.SENSITIVITY_VACANCIES)
    assert rgs_seen == {round(rg, 4) for rg in config.SENSITIVITY_RENT_GROWTHS}
    assert labels_seen == set(config.SENSITIVITY_EXPENSE_GROWTHS)


# ---------------------------------------------------------------------------
# Cell content + monotonicity
# ---------------------------------------------------------------------------


def test_each_cell_populated():
    """Every cell should have all numeric outputs filled."""
    grid = build_sensitivity(_dove_base())
    for c in grid.cells:
        assert isinstance(c, SensitivityCell)
        assert c.lp_irr is not None
        assert c.project_irr is not None
        assert isinstance(c.coc_year1, float)
        assert isinstance(c.flagged, bool)


def test_higher_vacancy_lowers_lp_irr_holding_other_vars_fixed():
    """For a fixed (rg, eg) pair, going from 5% → 10% vacancy should hurt LP IRR."""
    grid = build_sensitivity(_dove_base())
    # Pick rent_growth = 3.5%, expense_growth = conservative as the "middle" cell
    pick = lambda v: next(
        c for c in grid.cells
        if c.vacancy == v and round(c.rent_growth, 4) == 0.035
        and c.expense_growth_label == "conservative"
    )
    low_vac = pick(0.05).lp_irr
    mid_vac = pick(0.07).lp_irr
    high_vac = pick(0.10).lp_irr
    assert low_vac > mid_vac > high_vac


def test_higher_rent_growth_helps_lp_irr_holding_other_vars_fixed():
    """For a fixed (vac, eg) pair, going from 2% → 5% rent growth should improve LP IRR."""
    grid = build_sensitivity(_dove_base())
    pick = lambda rg: next(
        c for c in grid.cells
        if c.vacancy == 0.07 and round(c.rent_growth, 4) == round(rg, 4)
        and c.expense_growth_label == "conservative"
    )
    low_rg = pick(0.02).lp_irr
    mid_rg = pick(0.035).lp_irr
    high_rg = pick(0.05).lp_irr
    assert low_rg < mid_rg < high_rg


def test_aggressive_expense_growth_hurts_lp_irr():
    """For a fixed (vac, rg) pair, aggressive expense growth should hurt LP IRR
    vs conservative expense growth."""
    grid = build_sensitivity(_dove_base())
    base_filter = lambda c: (c.vacancy == 0.07 and round(c.rent_growth, 4) == 0.035)
    conservative = next(
        c for c in grid.cells
        if base_filter(c) and c.expense_growth_label == "conservative"
    )
    aggressive = next(
        c for c in grid.cells
        if base_filter(c) and c.expense_growth_label == "aggressive"
    )
    assert conservative.lp_irr > aggressive.lp_irr


# ---------------------------------------------------------------------------
# Flag-counting
# ---------------------------------------------------------------------------


def test_flagged_count_matches_cells():
    grid = build_sensitivity(_dove_base())
    manual_count = sum(1 for c in grid.cells if c.flagged)
    assert grid.flagged_count == manual_count


def test_cell_flagged_when_lp_irr_below_threshold():
    """Manually verify the flag rule: any cell with LP IRR < 12% is flagged."""
    grid = build_sensitivity(_dove_base())
    for c in grid.cells:
        if c.lp_irr is not None and c.lp_irr < config.SENSITIVITY_LP_IRR_FLAG:
            assert c.flagged is True
        else:
            assert c.flagged is False


def test_lp_irr_monotonicity_implies_flag_consistency():
    """Among cells with numeric LP IRR, those below the threshold must be
    flagged AND those at/above must not. Per-cell flag consistency is the
    contract — count comparisons across deals are unreliable because some
    stress scenarios produce None LP IRRs (negative CF vectors)."""
    grid = build_sensitivity(_dove_base())
    threshold = config.SENSITIVITY_LP_IRR_FLAG
    for c in grid.cells:
        if c.lp_irr is None:
            assert c.flagged is False, "None IRR should not flag"
        elif c.lp_irr < threshold:
            assert c.flagged is True
        else:
            assert c.flagged is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_zero_hold_years_raises():
    base = _dove_base()
    bad = SensitivityBase(**{**base.__dict__, "hold_years": 0})
    with pytest.raises(ValueError):
        build_sensitivity(bad)


def test_io_period_grid_still_valid():
    """Grid evaluates cleanly when the deal has IO years (debt schedule changes)."""
    base = _dove_base()
    io_base = SensitivityBase(**{**base.__dict__, "io_years": 3})
    grid = build_sensitivity(io_base)
    assert len(grid.cells) == 18
    # All cells should still produce numeric IRRs
    for c in grid.cells:
        assert c.lp_irr is not None
        assert c.project_irr is not None
