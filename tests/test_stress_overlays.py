"""Module E - named stress-test overlays (spec 6.3)."""

from __future__ import annotations

import config
from core.sensitivity import SensitivityBase
from core import stress_overlays as so


def _base(**kw) -> SensitivityBase:
    """A healthy Hampton Roads Class C deal: ~7.5% cap, comfortable DSCR."""
    defaults = dict(
        purchase_price=8_000_000,
        year1_gpr=1_400_000,
        year1_expenses=560_000,
        am_fee_pct=0.04,
        loan_amount=5_600_000,
        annual_rate=0.06,
        amort_months=config.AMORT_MONTHS,
        io_years=0,
        hold_years=5,
        exit_cap=0.075,
        equity_raise=2_400_000,
    )
    defaults.update(kw)
    return SensitivityBase(**defaults)


def test_all_three_named_overlays_run():
    report = so.run_stress_overlays(_base())
    assert [r.overlay.key for r in report.results] == [
        "gfc_2008", "covid_2020", "insurance_shock"]


def test_every_overlay_hurts_the_deal():
    """Each stress must produce a WORSE LP IRR than base - an overlay that
    helps the deal is mis-specified. An incomputable stressed IRR (capital
    never returned) counts as maximally worse."""
    report = so.run_stress_overlays(_base())
    for r in report.results:
        assert r.lp_irr is None or (
            r.lp_irr_delta is not None and r.lp_irr_delta < 0), r.overlay.key


def test_gfc_is_the_harshest_of_the_three():
    report = so.run_stress_overlays(_base())
    by_key = {r.overlay.key: r for r in report.results}
    def rank(r):   # None (capital never returned) ranks below any number
        return float("-inf") if r.lp_irr is None else r.lp_irr
    assert rank(by_key["gfc_2008"]) <= rank(by_key["covid_2020"])
    assert rank(by_key["gfc_2008"]) <= rank(by_key["insurance_shock"])


def test_strong_deal_survives_insurance_shock():
    """A deal bought at a fat cap with modest leverage should not fail the
    mildest overlay - otherwise the bar is calibrated too hot."""
    strong = _base(purchase_price=7_000_000, loan_amount=4_200_000,
                   equity_raise=3_000_000)
    report = so.run_stress_overlays(strong)
    by_key = {r.overlay.key: r for r in report.results}
    assert not by_key["insurance_shock"].failed


def test_thin_deal_fails_the_gfc_overlay():
    """A low-cap, high-leverage deal must fail the 2008 overlay."""
    thin = _base(purchase_price=11_000_000, year1_gpr=1_200_000,
                 year1_expenses=560_000, loan_amount=8_800_000,
                 equity_raise=2_200_000, exit_cap=0.055)
    report = so.run_stress_overlays(thin)
    by_key = {r.overlay.key: r for r in report.results}
    assert by_key["gfc_2008"].failed
    assert report.any_failed


def test_failure_bar_is_the_sensitivity_flag():
    report = so.run_stress_overlays(_base())
    for r in report.results:
        expected = r.lp_irr is None or r.lp_irr < config.SENSITIVITY_LP_IRR_FLAG
        assert r.failed == expected


def test_incomputable_lp_irr_under_stress_is_a_failure():
    """A wiped-out deal has no IRR at all - that must read as FAILED."""
    wiped = _base(purchase_price=14_000_000, year1_gpr=1_100_000,
                  year1_expenses=700_000, loan_amount=11_900_000,
                  equity_raise=2_100_000, exit_cap=0.05)
    report = so.run_stress_overlays(wiped)
    by_key = {r.overlay.key: r for r in report.results}
    gfc = by_key["gfc_2008"]
    assert gfc.lp_irr is None and gfc.failed
    assert any("not" in ln and "returned" in ln for ln in so.stress_rationale(report))


def test_rationale_names_the_failing_overlay():
    thin = _base(purchase_price=11_000_000, year1_gpr=1_200_000,
                 loan_amount=8_800_000, equity_raise=2_200_000, exit_cap=0.055)
    report = so.run_stress_overlays(thin)
    lines = so.stress_rationale(report)
    assert lines and any("2008-style" in ln for ln in lines)
    assert all(f"{config.SENSITIVITY_LP_IRR_FLAG:.0%}" in ln for ln in lines)


def test_year1_dscr_reported():
    report = so.run_stress_overlays(_base())
    for r in report.results:
        assert r.dscr_year1 is not None and r.dscr_year1 > 0


def test_deterministic():
    a = so.run_stress_overlays(_base())
    b = so.run_stress_overlays(_base())
    assert [(r.lp_irr, r.project_irr) for r in a.results] == \
           [(r.lp_irr, r.project_irr) for r in b.results]
