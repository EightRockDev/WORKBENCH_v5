"""Tests for core.due_diligence — scoring, dealbreakers, IC readiness, IO."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from core import due_diligence as dd


# ---------------------------------------------------------------------------
# Bootstrap + IO
# ---------------------------------------------------------------------------

class TestBootstrap:
    def test_bootstrap_includes_all_categories(self):
        state = dd.bootstrap_default_state("Test Deal")
        cats = {i.category for i in state.items}
        for c in dd.CATEGORIES:
            assert c in cats, f"category {c} missing from default checklist"

    def test_bootstrap_sets_default_strategy(self):
        state = dd.bootstrap_default_state("Test Deal")
        assert state.investment_strategy == "value-add"

    def test_bootstrap_all_items_pending(self):
        state = dd.bootstrap_default_state("X")
        assert all(i.status == "pending" for i in state.items)

    def test_bootstrap_due_dates_in_future(self):
        state = dd.bootstrap_default_state("X")
        today = dt.date.today()
        for i in state.items:
            d = dt.date.fromisoformat(i.due_date)
            assert d >= today


class TestIO:
    def test_save_then_load_roundtrip(self, tmp_path: Path):
        state = dd.bootstrap_default_state("Round Trip Deal")
        state.items[0].status = "complete"
        state.items[0].risk_score = 25
        state.items[0].notes = "Done — clean title."
        dd.save_state(tmp_path, state)
        loaded = dd.load_state(tmp_path)
        assert loaded.deal_id == "Round Trip Deal"
        assert loaded.items[0].status == "complete"
        assert loaded.items[0].risk_score == 25
        assert loaded.items[0].notes == "Done — clean title."

    def test_load_missing_file_bootstraps(self, tmp_path: Path):
        state = dd.load_state(tmp_path)
        assert state.deal_id == tmp_path.name
        assert len(state.items) == len(dd.DEFAULT_CHECKLIST)

    def test_load_corrupt_file_falls_back(self, tmp_path: Path):
        (tmp_path / "dd.json").write_text("not json", encoding="utf-8")
        state = dd.load_state(tmp_path)
        assert len(state.items) > 0  # fell back to bootstrap


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_risk_level_thresholds(self):
        assert dd.risk_level_for_score(0) == "LOW"
        assert dd.risk_level_for_score(25) == "LOW"
        assert dd.risk_level_for_score(26) == "MEDIUM"
        assert dd.risk_level_for_score(50) == "MEDIUM"
        assert dd.risk_level_for_score(51) == "HIGH"
        assert dd.risk_level_for_score(75) == "HIGH"
        assert dd.risk_level_for_score(76) == "CRITICAL"
        assert dd.risk_level_for_score(100) == "CRITICAL"
        assert dd.risk_level_for_score(None) == "UNSCORED"

    def test_category_score_averages_scored_items(self):
        state = dd.bootstrap_default_state("X")
        # Score 3 financial items; leave the rest unscored
        fin = [i for i in state.items if i.category == "financial"][:3]
        fin[0].risk_score = 30
        fin[1].risk_score = 40
        fin[2].risk_score = 50
        score = dd.compute_category_score(state.items, "financial")
        assert score == pytest.approx(40.0)

    def test_category_score_none_when_all_unscored(self):
        state = dd.bootstrap_default_state("X")
        assert dd.compute_category_score(state.items, "financial") is None

    def test_overall_score_uses_strategy_weights(self):
        state = dd.bootstrap_default_state("X")
        state.investment_strategy = "value-add"
        # Score everything at 50 → overall should also be 50
        for i in state.items:
            i.risk_score = 50
        overall, _ = dd.compute_overall_score(state.items, "value-add")
        assert overall == pytest.approx(50.0)

    def test_overall_redistributes_weights_when_unscored(self):
        """If a high-weight category is unscored, weight should redistribute
        proportionally — overall = avg of scored cats only."""
        state = dd.bootstrap_default_state("X")
        # Score only environmental (weight 0.08 in value-add)
        for i in state.items:
            if i.category == "environmental":
                i.risk_score = 60
        overall, cats = dd.compute_overall_score(state.items, "value-add")
        assert cats["environmental"] == pytest.approx(60.0)
        # Only scored category → overall == that category's score
        assert overall == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Dealbreakers + recommendations
# ---------------------------------------------------------------------------

class TestDealbreakers:
    def test_open_hard_dealbreaker_lists(self):
        state = dd.bootstrap_default_state("X")
        item = state.items[0]
        item.is_dealbreaker_hit = True
        item.dealbreaker_type = "hard"
        item.status = "in-progress"
        hard, soft = dd.list_open_dealbreakers(state.items)
        assert len(hard) == 1
        assert hard[0]["item_id"] == item.id
        assert len(soft) == 0

    def test_completed_dealbreaker_not_listed(self):
        state = dd.bootstrap_default_state("X")
        item = state.items[0]
        item.is_dealbreaker_hit = True
        item.dealbreaker_type = "hard"
        item.status = "complete"
        hard, soft = dd.list_open_dealbreakers(state.items)
        assert len(hard) == 0

    def test_recommendation_reject_on_hard_db(self):
        state = dd.bootstrap_default_state("X")
        state.items[0].is_dealbreaker_hit = True
        state.items[0].dealbreaker_type = "hard"
        state.items[0].status = "in-progress"
        state = dd.recompute_aggregates(state)
        assert state.recommendation == "REJECT"

    def test_recommendation_proceed_when_clean(self):
        state = dd.bootstrap_default_state("X")
        state.investment_strategy = "value-add"
        # Score everything LOW
        for i in state.items:
            i.risk_score = 10
        state = dd.recompute_aggregates(state)
        # value-add proceed threshold is 35; 10 < 35
        assert state.recommendation == "PROCEED"

    def test_soft_db_without_mitigation_blocks_proceed(self):
        state = dd.bootstrap_default_state("X")
        state.investment_strategy = "value-add"
        for i in state.items:
            i.risk_score = 20
        # Add a soft dealbreaker with no mitigation
        state.items[5].is_dealbreaker_hit = True
        state.items[5].dealbreaker_type = "soft"
        state.items[5].soft_mitigation = ""
        state.items[5].status = "in-progress"
        state = dd.recompute_aggregates(state)
        assert state.recommendation == "FURTHER_DILIGENCE"

    def test_soft_db_with_long_mitigation_passes(self):
        state = dd.bootstrap_default_state("X")
        state.investment_strategy = "value-add"
        for i in state.items:
            i.risk_score = 20
        state.items[5].is_dealbreaker_hit = True
        state.items[5].dealbreaker_type = "soft"
        state.items[5].soft_mitigation = (
            "Full plumbing repipe budgeted at $108K (24 units × $4.5K) "
            "included in renovation scope."
        )
        state.items[5].status = "in-progress"
        state = dd.recompute_aggregates(state)
        # value-add: scores 20 → threshold is 35 for PROCEED, 45 for WITH_MIT
        assert state.recommendation in ("PROCEED_WITH_MITIGATIONS", "PROCEED")


# ---------------------------------------------------------------------------
# IC-readiness gate
# ---------------------------------------------------------------------------

class TestICReadiness:
    def test_not_ready_when_low_completion(self):
        state = dd.bootstrap_default_state("X")
        ready = dd.ic_readiness(state)
        assert not ready.is_ready
        assert ready.completion_pct == 0.0
        assert any("DD completion" in r for r in ready.blocking_reasons)

    def test_not_ready_with_open_hard_db(self):
        state = dd.bootstrap_default_state("X")
        # Complete 80% of items
        for i in state.items[: int(len(state.items) * 0.85)]:
            i.status = "complete"
        # Add a hard dealbreaker
        state.items[-1].is_dealbreaker_hit = True
        state.items[-1].dealbreaker_type = "hard"
        state.items[-1].status = "in-progress"
        ready = dd.ic_readiness(state)
        assert not ready.is_ready
        assert any("Hard dealbreaker" in r for r in ready.blocking_reasons)

    def test_not_ready_with_soft_db_no_mitigation(self):
        state = dd.bootstrap_default_state("X")
        for i in state.items[: int(len(state.items) * 0.85)]:
            i.status = "complete"
        state.items[-1].is_dealbreaker_hit = True
        state.items[-1].dealbreaker_type = "soft"
        state.items[-1].soft_mitigation = "too short"
        state.items[-1].status = "in-progress"
        ready = dd.ic_readiness(state)
        assert not ready.is_ready
        assert any("mitigation" in r for r in ready.blocking_reasons)

    def test_ready_when_all_gates_clear(self):
        state = dd.bootstrap_default_state("X")
        # Complete 90% of items
        for i in state.items[: int(len(state.items) * 0.92)]:
            i.status = "complete"
        ready = dd.ic_readiness(state)
        assert ready.is_ready
        assert ready.blocking_reasons == []


# ---------------------------------------------------------------------------
# Strategy threshold edge cases
# ---------------------------------------------------------------------------

class TestStrategyThresholds:
    def test_opportunistic_allows_higher_scores(self):
        """A score of 50 is REJECT for core but PROCEED_WITH_MITIGATIONS for opportunistic."""
        state_core = dd.bootstrap_default_state("X")
        state_core.investment_strategy = "core"
        for i in state_core.items:
            i.risk_score = 50
        state_core = dd.recompute_aggregates(state_core)
        assert state_core.recommendation == "REJECT"

        state_opp = dd.bootstrap_default_state("X")
        state_opp.investment_strategy = "opportunistic"
        for i in state_opp.items:
            i.risk_score = 50
        state_opp = dd.recompute_aggregates(state_opp)
        assert state_opp.recommendation in ("PROCEED_WITH_MITIGATIONS", "PROCEED")
