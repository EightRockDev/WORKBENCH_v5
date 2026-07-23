"""GO / WATCH / NO-GO verdict logic.

Applies five rule layers in order, taking the most conservative outcome:
  1. Hard NO-GO floor: cap < NOGO_CAP — disqualifies regardless of anything else.
  2. PPU above WATCH ceiling for the city → NO-GO ("Priced Above Submarket
     Ceiling — Thesis Dependent" per SUMMARY-FORMAT.md). Override possible
     with a documented value-add thesis (we surface the flag; we don't override).
  3. Global GO bars: cap ≥ GO_CAP, DSCR ≥ GO_DSCR, CoC ≥ GO_COC.
  4. Norfolk overlay: DSCR floor tightened to NORFOLK_DSCR_FLOOR; deals between
     WATCH_DSCR and NORFOLK_DSCR_FLOOR can be WATCH but cannot be GO.
  5. PPU above GO ceiling drops one tier (GO → WATCH).
  6. Financing-constrained: DSCR in [WATCH_DSCR, GO_DSCR) →
     "FINANCING-CONSTRAINED-WATCH" (lender pre-qual required before LOI).
  7. WATCH bars: cap ≥ WATCH_CAP AND DSCR ≥ WATCH_DSCR AND CoC ≥ WATCH_COC,
     OR cap ≥ NOGO_CAP as a standalone override (regardless of other metrics).

**Calibration-aware thresholds** (added 2026-05-26):
    Every cap-rate bar (GO_CAP, WATCH_CAP, NOGO_CAP) and every per-city PPU
    ceiling is read from `core.calibration` instead of `config.py` directly.
    Calibration returns the same locked floor when no market data has been
    applied yet, so this module's behavior is unchanged on a fresh workbench.
    When the Monday cron pushes a wider bar (e.g. 10Y treasuries spike → GO
    cap widens from 7.5% to 7.85%), the verdict tightens automatically and
    the rationale surfaces "GO Cap Rate 7.85% (market-widened from 7.50%
    floor; 10Y DGS10 4.85% + 300 bps spread)."

Conventions ratified by Brian on 2026-05-06; see memory file
`feedback_underwriting_conventions.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import config
from core import calibration

Verdict = Literal["GO", "WATCH", "NO-GO", "FINANCING-CONSTRAINED-WATCH"]


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    rationale: list[str] = field(default_factory=list)
    cap_pass: bool = False        # cap meets GO bar
    dscr_pass: bool = False       # dscr meets GO bar (incl. Norfolk overlay)
    coc_pass: bool = False        # coc meets GO bar
    ppu_pass: bool = True         # PPU below or at city GO ceiling


def _calibrated(name: str, fallback: float) -> tuple[float, calibration.Threshold | None]:
    """Read a calibrated threshold, falling back to the literal `config.py` floor
    if calibration hasn't been initialized. Returns (value, threshold_obj)."""
    t = calibration.get_threshold(name)
    if t is None:
        return fallback, None
    return t.effective_value, t


def _annotate_bar(rationale: list[str], t: calibration.Threshold | None, label: str) -> None:
    """If a threshold was widened from its floor (or overridden), surface
    why in the rationale. Keeps the user honest about which bar is in play."""
    if t is None:
        return
    if t.effective_source == "market":
        rationale.append(
            f"{label}: {t.format_value()} (market-widened from floor "
            f"{calibration._format_for_units(t.floor_value, t.units)}; "
            f"source: {t.market_source})"
        )
    elif t.effective_source == "override":
        rationale.append(
            f"{label}: {t.format_value()} (Brian override; "
            f"reason: {t.override_reason or 'n/a'})"
        )


def evaluate(
    *,
    cap: float,
    dscr: float,
    coc: float,
    ppu: float,
    city: str,
) -> VerdictResult:
    """Apply the Eight Rock GO / WATCH / NO-GO rules.

    Args:
        cap: cap rate as a fraction (e.g. 0.075 for 7.5%)
        dscr: debt service coverage ratio (e.g. 1.30)
        coc: cash-on-cash return as a fraction (e.g. 0.06 for 6.0%)
        ppu: price per unit in dollars (e.g. 130_000)
        city: subject city; checked against `config.CITY_PPU_CEILINGS`
    """
    rationale: list[str] = []

    # ---- Resolve calibrated thresholds (with floor fallbacks) ------------
    go_cap, go_cap_t = _calibrated("GO_CAP", config.GO_CAP)
    watch_cap, watch_cap_t = _calibrated("WATCH_CAP", config.WATCH_CAP)
    nogo_cap, nogo_cap_t = _calibrated("NOGO_CAP", config.NOGO_CAP)

    # DSCR / CoC bars stay literal (no market signal feeds them yet) ------
    go_dscr = config.GO_DSCR
    watch_dscr = config.WATCH_DSCR
    go_coc = config.GO_COC
    watch_coc = config.WATCH_COC
    norfolk_dscr_floor = config.NORFOLK_DSCR_FLOOR

    # PPU ceilings — calibrated per city -----------------------------------
    token = calibration._normalize_city(city)
    ppu_go_t = calibration.get_threshold(f"PPU_GO_{token}")
    ppu_watch_t = calibration.get_threshold(f"PPU_WATCH_{token}")
    config_city = config.CITY_PPU_CEILINGS.get(city, {})
    go_ceil: float | None = (
        ppu_go_t.effective_value if ppu_go_t is not None else config_city.get("go")
    )
    watch_ceil: float | None = (
        ppu_watch_t.effective_value if ppu_watch_t is not None else config_city.get("watch")
    )

    # ---- Per-bar pass flags (informational; the verdict logic uses them) ----
    cap_pass = cap >= go_cap
    coc_pass = coc >= go_coc
    norfolk_floor = (city == "Norfolk")
    if norfolk_floor:
        dscr_pass = dscr >= max(go_dscr, norfolk_dscr_floor)
    else:
        dscr_pass = dscr >= go_dscr

    above_go_ppu = go_ceil is not None and ppu > go_ceil
    above_watch_ppu = watch_ceil is not None and ppu > watch_ceil
    ppu_pass = not above_go_ppu

    # ---- Rule 1: hard NO-GO floor ----
    if cap < nogo_cap:
        rationale.append(
            f"Cap rate {cap:.2%} below NO-GO floor of {nogo_cap:.2%}"
        )
        _annotate_bar(rationale, nogo_cap_t, "NO-GO Cap Floor")
        return VerdictResult(
            verdict="NO-GO",
            rationale=rationale,
            cap_pass=False, dscr_pass=dscr_pass, coc_pass=coc_pass, ppu_pass=ppu_pass,
        )

    # ---- Rule 2: PPU above WATCH ceiling → NO-GO (thesis-dependent override) ----
    if above_watch_ppu:
        rationale.append(
            f"Priced Above Submarket Ceiling — Thesis Dependent: "
            f"PPU ${ppu:,.0f} > {city} WATCH ceiling ${watch_ceil:,.0f}"
        )
        _annotate_bar(rationale, ppu_watch_t, f"{city} WATCH PPU Ceiling")
        return VerdictResult(
            verdict="NO-GO",
            rationale=rationale,
            cap_pass=cap_pass, dscr_pass=dscr_pass, coc_pass=coc_pass, ppu_pass=False,
        )

    # ---- Now in WATCH-or-GO territory ----

    # Try GO first
    is_go = cap_pass and dscr_pass and coc_pass and ppu_pass
    if is_go:
        rationale.append("Clears all GO bars (cap, DSCR, CoC, PPU)")
        _annotate_bar(rationale, go_cap_t, "GO Cap Rate")
        _annotate_bar(rationale, ppu_go_t, f"{city} GO PPU Ceiling")
        return VerdictResult(
            verdict="GO",
            rationale=rationale,
            cap_pass=True, dscr_pass=True, coc_pass=True, ppu_pass=True,
        )

    # Annotate why GO didn't fire
    if above_go_ppu:
        rationale.append(
            f"PPU ${ppu:,.0f} exceeds {city} GO ceiling ${go_ceil:,.0f}"
        )
        _annotate_bar(rationale, ppu_go_t, f"{city} GO PPU Ceiling")
    if norfolk_floor and watch_dscr <= dscr < norfolk_dscr_floor:
        rationale.append(
            f"Norfolk DSCR floor not met ({dscr:.2f}x < {norfolk_dscr_floor:.2f}x)"
        )

    # WATCH tier check
    cap_watch = cap >= watch_cap
    dscr_watch = dscr >= watch_dscr
    coc_watch = coc >= watch_coc
    cap_override = cap >= nogo_cap  # NOGO_CAP override per SUMMARY-FORMAT
    qualifies_watch = (cap_watch and dscr_watch and coc_watch) or cap_override

    # Financing-constrained DSCR — fires whenever DSCR is in [WATCH, GO) range
    is_financing_constrained = (
        watch_dscr <= dscr < go_dscr
    )

    if qualifies_watch:
        if is_financing_constrained:
            rationale.append(
                f"DSCR {dscr:.2f}x in financing-constrained range "
                f"[{watch_dscr:.2f}x-{go_dscr:.2f}x); "
                "lender pre-qual required before LOI"
            )
            verdict = "FINANCING-CONSTRAINED-WATCH"
        else:
            if cap_override and not (cap_watch and dscr_watch and coc_watch):
                rationale.append(
                    f"Cap rate {cap:.2%} ≥ {nogo_cap:.2%} override; "
                    "below GO thresholds"
                )
                _annotate_bar(rationale, nogo_cap_t, "NO-GO Cap Override")
            else:
                rationale.append("Clears WATCH bars; below GO thresholds")
                _annotate_bar(rationale, watch_cap_t, "WATCH Cap Rate")
            verdict = "WATCH"
        return VerdictResult(
            verdict=verdict,
            rationale=rationale,
            cap_pass=cap_pass, dscr_pass=dscr_pass, coc_pass=coc_pass, ppu_pass=ppu_pass,
        )

    # ---- NO-GO with detail on which WATCH bars failed ----
    if not cap_watch:
        rationale.append(
            f"Cap rate {cap:.2%} below WATCH bar of {watch_cap:.2%}"
        )
        _annotate_bar(rationale, watch_cap_t, "WATCH Cap Rate")
    if not dscr_watch:
        rationale.append(
            f"DSCR {dscr:.2f}x below WATCH bar of {watch_dscr:.2f}x"
        )
    if not coc_watch:
        rationale.append(
            f"CoC {coc:.2%} below WATCH bar of {watch_coc:.2%}"
        )
    return VerdictResult(
        verdict="NO-GO",
        rationale=rationale,
        cap_pass=cap_pass, dscr_pass=dscr_pass, coc_pass=coc_pass, ppu_pass=ppu_pass,
    )
