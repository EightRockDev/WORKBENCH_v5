"""Module E - bidirectional DD-to-verdict tightening (spec 6.3)."""

from __future__ import annotations

import config
from core import due_diligence as dd
from core import stress_overlays as so
from core import verdict_tightening as vt
from core.extraction_qa import FieldFlag, QACheck, QAReport
from core.sensitivity import SensitivityBase
from core.verdict import evaluate


def _go_verdict():
    """A verdict that is a clean economic GO (Chesapeake avoids the Norfolk
    overlay; PPU under the city ceiling)."""
    v = evaluate(cap=0.078, dscr=1.4, coc=0.07, ppu=120_000, city="Chesapeake")
    assert v.verdict == "GO", v.rationale
    return v


def _watch_verdict():
    v = evaluate(cap=0.072, dscr=1.4, coc=0.07, ppu=120_000, city="Chesapeake")
    assert v.verdict == "WATCH", v.rationale
    return v


def _dd_state(**overrides) -> dd.DDState:
    state = dd.bootstrap_default_state("test-deal")
    # A clean, complete DD file by default.
    for item in state.items:
        item.status = "complete"
        item.risk_score = 10
    state = dd.recompute_aggregates(state)
    for key, val in overrides.items():
        setattr(state, key, val)
    return state


def _failing_stress() -> so.StressReport:
    thin = SensitivityBase(
        purchase_price=11_000_000, year1_gpr=1_200_000, year1_expenses=560_000,
        am_fee_pct=0.04, loan_amount=8_800_000, annual_rate=0.06,
        amort_months=config.AMORT_MONTHS, io_years=0, hold_years=5,
        exit_cap=0.055, equity_raise=2_200_000)
    report = so.run_stress_overlays(thin)
    assert report.any_failed
    return report


def _passing_stress() -> so.StressReport:
    strong = SensitivityBase(
        purchase_price=7_000_000, year1_gpr=1_400_000, year1_expenses=560_000,
        am_fee_pct=0.04, loan_amount=4_200_000, annual_rate=0.06,
        amort_months=config.AMORT_MONTHS, io_years=0, hold_years=5,
        exit_cap=0.075, equity_raise=3_000_000)
    return so.run_stress_overlays(strong)


def _blocking_qa() -> QAReport:
    return QAReport(
        checks=[QACheck("T12-NOI", "Revenue - Expenses ties to NOI", False,
                        "error", "660,000 vs 100,000")],
        low_confidence=[FieldFlag("totalRevenue", 0.4, "confidence 40%")])


# ---------------------------------------------------------------- passthrough

def test_clean_signals_leave_a_go_untouched():
    result = vt.tighten(_go_verdict(), dd_state=_dd_state(),
                        stress=None, qa=QAReport())
    assert result.verdict == "GO" and not result.downgraded
    assert result.rationale == []


def test_no_signals_at_all_is_a_passthrough():
    result = vt.tighten(_go_verdict())
    assert result.verdict == "GO" and not result.downgraded


# ---------------------------------------------------------------- DD signals

def test_open_hard_dealbreaker_forces_nogo():
    state = _dd_state()
    state.items[0].status = "in-progress"
    state.items[0].is_dealbreaker_hit = True
    state.items[0].dealbreaker_type = "hard"
    state = dd.recompute_aggregates(state)
    result = vt.tighten(_go_verdict(), dd_state=state)
    assert result.verdict == "NO-GO" and result.downgraded
    assert any("hard dealbreaker" in ln for ln in result.rationale)


def test_high_risk_drops_one_tier():
    state = _dd_state()
    for item in state.items:
        item.risk_score = 70          # HIGH band
    state = dd.recompute_aggregates(state)
    assert state.overall_risk_level == "HIGH"
    result = vt.tighten(_go_verdict(), dd_state=state)
    assert result.verdict == "WATCH" and result.downgraded


def test_critical_risk_forces_nogo():
    state = _dd_state()
    for item in state.items:
        item.risk_score = 95
    state = dd.recompute_aggregates(state)
    assert state.overall_risk_level == "CRITICAL"
    result = vt.tighten(_go_verdict(), dd_state=state)
    assert result.verdict == "NO-GO"


def test_incomplete_dd_caps_go_at_watch():
    """The spec's headline case: a DD downgrade moves GO -> WATCH automatically."""
    state = _dd_state()
    for item in state.items:
        item.status = "pending"       # nothing done yet
        item.risk_score = None
    state = dd.recompute_aggregates(state)
    result = vt.tighten(_go_verdict(), dd_state=state)
    assert result.verdict == "WATCH" and result.downgraded
    assert any("IC-ready" in ln or "FURTHER_DILIGENCE" in ln for ln in result.rationale)


def test_incomplete_dd_does_not_touch_a_watch():
    """Tightening only ever moves DOWN from GO for soft signals - an already-
    WATCH deal stays WATCH, it is not pushed to NO-GO by mere incompleteness."""
    state = _dd_state()
    for item in state.items:
        item.status = "pending"
        item.risk_score = None
    state = dd.recompute_aggregates(state)
    result = vt.tighten(_watch_verdict(), dd_state=state)
    assert result.verdict == "WATCH"


# ------------------------------------------------------------- stress signals

def test_failed_stress_overlay_moves_go_to_watch():
    result = vt.tighten(_go_verdict(), stress=_failing_stress())
    assert result.verdict == "WATCH" and result.downgraded
    assert any("Stress overlay" in ln for ln in result.rationale)


def test_passing_stress_leaves_go_alone():
    result = vt.tighten(_go_verdict(), stress=_passing_stress())
    assert result.verdict == "GO" or result.downgraded is False


def test_failed_stress_does_not_deepen_a_watch():
    result = vt.tighten(_watch_verdict(), stress=_failing_stress())
    assert result.verdict == "WATCH"


# ----------------------------------------------------------------- QA signals

def test_blocking_qa_caps_go_at_watch():
    result = vt.tighten(_go_verdict(), qa=_blocking_qa())
    assert result.verdict == "WATCH" and result.downgraded
    assert any("Extraction QA" in ln for ln in result.rationale)


def test_clean_qa_is_a_passthrough():
    result = vt.tighten(_go_verdict(), qa=QAReport())
    assert result.verdict == "GO"


# ------------------------------------------------------------------ combined

def test_worst_signal_wins():
    state = _dd_state()
    state.items[0].status = "in-progress"
    state.items[0].is_dealbreaker_hit = True
    state.items[0].dealbreaker_type = "hard"
    state = dd.recompute_aggregates(state)
    result = vt.tighten(_go_verdict(), dd_state=state,
                        stress=_failing_stress(), qa=_blocking_qa())
    assert result.verdict == "NO-GO"
    assert result.base_verdict == "GO"


def test_tightening_never_upgrades():
    """A NO-GO stays NO-GO no matter how clean the other signals are."""
    nogo = evaluate(cap=0.05, dscr=1.0, coc=0.02, ppu=200_000, city="Norfolk")
    assert nogo.verdict == "NO-GO"
    result = vt.tighten(nogo, dd_state=_dd_state(),
                        stress=_passing_stress(), qa=QAReport())
    assert result.verdict == "NO-GO" and not result.downgraded
