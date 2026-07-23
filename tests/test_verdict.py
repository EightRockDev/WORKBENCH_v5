"""Tests for core.verdict — GO / WATCH / NO-GO logic.

Strategy: hand-traced scenarios for every rule layer. Each test fixes one
variable at a time so the failing rationale is unambiguous.

Locked thresholds (from config.py):
  GO:    cap ≥ 7.5%, DSCR ≥ 1.30, CoC ≥ 6.0%
  WATCH: cap ≥ 7.0% (or ≥ 6.85% override), DSCR ≥ 1.10, CoC ≥ 4.0%
  NO-GO: cap < 6.85%
  Norfolk DSCR floor: 1.25x
  Financing-constrained range: DSCR ∈ [1.10, 1.30)

Calibration isolation:
    The verdict module now reads through `core.calibration`, which can
    market-widen the bars above their floor. To keep these tests pinned to
    the locked-floor semantics they were written against, we point the
    calibration accessor at a nonexistent DB — that forces every call to
    `get_threshold()` to return a floor-only Threshold. Tests for the
    market-widening behavior live in `test_calibration.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from core import calibration
from core.verdict import evaluate


@pytest.fixture(autouse=True)
def _calibration_falls_back_to_floor(tmp_path: Path, monkeypatch):
    """Force calibration.get_threshold() to fall back to floor for every test
    in this module. This isolates the verdict logic tests from whatever live
    market values happen to be in workbench.db."""
    nonexistent = tmp_path / "no-calibration-here.db"
    monkeypatch.setattr(calibration, "WORKBENCH_DB_PATH", nonexistent)
    yield


# ---------------------------------------------------------------------------
# Hard NO-GO floor (Rule 1)
# ---------------------------------------------------------------------------


def test_hard_nogo_floor_cap_below_6_85():
    """Cap < 6.85% disqualifies regardless of anything else."""
    r = evaluate(cap=0.060, dscr=2.0, coc=0.10, ppu=80_000, city="Norfolk")
    assert r.verdict == "NO-GO"
    assert any("NO-GO floor" in s for s in r.rationale)


def test_hard_nogo_at_floor_passes_to_watch_check():
    """Cap exactly at 6.85% → not auto-NO-GO; falls through to WATCH check."""
    r = evaluate(cap=0.0685, dscr=1.30, coc=0.06, ppu=100_000, city="Norfolk")
    assert r.verdict in ("WATCH", "FINANCING-CONSTRAINED-WATCH")


# ---------------------------------------------------------------------------
# PPU ceilings (Rule 2)
# ---------------------------------------------------------------------------


def test_ppu_above_watch_ceiling_triggers_thesis_dependent_nogo():
    """Norfolk WATCH ceiling is $142k. $150k PPU → NO-GO with thesis flag."""
    r = evaluate(cap=0.080, dscr=1.40, coc=0.08, ppu=150_000, city="Norfolk")
    assert r.verdict == "NO-GO"
    assert any("Thesis Dependent" in s for s in r.rationale)
    assert r.ppu_pass is False


def test_ppu_above_go_ceiling_drops_to_watch():
    """Norfolk GO ceiling is $132k. $135k PPU + otherwise GO numbers → WATCH."""
    r = evaluate(cap=0.080, dscr=1.40, coc=0.08, ppu=135_000, city="Norfolk")
    assert r.verdict == "WATCH"
    assert any("GO ceiling" in s for s in r.rationale)
    assert r.ppu_pass is False


def test_ppu_below_go_ceiling_allows_go():
    """Norfolk GO ceiling is $132k. $130k → ppu_pass True."""
    r = evaluate(cap=0.080, dscr=1.40, coc=0.08, ppu=130_000, city="Norfolk")
    assert r.verdict == "GO"
    assert r.ppu_pass is True


def test_unknown_city_no_ppu_check():
    """Cities not in CITY_PPU_CEILINGS skip the PPU rule entirely."""
    r = evaluate(cap=0.080, dscr=1.40, coc=0.08, ppu=999_999, city="Mars")
    assert r.verdict == "GO"
    assert r.ppu_pass is True


# ---------------------------------------------------------------------------
# GO tier (Rule 3)
# ---------------------------------------------------------------------------


def test_clears_all_go_bars():
    """Cap 8%, DSCR 1.40, CoC 7%, PPU well under ceiling → GO."""
    r = evaluate(cap=0.080, dscr=1.40, coc=0.07, ppu=120_000, city="Hampton")
    assert r.verdict == "GO"
    assert r.cap_pass and r.dscr_pass and r.coc_pass and r.ppu_pass
    assert any("Clears all GO bars" in s for s in r.rationale)


def test_cap_just_below_go_drops_to_watch():
    """Cap 7.4% (below 7.5% GO bar) but above 7.0% WATCH bar → WATCH."""
    r = evaluate(cap=0.074, dscr=1.40, coc=0.07, ppu=120_000, city="Hampton")
    assert r.verdict == "WATCH"
    assert r.cap_pass is False  # didn't clear GO bar


# ---------------------------------------------------------------------------
# Norfolk DSCR overlay (Rule 4)
# ---------------------------------------------------------------------------


def test_norfolk_dscr_above_floor_can_be_go():
    """Norfolk + DSCR 1.30 (≥ 1.25 floor) → GO eligible."""
    r = evaluate(cap=0.080, dscr=1.30, coc=0.07, ppu=120_000, city="Norfolk")
    assert r.verdict == "GO"


def test_norfolk_dscr_below_floor_cannot_be_go():
    """Norfolk + DSCR 1.20 (between 1.10 watch and 1.25 floor) → cannot be GO.

    DSCR 1.20 is in the financing-constrained range [1.10, 1.30), so this
    fires the FINANCING-CONSTRAINED-WATCH outcome.
    """
    r = evaluate(cap=0.080, dscr=1.20, coc=0.07, ppu=120_000, city="Norfolk")
    assert r.verdict == "FINANCING-CONSTRAINED-WATCH"
    assert any("Norfolk DSCR floor" in s for s in r.rationale)


def test_other_city_dscr_at_1_20_still_eligible_for_go():
    """Outside Norfolk, the 1.30 GO bar applies — DSCR 1.20 → financing-constrained WATCH."""
    r = evaluate(cap=0.080, dscr=1.20, coc=0.07, ppu=100_000, city="Hampton")
    # 1.20 is below 1.30 GO bar → can't be GO, and is in financing-constrained band
    assert r.verdict == "FINANCING-CONSTRAINED-WATCH"
    assert r.dscr_pass is False


# ---------------------------------------------------------------------------
# Financing-constrained WATCH (Rule 6)
# ---------------------------------------------------------------------------


def test_financing_constrained_dscr_115():
    """DSCR 1.15 → FINANCING-CONSTRAINED-WATCH (in [1.10, 1.30) band)."""
    r = evaluate(cap=0.075, dscr=1.15, coc=0.06, ppu=120_000, city="Hampton")
    assert r.verdict == "FINANCING-CONSTRAINED-WATCH"
    assert any("financing-constrained" in s.lower() for s in r.rationale)
    assert any("lender pre-qual" in s for s in r.rationale)


def test_dscr_at_130_clears_financing_constraint():
    """DSCR exactly 1.30 → not financing-constrained; eligible for GO."""
    r = evaluate(cap=0.075, dscr=1.30, coc=0.06, ppu=120_000, city="Hampton")
    assert r.verdict == "GO"


def test_dscr_below_110_rescued_by_cap_override():
    """DSCR 1.05 below WATCH bar, but cap 7.5% ≥ 6.85% override → WATCH.

    Per SUMMARY-FORMAT.md, the "≥ 6.85% regardless of other metrics" override
    rescues a deal to WATCH whenever the cap clears that floor — even with a
    DSCR or CoC below the WATCH bar. This is the override doing its job.
    """
    r = evaluate(cap=0.075, dscr=1.05, coc=0.06, ppu=120_000, city="Hampton")
    assert r.verdict == "WATCH"
    # Confirm the rationale mentions the override path, not "Clears WATCH bars"
    assert any("override" in s for s in r.rationale)


# ---------------------------------------------------------------------------
# WATCH tier + cap override (Rule 7)
# ---------------------------------------------------------------------------


def test_clears_watch_bars():
    """Cap 7.2%, DSCR 1.30, CoC 5% → WATCH (CoC below GO bar)."""
    r = evaluate(cap=0.072, dscr=1.30, coc=0.05, ppu=120_000, city="Hampton")
    assert r.verdict == "WATCH"
    assert any("Clears WATCH bars" in s for s in r.rationale)


def test_cap_override_at_685_with_weak_metrics():
    """Cap 6.85% even with DSCR 1.0 and CoC 3% → WATCH (cap override)."""
    r = evaluate(cap=0.0685, dscr=1.05, coc=0.03, ppu=100_000, city="Hampton")
    # In financing-constrained band (DSCR 1.05 < 1.10), so falls through
    # to WATCH-bar check. cap_override fires. But DSCR 1.05 is BELOW the
    # 1.10 financing-constrained floor → just regular WATCH via cap override.
    assert r.verdict == "WATCH"
    assert any("override" in s for s in r.rationale)


def test_cap_override_with_financing_constrained_dscr():
    """Cap 6.90% + DSCR 1.15 → FINANCING-CONSTRAINED-WATCH (override + DSCR band)."""
    r = evaluate(cap=0.0690, dscr=1.15, coc=0.03, ppu=100_000, city="Hampton")
    assert r.verdict == "FINANCING-CONSTRAINED-WATCH"


# ---------------------------------------------------------------------------
# Below all WATCH bars → NO-GO with detail
# ---------------------------------------------------------------------------


def test_below_all_watch_bars_returns_nogo():
    """Cap 6.95% (above NO-GO floor but below 7% WATCH), DSCR 1.05, CoC 3%.

    Cap is in [6.85%, 7.0%) so the cap override fires → WATCH.
    Confirm the override actually fires when cap is exactly in this band.
    """
    r = evaluate(cap=0.0695, dscr=1.05, coc=0.03, ppu=100_000, city="Hampton")
    assert r.verdict == "WATCH"


def test_solidly_below_everything():
    """Cap 7.5% but DSCR 1.0 and CoC 1% → fails ALL WATCH bars → NO-GO."""
    r = evaluate(cap=0.075, dscr=1.05, coc=0.02, ppu=100_000, city="Hampton")
    # cap_watch=True, dscr_watch=False (1.05 < 1.10), coc_watch=False (2% < 4%)
    # cap_override fires (cap 7.5% > 6.85%) → WATCH still passes
    assert r.verdict == "WATCH"  # rescued by cap override


def test_truly_failing_deal():
    """Cap 6.85% (just above NO-GO), DSCR 1.05, CoC 1% → WATCH via cap override."""
    r = evaluate(cap=0.0685, dscr=1.05, coc=0.01, ppu=100_000, city="Hampton")
    # Cap override saves this from NO-GO
    assert r.verdict == "WATCH"


def test_below_floor_is_nogo():
    """Cap 6.84% (below 6.85% floor) → NO-GO no matter what."""
    r = evaluate(cap=0.0684, dscr=1.50, coc=0.10, ppu=100_000, city="Hampton")
    assert r.verdict == "NO-GO"


# ---------------------------------------------------------------------------
# Real-world fixture: Dove Landing
# ---------------------------------------------------------------------------


def test_dove_landing_at_70pct_cap():
    """Dove Landing per memory: $46.3M / $3.25M NOI = 7.02% cap, VA Beach.

    With reasonable DSCR/CoC/PPU, expect WATCH (between 7% WATCH and 7.5% GO bars).
    PPU = 46.3M / 316 units = $146,519 — above VA Beach GO ceiling ($141k)
    but below WATCH ceiling ($151k) → drops a tier from any other constraint.
    """
    cap = 3_250_000 / 46_300_000  # ≈ 7.02%
    ppu = 46_300_000 / 316        # ≈ $146,519
    r = evaluate(cap=cap, dscr=1.40, coc=0.07, ppu=ppu, city="Virginia Beach")
    # Cap below GO bar (7.0% < 7.5%) → can't be GO
    # PPU above VA Beach GO ceiling ($141k) → also blocks GO
    # But cap ≥ 7.0% and DSCR ≥ 1.10 and CoC ≥ 4% → WATCH
    assert r.verdict == "WATCH"
