"""Tests for core.lp_gp_ledger + core.distribution_engine."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from core import distribution_engine as dist
from core import lp_gp_ledger as lg


# ---------------------------------------------------------------------------
# Ledger basics
# ---------------------------------------------------------------------------

class TestLedgerBasics:
    def test_add_investor(self):
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        assert len(ledger.investors) == 1
        assert inv.name == "Alice"
        assert inv.commitment == 500_000
        assert inv.kind == "LP"

    def test_capital_call_rolls_up(self):
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(ledger, inv.investor_id, 500_000)
        assert inv.called_capital == 500_000
        assert inv.unreturned_capital == 500_000

    def test_distribution_rolls_up(self):
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(ledger, inv.investor_id, 500_000)
        lg.record_distribution(ledger, inv.investor_id, 40_000, tier="pref")
        assert inv.distributions_received == 40_000
        # Unreturned = called - distributions
        assert inv.unreturned_capital == 460_000

    def test_total_committed_and_remaining(self):
        ledger = lg.Ledger(deal_id="Test", raise_target=2_000_000)
        lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.add_investor(ledger, "Bob", 800_000, "LP")
        assert ledger.total_committed == 1_300_000
        assert ledger.remaining_to_raise == 700_000

    def test_lps_vs_gps(self):
        ledger = lg.Ledger(deal_id="Test")
        lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.add_investor(ledger, "Brian", 0, "GP")
        assert len(ledger.lps()) == 1
        assert len(ledger.gps()) == 1


# ---------------------------------------------------------------------------
# Pref accrual math
# ---------------------------------------------------------------------------

class TestPrefAccrual:
    def test_one_year_at_eight_pct(self):
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(
            ledger, inv.investor_id, 500_000, date="2024-01-01",
        )
        # One year later — pref should be ~$40,000 (8% × $500k)
        accrued = lg.compute_accrued_pref(
            ledger, inv.investor_id,
            as_of_date=dt.date(2025, 1, 1),
        )
        assert accrued == pytest.approx(40_000, rel=0.005)

    def test_non_compounded(self):
        """After 2 years with no pref payment, pref = 16%, NOT 16.64%
        (which would be 8% compounded). Eight Rock convention is cumulative
        non-compounded."""
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(
            ledger, inv.investor_id, 500_000, date="2024-01-01",
        )
        accrued = lg.compute_accrued_pref(
            ledger, inv.investor_id,
            as_of_date=dt.date(2026, 1, 1),
        )
        # 2 years × 8% × $500k = $80k (NOT $83.2k which would be compounded)
        assert accrued == pytest.approx(80_000, rel=0.005)

    def test_distribution_reduces_pref(self):
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(
            ledger, inv.investor_id, 500_000, date="2024-01-01",
        )
        # Pay $30k pref distribution at year mark — leaves $10k accrued
        lg.record_distribution(
            ledger, inv.investor_id, 30_000, tier="pref", date="2025-01-01",
        )
        accrued = lg.compute_accrued_pref(
            ledger, inv.investor_id,
            as_of_date=dt.date(2025, 1, 1),
        )
        # 2024 is a leap year (366 days) so actual accrual is slightly >$40k
        # before the $30k pref payment; tolerance accommodates this.
        assert accrued == pytest.approx(10_000, abs=200)

    def test_roc_distribution_reduces_unreturned_capital(self):
        """ROC distribution reduces unreturned capital, so future pref accrual
        is on the lower balance."""
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(
            ledger, inv.investor_id, 500_000, date="2024-01-01",
        )
        # Pay $40k pref + $250k ROC at year 1
        lg.record_distribution(
            ledger, inv.investor_id, 40_000, tier="pref", date="2025-01-01",
        )
        lg.record_distribution(
            ledger, inv.investor_id, 250_000, tier="roc", date="2025-01-01",
        )
        # Year 2: pref on remaining $250k × 8% = $20k
        accrued = lg.compute_accrued_pref(
            ledger, inv.investor_id,
            as_of_date=dt.date(2026, 1, 1),
        )
        assert accrued == pytest.approx(20_000, rel=0.005)

    def test_gp_has_no_pref(self):
        ledger = lg.Ledger(deal_id="Test")
        gp = lg.add_investor(ledger, "Brian", 0, "GP")
        # Even if a GP had a "commitment" they don't accrue pref
        accrued = lg.compute_accrued_pref(ledger, gp.investor_id)
        assert accrued == 0.0


# ---------------------------------------------------------------------------
# Distribution Engine — the waterfall
# ---------------------------------------------------------------------------

class TestDistributionEngine:
    def _setup_2lp_deal(self) -> lg.Ledger:
        """Two LPs ($500k each), one GP ($0)."""
        ledger = lg.Ledger(deal_id="Test")
        alice = lg.add_investor(ledger, "Alice", 500_000, "LP")
        bob = lg.add_investor(ledger, "Bob", 500_000, "LP")
        brian = lg.add_investor(ledger, "Brian", 0, "GP")
        lg.record_capital_call(ledger, alice.investor_id, 500_000, date="2024-01-01")
        lg.record_capital_call(ledger, bob.investor_id, 500_000, date="2024-01-01")
        return ledger

    def test_zero_cash_no_payments(self):
        ledger = self._setup_2lp_deal()
        plan = dist.preview_distribution(ledger, 0)
        assert plan.tier1_pref_total == 0
        assert plan.total_paid == 0

    def test_small_pot_pays_pref_only(self):
        """$50k pot, total accrued pref ~$80k → tier 1 captures all $50k."""
        ledger = self._setup_2lp_deal()
        # 1 year later: each LP has $40k pref accrued = $80k total
        plan = dist.preview_distribution(
            ledger, available_cash=50_000,
            as_of_date=dt.date(2025, 1, 1),
        )
        assert plan.tier1_pref_total == pytest.approx(50_000, rel=0.01)
        assert plan.tier2_roc_total == 0
        # Each LP gets $25k (proportional)
        lp_prefs = [p.pref_paid for p in plan.lp_payments]
        assert all(p == pytest.approx(25_000, rel=0.01) for p in lp_prefs)

    def test_medium_pot_pays_pref_then_roc(self):
        """$200k pot — pref ($80k) + ROC ($120k of $1M)."""
        ledger = self._setup_2lp_deal()
        plan = dist.preview_distribution(
            ledger, available_cash=200_000,
            as_of_date=dt.date(2025, 1, 1),
        )
        assert plan.tier1_pref_total == pytest.approx(80_000, rel=0.005)
        assert plan.tier2_roc_total == pytest.approx(120_000, rel=0.005)

    def test_huge_pot_triggers_residual_70_30(self):
        """$5M pot — pref + ROC + residual split. GP gets 30% of residual."""
        ledger = self._setup_2lp_deal()
        plan = dist.preview_distribution(
            ledger, available_cash=5_000_000,
            as_of_date=dt.date(2025, 1, 1),
        )
        # Pref + ROC = $80k + $1M = $1.08M
        # Residual = $5M - $1.08M = $3.92M
        # LP residual = 70% = $2.744M; GP residual = 30% = $1.176M
        assert plan.tier3_residual_lp_total == pytest.approx(3_920_000 * 0.70, rel=0.005)
        assert plan.tier3_residual_gp_total == pytest.approx(3_920_000 * 0.30, rel=0.005)
        # GP gets the entire promote pot since there's only 1 GP
        gp_promotes = [p.promote_paid for p in plan.gp_payments]
        assert gp_promotes[0] == pytest.approx(3_920_000 * 0.30, rel=0.005)

    def test_apply_distribution_commits_events(self):
        ledger = self._setup_2lp_deal()
        plan = dist.preview_distribution(
            ledger, available_cash=50_000,
            as_of_date=dt.date(2025, 1, 1),
        )
        events_before = len(ledger.events)
        n_added = dist.apply_distribution(ledger, plan)
        assert n_added == 2     # both LPs got pref payments
        assert len(ledger.events) == events_before + 2

    def test_no_lps_returns_safe_plan(self):
        ledger = lg.Ledger(deal_id="Test")
        lg.add_investor(ledger, "Brian", 0, "GP")
        plan = dist.preview_distribution(ledger, 100_000)
        assert plan.total_paid == 0
        assert "No LPs" in plan.trace[0]


# ---------------------------------------------------------------------------
# IO round-trip
# ---------------------------------------------------------------------------

class TestIO:
    def test_save_load_roundtrip(self, tmp_path: Path):
        ledger = lg.Ledger(deal_id="Round Trip", raise_target=1_500_000)
        alice = lg.add_investor(ledger, "Alice", 500_000, "LP")
        lg.record_capital_call(ledger, alice.investor_id, 500_000, notes="initial sub")
        lg.record_distribution(ledger, alice.investor_id, 10_000, tier="pref")

        lg.save(tmp_path, ledger)
        loaded = lg.load(tmp_path)

        assert loaded.deal_id == "Round Trip"
        assert loaded.raise_target == 1_500_000
        assert len(loaded.investors) == 1
        assert loaded.investors[0].name == "Alice"
        assert loaded.investors[0].called_capital == 500_000
        assert loaded.investors[0].distributions_received == 10_000
        assert len(loaded.events) == 2

    def test_load_missing_returns_empty(self, tmp_path: Path):
        ledger = lg.load(tmp_path)
        assert len(ledger.investors) == 0
        assert len(ledger.events) == 0
        assert ledger.deal_id == tmp_path.name


# ---------------------------------------------------------------------------
# Delete + rebuild self-healing
# ---------------------------------------------------------------------------

class TestRollupSelfHealing:
    def test_delete_event_recomputes(self):
        ledger = lg.Ledger(deal_id="Test")
        inv = lg.add_investor(ledger, "Alice", 500_000, "LP")
        ev = lg.record_capital_call(ledger, inv.investor_id, 500_000)
        assert inv.called_capital == 500_000

        lg.delete_event(ledger, ev.event_id)
        assert inv.called_capital == 0
