"""Capital stack: LP raise tracking, one-time fees, and IRR determinism.

Owner punchlist 2026-08-13, items 6, 7 and 8. Item 8 is the load-bearing
one: moving a dial A -> B -> A must return the SAME IRR. It did not,
because the LP equity raise - the denominator of IRR, EM and CoC - stopped
being a function of the dials the moment any dial was touched.
"""

from __future__ import annotations

import pytest

from data.property_io import DealState

BASE = {"s-pp": 2_000_000, "s-noi": 140_000, "s-dp": 30, "s-ir": 6.5,
        "s-vac": 7, "s-rg": 3, "s-eg": 3, "s-xc": 7, "s-hp": 5}


def _deal(**over) -> DealState:
    return DealState.model_validate({**BASE, **over})


def _project_irr(deal: DealState) -> float:
    """The same pipeline the tab and the headline both run."""
    import config
    from core.calc import DebtTerms, build_cashflow, build_debt_schedule
    from core.irr import project_irr
    sched = build_debt_schedule(
        DebtTerms(loan_amount=deal.loan_amount, annual_rate=deal.interest_rate,
                  amort_months=config.AMORT_MONTHS, io_years=deal.io),
        deal.hp)
    cf = build_cashflow(
        year1_gpr=deal.noi * 2.0, year1_vacancy_pct=deal.vacancy_frac,
        year1_expenses=deal.noi * 0.9, rent_growth=deal.rent_growth,
        expense_growth=deal.expense_growth, am_fee_pct=deal.am_fee_pct,
        debt=sched, hold_years=deal.hp, exit_cap=deal.exit_cap,
        equity_raise=deal.equity_raise)
    return project_irr(equity_raise=deal.project_equity,
                       annual_cashflows=[r.cash_flow for r in cf.rows],
                       exit_proceeds_net=cf.exit_proceeds_net)


# --------------------------------------------------------------- item 8

def test_raise_tracks_the_dials_by_default():
    d = _deal()
    assert d.raise_is_custom is False
    assert d.equity_raise == pytest.approx(600_000)   # 30% of 2.0M
    assert _deal(**{"s-pp": 3_000_000}).equity_raise == pytest.approx(900_000)


@pytest.mark.parametrize("dial,a,b", [
    ("s-pp", 2_000_000, 2_400_000),
    ("s-dp", 30, 40),
    ("s-ir", 6.5, 7.5),
    ("s-xc", 7, 8),
])
def test_slider_round_trip_restores_the_irr(dial, a, b):
    """A -> B -> A must land back on the original IRR (item 8)."""
    irr_a = _project_irr(_deal(**{dial: a}))
    _project_irr(_deal(**{dial: b}))          # the excursion
    irr_back = _project_irr(_deal(**{dial: a}))
    assert irr_back == pytest.approx(irr_a, rel=1e-12)


def test_legacy_pinned_raise_is_released_back_to_tracking():
    """Pre-fix files carry a raise_amount the dial widget wrote silently.
    A value that merely echoes the dials must NOT be treated as a
    deliberate override, or the corruption becomes permanent."""
    d = _deal(raise_amount=600_000)           # == 30% of 2.0M, as saved
    assert d.raise_is_custom is False
    assert d.equity_raise == pytest.approx(600_000)
    # Moving a dial with the override OFF clears raise_amount (that is what
    # the dial widget now writes), so the raise follows the dials again.
    moved = d.model_copy(update={"pp": 3_000_000.0, "raise_amount": None})
    assert moved.equity_raise == pytest.approx(900_000)


def test_a_real_override_is_preserved():
    d = _deal(raise_amount=1_250_000)         # nothing like 30% of 2.0M
    assert d.raise_is_custom is True
    assert d.equity_raise == pytest.approx(1_250_000)
    # ...and stays put when the dials move.
    assert _deal(raise_amount=1_250_000,
                 **{"s-pp": 3_000_000}).equity_raise == pytest.approx(1_250_000)


# ------------------------------------------------------------- items 6+7

def test_closing_costs_raise_lp_capital_gp_fee_does_not():
    """Owner decision 2026-08-13: closing costs are funded by the equity
    raise; the GP acquisition fee sits outside LP invested capital."""
    d = _deal(**{"s-closing": 60_000, "s-gpfee": 40_000})
    assert d.equity_raise == pytest.approx(660_000)     # 600k + closing
    assert d.project_equity == pytest.approx(700_000)   # + GP fee
    assert d.total_uses == pytest.approx(2_100_000)


def test_fees_never_touch_noi_cap_rate_or_loan_sizing():
    from core.calc import cap_rate
    plain, fees = _deal(), _deal(**{"s-closing": 75_000, "s-gpfee": 50_000})
    assert fees.noi == plain.noi
    assert cap_rate(fees.noi, fees.pp) == cap_rate(plain.noi, plain.pp)
    assert fees.loan_amount == plain.loan_amount


def test_fees_depress_project_irr():
    assert _project_irr(_deal(**{"s-closing": 100_000, "s-gpfee": 80_000})) \
        < _project_irr(_deal())


def test_gp_fee_alone_leaves_lp_capital_untouched():
    """The LP-side denominator (EM / CoC / LP IRR) must ignore the GP fee."""
    assert _deal(**{"s-gpfee": 90_000}).equity_raise \
        == pytest.approx(_deal().equity_raise)


def test_old_deal_json_still_loads():
    """No new field may be required - files predating 2026-08-13 have none."""
    d = DealState.model_validate(BASE)
    assert (d.gp_fee, d.closing_costs, d.raise_is_custom) == (0.0, 0.0, False)


def test_new_fields_round_trip_through_aliases():
    d = _deal(**{"s-closing": 25_000, "s-gpfee": 15_000}, raise_amount=1_400_000)
    dumped = d.model_dump(by_alias=True)
    assert dumped["s-closing"] == 25_000 and dumped["s-gpfee"] == 15_000
    assert DealState.model_validate(dumped).equity_raise == pytest.approx(1_400_000)
