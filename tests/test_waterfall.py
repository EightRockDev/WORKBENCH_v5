"""Tests for core.waterfall — 3-tier waterfall mechanics.

Strategy: hand-computed fixtures with explicit expected numbers. The waterfall
is the most load-bearing math in the project (every LP IRR depends on it), so
each subtle rule gets its own targeted test:

  - pref_carryforward: pot < pref → unpaid rolls forward
  - shrinking_pref_base: ROC reduces next year's pref accrual base
  - non_compounding: pref does NOT earn pref while carried forward
  - sale_year_combined_pot: year-N operating + sale proceeds run through
                              the whole waterfall as one
  - residual_split: 70/30 promote on what's left
  - zero_pots: pref keeps accruing but nothing flows to LP/GP
"""

from __future__ import annotations

import pytest

from core.waterfall import run_waterfall

# ---------------------------------------------------------------------------
# Tier 1 — preferred return mechanics
# ---------------------------------------------------------------------------


def test_pref_paid_in_full_when_pot_exceeds_pref_only():
    """Pot exactly equals pref → all pref paid, no ROC, no residual."""
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[800_000],          # 8% × $10M = $800k
        pref_rate=0.08,
        lp_split=0.70,
        gp_split=0.30,
    )
    yr = result.years[0]
    assert yr.pref_accrued_this_year == pytest.approx(800_000)
    assert yr.pref_paid == pytest.approx(800_000)
    assert yr.pref_owed_end == 0.0
    assert yr.roc_paid == 0.0
    assert yr.residual == 0.0
    assert yr.lp_distribution == pytest.approx(800_000)
    assert yr.gp_distribution == 0.0


def test_pref_carryforward_when_pot_below_pref():
    """Year 1 pot $400k < $800k pref → $400k carries to year 2.

    Year 2 pref accrued is STILL $800k (8% × $10M unchanged LP capital).
    With $1.2M pot in year 2, the full $400k carry + $800k current pref pay,
    nothing left for ROC.
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[400_000, 1_200_000],
        pref_rate=0.08,
        lp_split=0.70,
        gp_split=0.30,
    )
    y1, y2 = result.years
    # Year 1: pot < pref, partial pay, $400k carry
    assert y1.pref_accrued_this_year == pytest.approx(800_000)
    assert y1.pref_paid == pytest.approx(400_000)
    assert y1.pref_owed_end == pytest.approx(400_000)
    assert y1.roc_paid == 0.0
    # Year 2: pref base UNCHANGED ($10M) because no ROC happened in year 1
    assert y2.pref_accrued_this_year == pytest.approx(800_000)
    assert y2.pref_owed_start == pytest.approx(400_000)  # carryforward from y1
    assert y2.pref_paid == pytest.approx(1_200_000)      # 400 carry + 800 current
    assert y2.pref_owed_end == 0.0
    assert y2.roc_paid == 0.0


def test_pref_does_not_compound():
    """Non-compounding: $800k unpaid pref in Y1 does NOT earn 8% in Y2.

    Y1: pot=0 → $800k pref accrues, $800k carries.
    Y2: pot=0 → another $800k accrues. Carryforward = $1.6M, not $864k.
    Y3: pot=0 → another $800k accrues. Carryforward = $2.4M.
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[0, 0, 0],
        pref_rate=0.08,
    )
    # Carryforward grows linearly, not exponentially
    assert result.years[0].pref_owed_end == pytest.approx(800_000)
    assert result.years[1].pref_owed_end == pytest.approx(1_600_000)
    assert result.years[2].pref_owed_end == pytest.approx(2_400_000)
    # Each year's accrual is exactly $800k (pref base unchanged because no ROC)
    for yr in result.years:
        assert yr.pref_accrued_this_year == pytest.approx(800_000)


# ---------------------------------------------------------------------------
# Tier 2 — Return of Capital, with shrinking pref base
# ---------------------------------------------------------------------------


def test_shrinking_pref_base_after_roc():
    """Critical mechanic: Y1 ROC of $1.2M shrinks LP capital to $8.8M.

    Y2 pref accrual must be 8% × $8.8M = $704k, NOT 8% × $10M = $800k.
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[2_000_000, 100_000],   # Y1 has plenty for pref + ROC
        pref_rate=0.08,
    )
    y1, y2 = result.years
    # Y1: pay $800k pref, then ROC the $1.2M remaining
    assert y1.pref_paid == pytest.approx(800_000)
    assert y1.roc_paid == pytest.approx(1_200_000)
    assert y1.lp_capital_remaining_end == pytest.approx(8_800_000)
    # Y2: pref base is now $8.8M → accrual is $704k
    assert y2.pref_accrued_this_year == pytest.approx(704_000)


def test_full_roc_then_residual_splits():
    """One huge year clears all pref + ROC, residual splits 70/30."""
    # $10M equity, single $30M pot in year 1.
    # Pref: $800k. ROC: $10M. Residual: $19.2M.
    # LP gets: 800k + 10M + 70% × 19.2M = 800k + 10M + 13.44M = $24.24M
    # GP gets: 30% × 19.2M = $5.76M
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[30_000_000],
        pref_rate=0.08,
        lp_split=0.70,
        gp_split=0.30,
    )
    yr = result.years[0]
    assert yr.pref_paid == pytest.approx(800_000)
    assert yr.roc_paid == pytest.approx(10_000_000)
    assert yr.lp_capital_remaining_end == 0.0
    assert yr.residual == pytest.approx(19_200_000)
    assert yr.lp_distribution == pytest.approx(24_240_000)  # 800k + 10M + 13.44M
    assert yr.gp_distribution == pytest.approx(5_760_000)   # 30% × 19.2M


# ---------------------------------------------------------------------------
# Tier 3 — Residual split mechanics
# ---------------------------------------------------------------------------


def test_residual_split_70_30():
    """Default split is 70 LP / 30 GP per Eight Rock convention."""
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[20_000_000],
    )
    yr = result.years[0]
    # Residual = 20M - 800k pref - 10M ROC = 9.2M
    assert yr.residual == pytest.approx(9_200_000)
    # 70/30 of 9.2M
    expected_lp_residual = 9_200_000 * 0.70
    expected_gp_residual = 9_200_000 * 0.30
    assert yr.gp_distribution == pytest.approx(expected_gp_residual)
    # LP = pref + roc + residual share
    assert yr.lp_distribution == pytest.approx(
        800_000 + 10_000_000 + expected_lp_residual
    )


def test_invalid_split_sum_raises():
    with pytest.raises(ValueError):
        run_waterfall(
            equity_raise=10_000_000,
            annual_pots=[1_000_000],
            lp_split=0.60,
            gp_split=0.30,  # only sums to 0.90
        )


# ---------------------------------------------------------------------------
# Cash flow vector shape — LP and GP IRR-ready
# ---------------------------------------------------------------------------


def test_lp_cashflows_vector_shape():
    """LP cashflows = [-equity, yr1, ..., yrN]; length N+1; first entry negative."""
    result = run_waterfall(equity_raise=10_000_000, annual_pots=[1_000_000] * 5)
    assert len(result.lp_cashflows) == 6
    assert result.lp_cashflows[0] == -10_000_000  # initial equity outflow
    # All subsequent entries non-negative (waterfall never gives negative LP dist)
    for cf in result.lp_cashflows[1:]:
        assert cf >= 0


def test_gp_cashflows_vector_shape():
    """GP cashflows = [0, yr1, ..., yrN]; length N+1; first entry zero (no co-invest)."""
    result = run_waterfall(equity_raise=10_000_000, annual_pots=[1_000_000] * 5)
    assert len(result.gp_cashflows) == 6
    assert result.gp_cashflows[0] == 0.0
    for cf in result.gp_cashflows[1:]:
        assert cf >= 0


def test_total_distributions_match_cashflow_sums():
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[1_500_000, 1_500_000, 1_500_000, 1_500_000, 30_000_000],
    )
    assert result.total_lp_distributions == pytest.approx(sum(result.lp_cashflows[1:]))
    assert result.total_gp_distributions == pytest.approx(sum(result.gp_cashflows[1:]))


# ---------------------------------------------------------------------------
# Sale-year combined pot (the critical end-of-life test)
# ---------------------------------------------------------------------------


def test_sale_year_combined_pot_full_clearance():
    """Hand-computed 5-year deal — sale proceeds clear pref, ROC, then split.

    Setup:
      Equity: $10M
      Pref:   8%
      Years 1-4: $1M operating CF each (just over the $800k pref → small ROC)
      Year 5: $1M operating CF + $20M net sale = $21M combined pot

    Hand math:
      Y1: pref=$800k on $10M, paid; ROC=$200k. LP cap = $9.8M.
      Y2: pref=$784k on $9.8M ($9.8M × 0.08), paid; ROC=$216k. LP cap = $9.584M.
      Y3: pref=$766.72k on $9.584M, paid; ROC=$233.28k. LP cap = $9.350720M.
      Y4: pref=$748.0576k on $9.350720M, paid; ROC=$251.9424k. LP cap = $9.098778M.
      Y5: pref=$727.9022k on $9.098778M. Pot=$21M. Pay pref. Remaining=$20.272098M.
          ROC=$9.098778M (all). Remaining=$11.173320M. Residual splits 70/30.
          LP gets: pref + roc + 70%*11.173320M = 727.9022k + 9.098778M + 7.821324M
                = $17.648M
          GP gets: 30%*11.173320M = $3.351996M
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[1_000_000, 1_000_000, 1_000_000, 1_000_000, 21_000_000],
        pref_rate=0.08,
        lp_split=0.70,
        gp_split=0.30,
    )
    # Year-by-year LP capital balances after ROC
    assert result.years[0].lp_capital_remaining_end == pytest.approx(9_800_000)
    assert result.years[1].lp_capital_remaining_end == pytest.approx(9_584_000)
    assert result.years[2].lp_capital_remaining_end == pytest.approx(9_350_720)
    assert result.years[3].lp_capital_remaining_end == pytest.approx(9_098_778, abs=1)
    # Year 5: full clearance + residual split
    y5 = result.years[4]
    assert y5.pref_accrued_this_year == pytest.approx(727_902.24, abs=1)
    assert y5.pref_paid == pytest.approx(727_902.24, abs=1)
    assert y5.roc_paid == pytest.approx(9_098_778, abs=1)
    assert y5.lp_capital_remaining_end == pytest.approx(0.0, abs=0.01)
    assert y5.residual == pytest.approx(11_173_320, abs=1)
    # LP and GP final-year distributions
    assert y5.lp_distribution == pytest.approx(17_648_000, abs=10)
    assert y5.gp_distribution == pytest.approx(3_351_996, abs=10)


def test_zero_operating_pots_then_huge_sale():
    """Pure value-add deal — no operating CF, all return at exit.

    Setup:
      Equity: $10M, hold 5 years, year 5 sale = $30M
      Years 1-4: $0 operating
      Year 5: $30M

    Hand math:
      Y1-Y4: pref accrues at $800k each year (LP cap unchanged at $10M),
             nothing paid → carryforward grows to $3,200k by start of Y5.
      Y5: pref accrued = $800k, total owed = $4M. Pot $30M.
          Pay $4M pref. Remaining $26M.
          ROC $10M. Remaining $16M.
          Residual 70/30 → LP $11.2M, GP $4.8M.
          LP total Y5 = $4M + $10M + $11.2M = $25.2M
          GP total Y5 = $4.8M
      Equity multiple (LP) = $25.2M / $10M = 2.52x
    """
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[0, 0, 0, 0, 30_000_000],
    )
    # Y1-Y4: pref carries, no distributions
    for y in range(4):
        assert result.years[y].pref_paid == 0.0
        assert result.years[y].roc_paid == 0.0
        assert result.years[y].lp_distribution == 0.0
        assert result.years[y].gp_distribution == 0.0
    assert result.years[3].pref_owed_end == pytest.approx(3_200_000)
    # Y5: full clearance
    y5 = result.years[4]
    assert y5.pref_paid == pytest.approx(4_000_000)
    assert y5.roc_paid == pytest.approx(10_000_000)
    assert y5.residual == pytest.approx(16_000_000)
    assert y5.lp_distribution == pytest.approx(25_200_000)
    assert y5.gp_distribution == pytest.approx(4_800_000)
    # LP equity multiple (sanity check, not the IRR-using one)
    assert result.total_lp_distributions / 10_000_000 == pytest.approx(2.52)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_invalid_equity_raise_raises():
    with pytest.raises(ValueError):
        run_waterfall(equity_raise=0, annual_pots=[1_000_000])
    with pytest.raises(ValueError):
        run_waterfall(equity_raise=-100, annual_pots=[1_000_000])


def test_empty_annual_pots_raises():
    with pytest.raises(ValueError):
        run_waterfall(equity_raise=10_000_000, annual_pots=[])


def test_negative_pot_treated_as_zero():
    """A negative pot (year had operating loss covered by debt) shouldn't break
    the waterfall — pref accrues, nothing is distributed."""
    result = run_waterfall(
        equity_raise=10_000_000,
        annual_pots=[-100_000, 1_000_000],
    )
    y1 = result.years[0]
    assert y1.pot == 0.0
    assert y1.pref_paid == 0.0
    assert y1.pref_owed_end == pytest.approx(800_000)
    assert y1.lp_distribution == 0.0
