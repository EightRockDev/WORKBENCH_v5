"""Tests for core.calibration — floor-aware direction resolution + override.

Strategy:
  - `_resolve_effective` is the heart of the floor rule. Hand-traced cases
    for both directions × {market widens / market compresses / no market /
    override} cover every code path.
  - End-to-end apply test uses a tmp SQLite DB so we don't touch live state.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from core import calibration
from core.calibration import ComputedThreshold, _Spec, _resolve_effective


# ---------------------------------------------------------------------------
# Direction semantics
# ---------------------------------------------------------------------------

def _spec_up(floor: float = 0.075) -> _Spec:
    return _Spec(
        name="TEST_UP",
        display_label="Test (conservative up)",
        units="pct", direction="conservative_up", category="returns",
        floor_value=floor,
    )


def _spec_down(floor: float = 130_000.0) -> _Spec:
    return _Spec(
        name="TEST_DOWN",
        display_label="Test (conservative down)",
        units="usd", direction="conservative_down", category="ppu",
        floor_value=floor,
    )


class TestConservativeUp:
    """e.g. GO_CAP. Higher = more conservative. Market may only widen ↑."""

    def test_market_widens_above_floor_wins(self):
        v, src = _resolve_effective(_spec_up(0.075), 0.0785, None)
        assert v == pytest.approx(0.0785)
        assert src == "market"

    def test_market_compresses_below_floor_held_at_floor(self):
        v, src = _resolve_effective(_spec_up(0.075), 0.0720, None)
        assert v == pytest.approx(0.075)
        assert src == "floor"

    def test_market_exactly_at_floor_uses_floor(self):
        v, src = _resolve_effective(_spec_up(0.075), 0.075, None)
        assert v == pytest.approx(0.075)
        assert src == "floor"

    def test_no_market_uses_floor(self):
        v, src = _resolve_effective(_spec_up(0.075), None, None)
        assert v == pytest.approx(0.075)
        assert src == "floor"

    def test_override_below_floor_wins(self):
        """The only way to compress below floor — Brian explicit override."""
        v, src = _resolve_effective(_spec_up(0.075), 0.0785, 0.0700)
        assert v == pytest.approx(0.0700)
        assert src == "override"

    def test_override_beats_market(self):
        v, src = _resolve_effective(_spec_up(0.075), 0.0900, 0.0700)
        assert v == pytest.approx(0.0700)
        assert src == "override"


class TestConservativeDown:
    """e.g. PPU_GO_NORFOLK. Lower = more conservative. Market may only widen ↓."""

    def test_market_below_floor_wins(self):
        v, src = _resolve_effective(_spec_down(132_000), 128_000, None)
        assert v == pytest.approx(128_000)
        assert src == "market"

    def test_market_above_floor_held_at_floor(self):
        v, src = _resolve_effective(_spec_down(132_000), 135_000, None)
        assert v == pytest.approx(132_000)
        assert src == "floor"

    def test_override_above_floor_wins(self):
        """Override can compress conservatism here (raise the ceiling)."""
        v, src = _resolve_effective(_spec_down(132_000), 128_000, 140_000)
        assert v == pytest.approx(140_000)
        assert src == "override"


# ---------------------------------------------------------------------------
# Threshold formatting + display helpers
# ---------------------------------------------------------------------------

def test_format_pct():
    assert calibration._format_for_units(0.0785, "pct") == "7.85%"


def test_format_usd():
    assert calibration._format_for_units(132_000.0, "usd") == "$132,000"


def test_format_ratio():
    assert calibration._format_for_units(1.30, "ratio") == "1.30x"


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------

def test_registry_includes_core_thresholds():
    names = {s.name for s in calibration.SPECS}
    for required in ("GO_CAP", "WATCH_CAP", "NOGO_CAP",
                     "MIN_DEBT_YIELD", "VACANCY_DEFAULT", "EXIT_CAP_DEFAULT"):
        assert required in names, f"missing {required} from SPECS"


def test_registry_includes_all_city_ppu_pairs():
    import config
    names = {s.name for s in calibration.SPECS}
    for city in config.CITY_PPU_CEILINGS:
        tok = calibration._normalize_city(city)
        assert f"PPU_GO_{tok}" in names
        assert f"PPU_WATCH_{tok}" in names


def test_city_normalize_roundtrip():
    for city in ("Norfolk", "Virginia Beach", "Newport News"):
        token = calibration._normalize_city(city)
        assert calibration._denormalize_city(token) == city


# ---------------------------------------------------------------------------
# End-to-end persistence
# ---------------------------------------------------------------------------

def test_apply_calibration_persists_and_reads_back(tmp_path: Path):
    db = tmp_path / "workbench.db"

    # Apply once with synthetic computed values (no ETL DB needed)
    computed = [
        ComputedThreshold(
            name="GO_CAP",
            market_value=0.0785,
            market_source="test fixture",
            market_as_of=dt.date(2026, 5, 21),
        ),
        ComputedThreshold(
            name="MIN_DEBT_YIELD",
            market_value=0.065,  # below floor 0.07 -> should be floored
            market_source="test fixture",
            market_as_of=dt.date(2026, 5, 21),
        ),
    ]
    applied = calibration.apply_calibration(computed, db_path=db)
    by_name = {t.name: t for t in applied}

    # GO_CAP: market widened above floor
    assert by_name["GO_CAP"].effective_source == "market"
    assert by_name["GO_CAP"].effective_value == pytest.approx(0.0785)

    # MIN_DEBT_YIELD: market wanted to compress, held at floor
    assert by_name["MIN_DEBT_YIELD"].effective_source == "floor"
    assert by_name["MIN_DEBT_YIELD"].effective_value == pytest.approx(0.07)

    # Read back via get_threshold
    fetched = calibration.get_threshold("GO_CAP", db_path=db)
    assert fetched is not None
    assert fetched.effective_value == pytest.approx(0.0785)
    assert fetched.market_source == "test fixture"

    # effective_value shortcut
    assert calibration.effective_value("GO_CAP", db_path=db) == pytest.approx(0.0785)
    assert calibration.effective_value("MIN_DEBT_YIELD", db_path=db) == pytest.approx(0.07)


def test_override_compresses_below_floor(tmp_path: Path):
    db = tmp_path / "workbench.db"
    calibration.apply_calibration([], db_path=db)

    # Override GO_CAP below floor — only way to compress
    t = calibration.set_override(
        "GO_CAP", 0.0700,
        reason="pilot test fixture",
        set_by="pytest",
        db_path=db,
    )
    assert t.effective_source == "override"
    assert t.effective_value == pytest.approx(0.0700)

    # Re-apply (simulating Monday cron) — override must survive
    calibration.apply_calibration([
        ComputedThreshold(
            name="GO_CAP",
            market_value=0.0800,
            market_source="test fixture",
            market_as_of=dt.date(2026, 5, 21),
        ),
    ], db_path=db)
    t = calibration.get_threshold("GO_CAP", db_path=db)
    assert t.effective_source == "override"
    assert t.effective_value == pytest.approx(0.0700)

    # Clear override → falls back to market (now above floor)
    t = calibration.clear_override("GO_CAP", db_path=db)
    assert t.effective_source == "market"
    assert t.effective_value == pytest.approx(0.0800)


def test_history_records_each_apply(tmp_path: Path):
    db = tmp_path / "workbench.db"
    for mv in (0.0760, 0.0780, 0.0800):
        calibration.apply_calibration([
            ComputedThreshold(
                name="GO_CAP", market_value=mv,
                market_source="fixture", market_as_of=dt.date(2026, 5, 21),
            ),
        ], db_path=db)

    history = calibration.get_history("GO_CAP", db_path=db)
    assert len(history) == 3
    # Newest first
    assert history[0][1] == pytest.approx(0.0800)
    assert history[-1][1] == pytest.approx(0.0760)


def test_get_threshold_falls_back_to_floor_when_db_missing(tmp_path: Path):
    """If the workbench.db doesn't exist (fresh checkout), accessors must
    still return Threshold objects pinned to the floor."""
    nonexistent = tmp_path / "nope.db"
    t = calibration.get_threshold("GO_CAP", db_path=nonexistent)
    assert t is not None
    assert t.effective_source == "floor"
    assert t.market_value is None
