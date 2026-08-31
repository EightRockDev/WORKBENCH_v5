"""Value-Add CAPEX wired into the returns engine (2026-08-31).

Pins the fix for the Forrest Pines defect: the renovation panel was a
closed-form display calculator whose plan never reached the cash flow, so
changing the schedule from 2/2/2/2/2 to 2/2/10/10/10 left every header
figure bit-identical, and the "$ per $1 of CAPEX" tile was a per-unit
ratio in disguise (unit count cancelled — $2.30 for 2 units or 200).

Anchors are the owner's exact inputs (Linden at Forrest Pines, 110 units,
Newport News, screenshot of 2026-08-31).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.calc import (
    EMPTY_RENOVATION_PLAN,
    DebtTerms,
    build_cashflow,
    build_debt_schedule,
    build_renovation_plan,
)
from core.renovation import renovation_impact
from data.property_io import DealState

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---- Forrest Pines control inputs (AC-1, screenshot 2026-08-31) ----------
GPR = 2_022_527.0          # sources.json totalRevenue
EXPENSES = 807_945.0
VACANCY = 0.07
RENT_GROWTH = 0.03
EXPENSE_GROWTH = 0.03
AM_FEE = 0.04
EXIT_CAP = 0.07
HOLD = 5
LOAN = 12_145_830.0
RATE = 0.0625
EQUITY = 4_210_554.0       # LP raise, no renovation
PROJECT_EQUITY = 4_410_554.0  # + $200k GP fee
COST_PER_UNIT = 15_000.0
BUMP = 201.0


def _debt(hold: int = HOLD):
    return build_debt_schedule(
        DebtTerms(loan_amount=LOAN, annual_rate=RATE,
                  amort_months=300, io_years=2),
        hold,
    )


def _plan(schedule, hold: int = HOLD, growth: float = RENT_GROWTH,
          cost: float = COST_PER_UNIT, bump: float = BUMP):
    return build_renovation_plan(
        renovations_per_year=schedule, cost_per_unit=cost,
        monthly_rent_bump=bump, hold_years=hold, rent_growth=growth)


def _cashflow(plan=None, funding: str = "raise", equity: float = EQUITY,
              **overrides):
    kwargs = dict(
        year1_gpr=GPR, year1_vacancy_pct=VACANCY, year1_expenses=EXPENSES,
        rent_growth=RENT_GROWTH, expense_growth=EXPENSE_GROWTH,
        am_fee_pct=AM_FEE, debt=_debt(), hold_years=HOLD,
        exit_cap=EXIT_CAP, equity_raise=equity,
        reno=plan, reno_capex_funding=funding,
    )
    kwargs.update(overrides)
    return build_cashflow(**kwargs)


def _impact(schedule, funding: str = "raise", **plan_overrides):
    plan = _plan(schedule, **plan_overrides)
    return renovation_impact(
        plan=plan, capex_funding=funding,
        equity_without_reno=EQUITY,
        project_equity_without_reno=PROJECT_EQUITY,
        year1_gpr=GPR, year1_vacancy_pct=VACANCY, year1_expenses=EXPENSES,
        rent_growth=RENT_GROWTH, expense_growth=EXPENSE_GROWTH,
        am_fee_pct=AM_FEE, debt=_debt(), hold_years=HOLD, exit_cap=EXIT_CAP,
    )


def _deal(**overrides) -> DealState:
    base = dict(
        pp=16_190_000.0, noi=1_214_582.0, dp=26.0, ir=6.25, vac=7.0,
        rg=3.0, eg=3.0, xc=7.0, hp=5,
        closing_costs=0.0, gp_fee=0.0,
    )
    base.update(overrides)
    return DealState.model_validate(base)


# ===========================================================================
# Plan math
# ===========================================================================

def test_plan_pads_and_truncates_to_hold_period():
    short = _plan([2, 2], hold=5)
    assert short.units_by_year == [2, 2, 0, 0, 0]
    assert len(short.capex_by_year) == 5
    assert len(short.rent_lift_by_year) == 5

    long = _plan([1, 2, 3, 4, 5, 6, 7], hold=3)
    assert long.units_by_year == [1, 2, 3]
    assert long.total_units == 6


def test_capex_equals_units_times_cost():
    plan = _plan([2, 2, 10, 10, 10])
    assert plan.capex_by_year == [30_000.0, 30_000.0, 150_000.0,
                                  150_000.0, 150_000.0]
    assert plan.total_capex == pytest.approx(510_000.0)
    assert plan.total_units == 34


def test_mid_year_factor_applies_to_placement_year():
    plan = _plan([4, 0, 0, 0, 0], growth=0.0)
    annual = 4 * BUMP * 12.0
    assert plan.rent_lift_by_year[0] == pytest.approx(annual * 0.5)
    # Full year from the year after placement.
    assert plan.rent_lift_by_year[1] == pytest.approx(annual)


def test_placed_lift_grows_at_rent_growth():
    g = 0.03
    plan = _plan([4, 0, 0, 0, 0], growth=g)
    annual = 4 * BUMP * 12.0
    # Year 3 = two growth years after a year-1 placement.
    assert plan.rent_lift_by_year[2] == pytest.approx(annual * (1 + g) ** 2)


def test_exit_lift_is_full_year_grown_to_n_plus_1():
    g = 0.03
    plan = _plan([4, 0, 0, 0, 3], growth=g)
    annual_per_unit = BUMP * 12.0
    expected = (4 * annual_per_unit * (1 + g) ** 5      # placed year 1 → N+1
                + 3 * annual_per_unit * (1 + g) ** 1)   # placed year 5 → N+1
    assert plan.exit_rent_lift == pytest.approx(expected)


def test_empty_plan_is_inert():
    assert EMPTY_RENOVATION_PLAN.is_empty
    plan = _plan([0, 0, 0, 0, 0])
    assert plan.is_empty
    assert plan.total_capex == 0.0
    assert all(v == 0.0 for v in plan.rent_lift_by_year)
    assert plan.exit_rent_lift == 0.0
    # The engine treats it exactly like no plan at all.
    assert (_cashflow(plan).total_distributions
            == pytest.approx(_cashflow(None).total_distributions))


def test_plan_validation_rejects_bad_inputs():
    with pytest.raises(ValueError, match="hold_years"):
        _plan([1], hold=0)
    with pytest.raises(ValueError, match="negative"):
        _plan([2, -1, 0, 0, 0])
    with pytest.raises(ValueError, match="first_year_factor"):
        build_renovation_plan(
            renovations_per_year=[1], cost_per_unit=1.0,
            monthly_rent_bump=1.0, hold_years=5, first_year_factor=1.5)


# ===========================================================================
# Engine wiring
# ===========================================================================

def test_lift_raises_gpr_noi_and_exit_monotonically():
    runs = [_cashflow(None),
            _cashflow(_plan([2, 2, 2, 2, 2])),
            _cashflow(_plan([2, 2, 10, 10, 10]))]
    for smaller, larger in zip(runs, runs[1:]):
        assert larger.rows[-1].gpr > smaller.rows[-1].gpr
        assert larger.rows[-1].noi > smaller.rows[-1].noi
        assert larger.exit_noi > smaller.exit_noi
        assert larger.exit_proceeds_gross > smaller.exit_proceeds_gross


def test_more_units_moves_the_irr():
    none = _impact([0, 0, 0, 0, 0])
    ten = _impact([2, 2, 2, 2, 2])
    thirty_four = _impact([2, 2, 10, 10, 10])
    assert none.irr_delta == pytest.approx(0.0, abs=1e-9)
    assert ten.irr_with > none.irr_with
    assert thirty_four.irr_with > ten.irr_with


def test_vacancy_and_am_fee_both_apply_to_the_lift():
    plan = _plan([10, 0, 0, 0, 0])
    with_reno = _cashflow(plan)
    without = _cashflow(None)
    y1_with, y1_without = with_reno.rows[0], without.rows[0]
    lift = y1_with.reno_rent_lift
    assert lift > 0
    # Vacancy is charged on lifted GPR...
    assert y1_with.vacancy_loss == pytest.approx(y1_with.gpr * VACANCY)
    assert (y1_with.vacancy_loss - y1_without.vacancy_loss
            == pytest.approx(lift * VACANCY))
    # ...and so is the AM fee (year 1 < hold, so the fee is live).
    assert y1_with.am_fee == pytest.approx(y1_with.gpr * AM_FEE)
    assert (y1_with.am_fee - y1_without.am_fee
            == pytest.approx(lift * AM_FEE))


def test_operations_funding_deducts_yearly_capex_and_raise_does_not():
    plan = _plan([2, 2, 10, 10, 10])
    cf_raise = _cashflow(plan, funding="raise")
    cf_ops = _cashflow(plan, funding="operations")
    for r_raise, r_ops, capex in zip(cf_raise.rows, cf_ops.rows,
                                     plan.capex_by_year):
        assert r_ops.cash_flow == pytest.approx(r_raise.cash_flow - capex)
        assert r_raise.reno_capex == capex  # recorded, not deducted


def test_noi_identical_under_both_funding_modes():
    plan = _plan([2, 2, 10, 10, 10])
    cf_raise = _cashflow(plan, funding="raise")
    cf_ops = _cashflow(plan, funding="operations")
    for r_raise, r_ops in zip(cf_raise.rows, cf_ops.rows):
        assert r_ops.noi == pytest.approx(r_raise.noi)
        assert r_ops.noi_after_am == pytest.approx(r_raise.noi_after_am)
    assert cf_ops.exit_noi == pytest.approx(cf_raise.exit_noi)


def test_capex_is_never_charged_twice():
    plan = _plan([2, 2, 10, 10, 10])
    cf_raise = _cashflow(plan, funding="raise")
    cf_ops = _cashflow(plan, funding="operations")
    # Exactly ONE total-capex gap between the two modes' distributions —
    # a double charge would make it 2×, a missed charge 0.
    assert (cf_raise.total_distributions - cf_ops.total_distributions
            == pytest.approx(plan.total_capex))


def test_wrong_length_plan_and_unknown_funding_raise():
    plan_3yr = _plan([1, 1, 1], hold=3)
    with pytest.raises(ValueError, match="renovation plan covers"):
        _cashflow(plan_3yr)  # hold_years=5 in the cashflow
    with pytest.raises(ValueError, match="reno_capex_funding"):
        _cashflow(_plan([1, 0, 0, 0, 0]), funding="escrow")


def test_reno_none_reproduces_prior_behaviour():
    legacy_shape = build_cashflow(
        year1_gpr=GPR, year1_vacancy_pct=VACANCY, year1_expenses=EXPENSES,
        rent_growth=RENT_GROWTH, expense_growth=EXPENSE_GROWTH,
        am_fee_pct=AM_FEE, debt=_debt(), hold_years=HOLD,
        exit_cap=EXIT_CAP, equity_raise=EQUITY,
    )
    explicit_none = _cashflow(None)
    assert legacy_shape == explicit_none
    assert all(r.reno_rent_lift == 0.0 and r.reno_capex == 0.0
               for r in legacy_shape.rows)
    assert legacy_shape.reno_total_capex == 0.0


# ===========================================================================
# Model (DealState)
# ===========================================================================

def test_escrowed_capex_enters_raise_and_total_uses():
    deal = _deal(reno_units_by_year=[2, 2, 10, 10, 10],
                 reno_cost_per_unit=15_000.0,
                 reno_monthly_rent_bump=201.0,
                 reno_capex_funding="raise")
    assert deal.reno_total_capex == pytest.approx(510_000.0)
    assert deal.equity_raise == pytest.approx(
        deal.down_payment_dollars + deal.closing_costs + 510_000.0)
    assert deal.total_uses == pytest.approx(
        deal.pp + deal.closing_costs + deal.gp_fee + 510_000.0)


def test_operations_funding_stays_out_of_raise_but_in_total_uses():
    deal = _deal(reno_units_by_year=[2, 2, 10, 10, 10],
                 reno_cost_per_unit=15_000.0,
                 reno_monthly_rent_bump=201.0,
                 reno_capex_funding="operations")
    assert deal.reno_capex_in_raise == 0.0
    assert deal.equity_raise == pytest.approx(
        deal.down_payment_dollars + deal.closing_costs)
    assert deal.total_uses == pytest.approx(
        deal.pp + deal.closing_costs + deal.gp_fee + 510_000.0)


def test_plan_follows_the_hold_period_dial():
    deal = _deal(reno_units_by_year=[2, 2], reno_monthly_rent_bump=100.0)
    assert len(deal.renovation_plan().units_by_year) == 5
    longer = deal.model_copy(update={"hp": 8})
    assert len(longer.renovation_plan().units_by_year) == 8
    shorter = deal.model_copy(update={"hp": 3})
    plan = shorter.renovation_plan()
    assert len(plan.units_by_year) == 3
    assert plan.units_by_year == [2, 2, 0]


def test_bad_funding_string_on_disk_degrades_to_raise():
    deal = _deal(reno_capex_funding="escrowed-maybe")
    assert deal.reno_capex_funding == "raise"


def test_down_payment_dial_a_b_a_reproduces_equity_raise():
    """Re-pins the 2026-08-13 sentinel bug with CAPEX in the raise."""
    deal = _deal(dp=26.0, reno_units_by_year=[2, 2, 10, 10, 10],
                 reno_monthly_rent_bump=201.0)
    original = deal.equity_raise
    moved = deal.model_copy(update={"dp": 40.0})
    assert moved.equity_raise != original
    back = moved.model_copy(update={"dp": 26.0})
    assert back.equity_raise == original


# ===========================================================================
# Impact (core.renovation)
# ===========================================================================

def test_control_reproduces_the_screenshot():
    imp = _impact([0, 0, 0, 0, 0])
    assert imp.irr_without == pytest.approx(0.1120, abs=5e-4)
    assert imp.em_without == pytest.approx(1.72, abs=5e-3)
    assert imp.equity_without == pytest.approx(4_210_554.0)


def test_profit_per_capex_dollar_varies_with_schedule_shape():
    front = _impact([2, 2, 2, 2, 2])         # 10 units, early
    back = _impact([2, 2, 10, 10, 10])       # 34 units, late
    assert front.profit_per_capex_dollar == pytest.approx(1.72, abs=0.02)
    assert back.profit_per_capex_dollar == pytest.approx(1.56, abs=0.02)
    # Back-loading earns the bump for fewer years → less per dollar.
    assert front.profit_per_capex_dollar > back.profit_per_capex_dollar


def test_brians_exact_edit_moves_the_irr():
    ten = _impact([2, 2, 2, 2, 2])
    thirty_four = _impact([2, 2, 10, 10, 10])
    assert ten.irr_with == pytest.approx(0.1170, abs=5e-4)
    assert ten.equity_with == pytest.approx(4_360_554.0)
    assert thirty_four.irr_with == pytest.approx(0.1246, abs=5e-4)
    assert thirty_four.equity_with == pytest.approx(4_720_554.0)
    # The 10 → 34 edit moves project IRR ~+0.76 points. Pre-fix: 0.00.
    assert (thirty_four.irr_with - ten.irr_with) * 100 == pytest.approx(
        0.76, abs=0.02)


def test_irr_delta_sign_reflects_program_quality():
    good = _impact([2, 2, 10, 10, 10])
    assert good.irr_delta is not None and good.irr_delta > 0
    # $100k/unit for a $50/mo bump is clearly dilutive — the delta must be
    # reported negative, never clamped or hidden.
    bad = _impact([5, 5, 5, 5, 5], cost=100_000.0, bump=50.0)
    assert bad.irr_delta is not None and bad.irr_delta < 0


def test_funding_mode_changes_timing_not_total_profit():
    escrowed = _impact([2, 2, 10, 10, 10], funding="raise")
    from_ops = _impact([2, 2, 10, 10, 10], funding="operations")
    assert escrowed.profit_delta == pytest.approx(from_ops.profit_delta)
    assert escrowed.irr_with != pytest.approx(from_ops.irr_with, abs=1e-4)


def test_empty_plan_gives_all_zero_impact():
    imp = _impact([0, 0, 0, 0, 0])
    assert imp.is_empty
    assert imp.total_capex == 0.0
    assert imp.irr_delta == pytest.approx(0.0, abs=1e-9)
    assert imp.em_delta == pytest.approx(0.0, abs=1e-9)
    assert imp.exit_value_delta == pytest.approx(0.0, abs=1e-6)
    assert imp.profit_delta == pytest.approx(0.0, abs=1e-6)
    assert imp.profit_per_capex_dollar == 0.0


# ===========================================================================
# Review round (2026-08-31 adversarial pass) — confirmed-finding pins
# ===========================================================================

def test_negative_units_on_disk_degrade_instead_of_crashing():
    """deal.json is user-editable; a bad entry must not take down every
    surface (equity_raise -> tracked_raise -> renovation_plan())."""
    deal = DealState.model_validate({
        "s-pp": 16_190_000.0, "s-noi": 1_214_582.0, "s-dp": 25.0,
        "s-ir": 6.25, "s-vac": 7.0, "s-rg": 3.0, "s-eg": 3.0,
        "s-xc": 7.0, "s-hp": 5,
        "s-reno-units": [2, -1, "3", None, 5.0],
    })
    assert deal.reno_units_by_year == [2, 0, 3, 0, 5]
    # These all crashed with an unsanitized list:
    assert deal.renovation_plan().total_units == 10
    assert deal.equity_raise > 0
    assert deal.total_uses > 0


def test_legacy_pinned_raise_stays_tracking_when_reno_added():
    """A raise_amount pinned by the pre-2026-08-13 bug matches the OLD
    (capex-less) implied figure. Adding a reno program must not reclassify
    that stale copy as a deliberate override — verified repro: it silently
    pinned the raise WITHOUT the escrow, forever."""
    deal = _deal(dp=25.0, closing_costs=163_054.0,
                 raise_amount=16_190_000.0 * 0.25 + 163_054.0)
    assert not deal.raise_is_custom
    with_reno = deal.model_copy(update={
        "reno_units_by_year": [2, 2, 10, 10, 10],
        "reno_monthly_rent_bump": 201.0,
    })
    reloaded = DealState.model_validate(with_reno.model_dump(by_alias=True))
    assert not reloaded.raise_is_custom
    assert reloaded.equity_raise == pytest.approx(reloaded.tracked_raise)
    assert reloaded.equity_raise == pytest.approx(
        16_190_000.0 * 0.25 + 163_054.0 + 510_000.0)


def test_merge_schedule_preserves_tail_through_hold_round_trip():
    """Hold dial 5 -> 3 -> 5 must not destroy years 4-5 of the program."""
    from ui.value_add import _merge_schedule
    stored = [2, 2, 10, 10, 10]
    # Viewing/saving at hold=3 keeps the stored tail...
    assert _merge_schedule(stored, [2, 2, 10], 3) == stored
    # ...an in-window edit applies without touching the tail...
    assert _merge_schedule(stored, [3, 3, 3], 3) == [3, 3, 3, 10, 10]
    # ...and a longer hold just appends the widget years.
    assert _merge_schedule([2, 2], [2, 2, 1, 1, 1], 5) == [2, 2, 1, 1, 1]


def test_legacy_migration_values_preserve_deliberate_zero():
    """`x or default` resurrected $15,000 over a saved $0 — absent falls
    back, zero survives."""
    from ui.value_add import _legacy_plan_values
    units, cost, bump = _legacy_plan_values({
        "cost_per_unit": 0.0,
        "monthly_rent_increase_per_unit": 0.0,
        "renovations_per_year": [2, -1, 3],
    })
    assert cost == 0.0
    assert bump == 0.0
    assert units == [2, 0, 3]
    units, cost, bump = _legacy_plan_values({})
    assert cost == 15_000.0
    assert bump == 0.0


def test_sensitivity_and_stress_rebuild_plan_at_cell_growth():
    """A stressed cell must not let renovated units keep growing at the
    base-case rate — the plan is rebuilt per cell (replan_rent_growth)."""
    from core.calc import replan_rent_growth
    base_plan = _plan([2, 2, 10, 10, 10], growth=0.03)
    stressed = replan_rent_growth(base_plan, HOLD, 0.05)
    assert stressed.units_by_year == base_plan.units_by_year
    assert stressed.cost_per_unit == base_plan.cost_per_unit
    assert stressed.rent_lift_by_year[-1] > base_plan.rent_lift_by_year[-1]
    assert stressed.exit_rent_lift > base_plan.exit_rent_lift
    assert replan_rent_growth(None, HOLD, 0.05) is None

    # Behavioral: the reno path through the sensitivity grid moves numbers.
    from core.sensitivity import SensitivityBase, build_sensitivity
    common = dict(
        purchase_price=16_190_000.0, year1_gpr=GPR, year1_expenses=EXPENSES,
        am_fee_pct=AM_FEE, loan_amount=LOAN, annual_rate=RATE,
        amort_months=300, io_years=2, hold_years=HOLD, exit_cap=EXIT_CAP,
    )
    plain = build_sensitivity(SensitivityBase(equity_raise=EQUITY, **common))
    with_reno = build_sensitivity(SensitivityBase(
        equity_raise=EQUITY + base_plan.total_capex,
        reno=base_plan, reno_capex_funding="raise", **common))
    assert any(
        a.project_irr != b.project_irr
        for a, b in zip(plain.cells, with_reno.cells)
    )


# ===========================================================================
# The seam guard
# ===========================================================================

def test_every_build_cashflow_call_site_passes_a_renovation_plan():
    """CLAUDE.md: 'three divergent builds is the biggest structural risk'."""
    missing: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(".venv/") or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match bare-name AND attribute-form calls (calc.build_cashflow)
            # — the attribute form would otherwise evade the guard.
            func_name = (getattr(node.func, "id", None)
                         or getattr(node.func, "attr", None))
            if func_name != "build_cashflow":
                continue
            if "reno" not in {kw.arg for kw in node.keywords}:
                missing.append(f"{rel}:{node.lineno}")
    assert not missing, (
        "build_cashflow call sites that drop the renovation plan: "
        + ", ".join(missing)
    )
