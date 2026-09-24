"""core.year1_inputs - the ONE Year-1 derivation behind every projection.

Owner report 2026-09-24 (Hampton Community Townhomes, 120u, Hampton): the V2
stat bar showed a 6.73% going-in cap next to a -22.4% five-year IRR and a
0.2x equity multiple. Root cause: the cap / DSCR / CoC cards ran off the NOI
dial while the IRR engine rebuilt Year 1 from the T-12 file, treating the
T-12's COLLECTED income as gross potential rent and taking vacancy out of it a
second time. These tests pin the invariant that makes the cards agree.
"""

from __future__ import annotations

import config
from core.calc import (
    DebtTerms,
    build_cashflow,
    build_debt_schedule,
    effective_year1_vacancy,
)
from core.irr import project_irr
from core.year1_inputs import derive_year1_inputs
from data.property_io import DealState


def _deal(**kw) -> DealState:
    base = dict(pp=5_000_000, noi=350_000, dp=30, ir=6.5, vac=8, rg=3,
                eg=3, xc=6.5, hp=5, vac_spike_pp=0, stabilization_months=0,
                tax_reassessment_on=False, insurance_escalator_on=False)
    base.update(kw)
    return DealState(**base)


def _year1_row(deal: DealState, sources, units=100, city="Norfolk"):
    gpr, expenses = derive_year1_inputs(deal, sources, units, city=city)
    debt = build_debt_schedule(
        DebtTerms(loan_amount=deal.loan_amount, annual_rate=deal.interest_rate,
                  amort_months=config.AMORT_MONTHS, io_years=deal.io),
        deal.hp,
    )
    cf = build_cashflow(
        year1_gpr=gpr, year1_vacancy_pct=deal.vacancy_frac,
        year1_expenses=expenses, rent_growth=deal.rent_growth,
        expense_growth=deal.expense_growth, am_fee_pct=deal.am_fee_pct,
        debt=debt, hold_years=deal.hp, exit_cap=deal.exit_cap,
        equity_raise=deal.equity_raise,
        stabilized_vacancy_pct=deal.vacancy_frac,
    )
    return gpr, expenses, cf


T12 = {"totalRevenue": {"value": 2_143_644, "source": "T12"},
       "totalOpex": {"value": 1_202_047, "source": "T12"},
       "noi": {"value": 941_597, "source": "T12"}}


# ---------------------------------------------------------------------------
# The anchor: Year-1 NOI in the projection IS the dial
# ---------------------------------------------------------------------------

def test_t12_branch_year1_noi_equals_the_dial():
    deal = _deal(noi=941_597)
    _, _, cf = _year1_row(deal, T12)
    assert abs(cf.rows[0].noi - deal.noi) < 0.01


def test_dialing_noi_up_moves_the_projection_one_for_one():
    """The Hampton screen: dial at $1,144,070 while the T-12 read $941,597.
    Before the fix the IRR engine never saw the extra $202,473."""
    lo = _deal(noi=941_597)
    hi = _deal(noi=1_144_070)
    _, _, cf_lo = _year1_row(lo, T12)
    _, _, cf_hi = _year1_row(hi, T12)
    assert abs(cf_hi.rows[0].noi - cf_lo.rows[0].noi - 202_473) < 0.01
    assert abs(cf_hi.rows[0].noi - 1_144_070) < 0.01


def test_vacancy_is_charged_exactly_once():
    """T-12 total revenue is collected income. Grossed up and then reduced by
    the dialed vacancy it must land back on itself - never 7% below."""
    deal = _deal(noi=941_597, vac=7)
    gpr, _, cf = _year1_row(deal, T12)
    assert abs(cf.rows[0].egi - 2_143_644) < 0.01
    assert abs(gpr * (1 - 0.07) - 2_143_644) < 0.01
    assert cf.rows[0].vacancy_loss > 0            # the model still takes it once


def test_dial_on_the_t12_noi_reproduces_the_t12_opex_line():
    deal = _deal(noi=941_597)
    _, expenses, _ = _year1_row(deal, T12)
    assert abs(expenses - 1_202_047) < 0.01


def test_no_t12_fallback_is_still_anchored_to_the_dial():
    deal = _deal(noi=350_000)
    _, _, cf = _year1_row(deal, None)
    assert abs(cf.rows[0].noi - 350_000) < 0.01


def test_dial_above_collected_income_floors_expenses_at_zero():
    deal = _deal(noi=9_999_999)
    _, expenses, _ = _year1_row(deal, T12)
    assert expenses == 0.0


def test_post_sale_adjustments_still_layer_on_top():
    """The tax step-up and insurance escalator are overlays ABOVE the anchored
    Year 1, exactly as before - they are not folded into the dial."""
    base = _deal(pp=17_000_000, noi=941_597)
    taxed = _deal(pp=17_000_000, noi=941_597, tax_reassessment_on=True)
    insured = _deal(pp=17_000_000, noi=941_597, insurance_escalator_on=True)
    src = dict(T12, t12_fixedCharges={"realEstateTaxes": {"value": 90_000}})
    _, e_base, _ = _year1_row(base, src, units=120, city="Hampton")
    _, e_tax, _ = _year1_row(taxed, src, units=120, city="Hampton")
    _, e_ins, _ = _year1_row(insured, src, units=120, city="Hampton")
    # new tax = 17M x 85% x 1.16 / 100 = 167,620; delta over the 90K line
    assert abs(e_tax - e_base - (167_620 - 90_000)) < 0.01
    assert abs(e_ins - e_base - 50 * 120) < 0.01


def test_wrapped_and_bare_sources_agree():
    bare = {"totalRevenue": 2_143_644, "totalOpex": 1_202_047}
    assert derive_year1_inputs(_deal(), bare, 100) == \
        derive_year1_inputs(_deal(), T12, 100)


def test_junk_sources_fall_back_instead_of_crashing():
    junk = {"totalRevenue": {"value": "n/a"}, "totalOpex": None}
    gpr, expenses = derive_year1_inputs(_deal(), junk, 100)
    assert gpr > 0 and expenses > 0


# ---------------------------------------------------------------------------
# Regression: Hampton Community Townhomes, the screen of 2026-09-24
# ---------------------------------------------------------------------------

def test_hampton_community_irr_is_no_longer_negative():
    """$17.0M, 30% down, 6.25%, 25-yr amort, 7% vacancy, 3%/3% growth, 7.5%
    exit, 5-yr hold, 4% AM fee, dial NOI $1,144,070 on a $941,597 T-12.
    Screen showed IRR -22.4% / EM 0.2x with NOI FALLING 19% over the hold.
    With one NOI everywhere the projection grows with rents and the IRR is
    the thin-but-positive number the cap rate implies."""
    deal = _deal(pp=17_000_000, noi=1_144_070, dp=30, ir=6.25, vac=7, rg=3,
                 eg=3, xc=7.5, hp=5, amf=4)
    gpr, expenses = derive_year1_inputs(deal, T12, 120, city="Hampton")
    debt = build_debt_schedule(
        DebtTerms(loan_amount=deal.loan_amount, annual_rate=deal.interest_rate,
                  amort_months=config.AMORT_MONTHS, io_years=0), 5)
    y1_vac = effective_year1_vacancy(deal.vacancy_frac, 0.0, 0)
    cf = build_cashflow(
        year1_gpr=gpr, year1_vacancy_pct=y1_vac, year1_expenses=expenses,
        rent_growth=.03, expense_growth=.03, am_fee_pct=.04, debt=debt,
        hold_years=5, exit_cap=.075, equity_raise=deal.equity_raise,
        stabilized_vacancy_pct=deal.vacancy_frac, stabilization_year_break=1,
    )
    irr = project_irr(equity_raise=deal.project_equity,
                      annual_cashflows=[r.cash_flow for r in cf.rows],
                      exit_proceeds_net=cf.exit_proceeds_net)
    nois = [r.noi for r in cf.rows]
    assert abs(nois[0] - 1_144_070) < 0.01
    assert all(b > a for a, b in zip(nois, nois[1:], strict=False)), "NOI must grow with rents"
    assert cf.equity_multiple > 1.0
    assert irr is not None and 0.05 < irr < 0.15      # thin, positive - as the 6.7% cap implies


# ---------------------------------------------------------------------------
# One implementation, every surface
# ---------------------------------------------------------------------------

def test_every_surface_imports_the_one_derivation():
    import core.artifact_engine as ae
    import ui.exec_summary as es
    import ui.underwriting as uw
    import ui.waterfall_view as wv
    from core import year1_inputs

    assert uw._derive_year1_inputs is year1_inputs.derive_year1_inputs
    assert es.derive_year1_inputs is year1_inputs.derive_year1_inputs
    assert wv.derive_year1_inputs is year1_inputs.derive_year1_inputs
    assert ae.derive_year1_inputs is year1_inputs.derive_year1_inputs
    # the hand copies are gone for good
    assert not hasattr(es, "_derive_year1")
    assert not hasattr(wv, "_derive_year1_inputs")
    assert not hasattr(ae, "_gpr_from_noi")
