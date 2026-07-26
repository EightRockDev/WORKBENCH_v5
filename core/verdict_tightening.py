"""Module E - bidirectional diligence-to-verdict tightening (spec 6.3).

The base verdict (core/verdict.py) is pure economics: cap, DSCR, CoC, PPU.
This layer composes onto it what the rest of the workbench has learned about
the deal, and only ever moves the verdict DOWN - a great DD file cannot make
a thin deal a GO:

  * **DD findings** - an open hard dealbreaker forces NO-GO; a CRITICAL risk
    score forces NO-GO; HIGH risk or a REJECT/FURTHER_DILIGENCE recommendation
    drops one tier; IC-readiness blockers cap a GO at WATCH ("a DD finding
    downgrade... can move GO -> WATCH automatically").
  * **Named stress overlays** - a failed 2008-style / COVID-style / insurance
    shock drops a GO one tier, with the failing overlay named in the rationale.
  * **Extraction QA** - blocking QA errors (broken tie-outs, low-confidence
    fields) cap a GO at WATCH: numbers that don't tie are not GO numbers.

Deterministic, order-independent (each source computes a cap, the final tier
is the minimum), and every downgrade adds a rationale line so the exec summary
can show WHY the tier moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import due_diligence as dd
from core.extraction_qa import QAReport
from core.stress_overlays import StressReport, stress_rationale
from core.verdict import VerdictResult

# Severity order. FINANCING-CONSTRAINED-WATCH sits with WATCH: both mean
# "not a clean GO, not dead".
_TIER_ORDER = {"GO": 0, "WATCH": 1, "FINANCING-CONSTRAINED-WATCH": 1, "NO-GO": 2}
_DOWN_ONE = {"GO": "WATCH", "WATCH": "NO-GO",
             "FINANCING-CONSTRAINED-WATCH": "NO-GO", "NO-GO": "NO-GO"}


@dataclass(frozen=True)
class TightenedVerdict:
    verdict: str                      # final tier after tightening
    base_verdict: str                 # what pure economics said
    downgraded: bool
    rationale: list[str] = field(default_factory=list)   # added lines only


def _floor(tier: str, at_least: str) -> str:
    """The worse of two tiers."""
    return tier if _TIER_ORDER[tier] >= _TIER_ORDER[at_least] else at_least


def tighten(
    base: VerdictResult,
    *,
    dd_state: "dd.DDState | None" = None,
    stress: StressReport | None = None,
    qa: QAReport | None = None,
) -> TightenedVerdict:
    """Compose DD, stress and QA signals onto the economic verdict."""
    tier = base.verdict
    added: list[str] = []

    # ---- DD findings -----------------------------------------------------
    if dd_state is not None:
        hard, _soft = dd.list_open_dealbreakers(dd_state.items)
        if hard:
            tier = _floor(tier, "NO-GO")
            added.append(
                f"DD: {len(hard)} open hard dealbreaker(s) - "
                + "; ".join(h["title"] for h in hard[:3]))
        if dd_state.overall_risk_level == "CRITICAL":
            tier = _floor(tier, "NO-GO")
            added.append(
                f"DD: overall risk CRITICAL (score {dd_state.overall_risk_score:.0f})")
        elif dd_state.overall_risk_level == "HIGH":
            new = _DOWN_ONE[tier]
            if new != tier:
                tier = new
                added.append(
                    f"DD: overall risk HIGH (score {dd_state.overall_risk_score:.0f}) "
                    "- verdict dropped one tier")
        if dd_state.recommendation in ("REJECT", "FURTHER_DILIGENCE") and tier == "GO":
            tier = "WATCH"
            added.append(f"DD recommendation is {dd_state.recommendation} - GO withheld")
        # IC-readiness: a GO cannot stand on an incomplete DD file.
        readiness = dd.ic_readiness(dd_state)
        if not readiness.is_ready and tier == "GO":
            tier = "WATCH"
            added.append("DD not IC-ready: " + "; ".join(readiness.blocking_reasons[:2]))

    # ---- Named stress overlays ------------------------------------------
    if stress is not None and stress.any_failed and tier == "GO":
        tier = "WATCH"
        added.extend(stress_rationale(stress))

    # ---- Extraction QA ---------------------------------------------------
    if qa is not None and qa.blocking and tier == "GO":
        tier = "WATCH"
        bits = []
        if qa.errors:
            bits.append(f"{len(qa.errors)} failed tie-out(s): "
                        + "; ".join(c.title for c in qa.errors[:2]))
        if qa.low_confidence:
            bits.append(f"{len(qa.low_confidence)} low-confidence extracted field(s)")
        added.append("Extraction QA blocking - " + " | ".join(bits))

    return TightenedVerdict(
        verdict=tier,
        base_verdict=base.verdict,
        downgraded=_TIER_ORDER[tier] > _TIER_ORDER[base.verdict],
        rationale=added,
    )
