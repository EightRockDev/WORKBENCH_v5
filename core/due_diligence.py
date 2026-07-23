"""Due Diligence checklist + risk scoring per-property.

Seeded from the cre-due-diligence skill plugin's risk-scoring framework
(9 categories, ~10 factors per category, weighted by investment strategy).
Each property has a ``dd.json`` in its folder tracking the checklist state
and aggregated risk score.

Schema (``dd.json``)::

    {
        "deal_id":            "Green Tree",
        "investment_strategy": "value-add",
        "scoring_date":       "2026-05-26T14:30:00",
        "last_updated":       "2026-05-26T14:30:00",
        "items": [
            {
                "id":        "fin-1",
                "category":  "financial",
                "title":     "Validate T-12 against rent roll",
                "owner":     "brian",
                "due_date":  "2026-06-15",
                "status":    "in-progress",
                "notes":     "...",
                "artifacts": [
                    {"filename": "T12-Green-Tree-05262026.pdf",
                     "uploaded_at": "2026-05-26T...",
                     "ai_summary": "..."}
                ],
                "risk_score":   45,
                "risk_factor":  "financial.noi_trend",
                "is_dealbreaker_hit": false,
                "dealbreaker_type":   null
            },
            ...
        ],
        "category_scores":    {"ownershipTitle": 15, ...},
        "overall_risk_score": 42,
        "overall_risk_level": "MEDIUM",
        "dealbreakers":       [],
        "soft_dealbreakers":  [],
        "recommendation":     "PROCEED_WITH_MITIGATIONS"
    }

Status values: ``pending`` | ``in-progress`` | ``complete`` | ``n-a`` | ``blocked``

Bidirectional verdict gating:
    The Exec Summary tab calls ``ready_for_ic(folder)`` to check whether the
    deal can be marked GO. A deal is IC-ready only when:
      • ≥ 80% of DD items are in {"complete", "n-a"}
      • Zero open hard dealbreakers
      • All open soft dealbreakers have a documented mitigation note (≥ 40 chars)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants — risk categories + scoring (from risk-scoring.md)
# ---------------------------------------------------------------------------

Status = Literal["pending", "in-progress", "complete", "n-a", "blocked"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNSCORED"]
Recommendation = Literal[
    "PROCEED",
    "PROCEED_WITH_MITIGATIONS",
    "PROCEED_WITH_CAUTION",
    "FURTHER_DILIGENCE",
    "REJECT",
]
InvestmentStrategy = Literal[
    "core", "core-plus", "value-add", "opportunistic", "distressed"
]

CATEGORIES = (
    "ownershipTitle",
    "legalLitigation",
    "environmental",
    "zoningRegulatory",
    "financial",
    "market",
    "tenantConcentration",
    "physicalCondition",
    "regulatoryMultifamily",
)

CATEGORY_LABELS = {
    "ownershipTitle":         "Ownership & Title",
    "legalLitigation":        "Legal & Litigation",
    "environmental":          "Environmental",
    "zoningRegulatory":       "Zoning & Regulatory",
    "financial":              "Financial",
    "market":                 "Market",
    "tenantConcentration":    "Tenant Concentration",
    "physicalCondition":      "Physical Condition",
    "regulatoryMultifamily":  "Regulatory (Multifamily)",
}

CATEGORY_ICONS = {
    "ownershipTitle":         "📜",
    "legalLitigation":        "⚖️",
    "environmental":          "🌿",
    "zoningRegulatory":       "🏛️",
    "financial":              "💰",
    "market":                 "📈",
    "tenantConcentration":    "👥",
    "physicalCondition":      "🔧",
    "regulatoryMultifamily":  "📋",
}

# Risk weighting by strategy (from risk-scoring.md Risk Weighting section)
STRATEGY_WEIGHTS: dict[InvestmentStrategy, dict[str, float]] = {
    "core":          {"ownershipTitle": .10, "legalLitigation": .10, "environmental": .10,
                      "zoningRegulatory": .05, "financial": .25, "market": .15,
                      "tenantConcentration": .10, "physicalCondition": .05,
                      "regulatoryMultifamily": .10},
    "core-plus":     {"ownershipTitle": .10, "legalLitigation": .10, "environmental": .10,
                      "zoningRegulatory": .07, "financial": .22, "market": .14,
                      "tenantConcentration": .10, "physicalCondition": .07,
                      "regulatoryMultifamily": .10},
    "value-add":     {"ownershipTitle": .08, "legalLitigation": .08, "environmental": .08,
                      "zoningRegulatory": .08, "financial": .18, "market": .12,
                      "tenantConcentration": .10, "physicalCondition": .15,
                      "regulatoryMultifamily": .13},
    "opportunistic": {"ownershipTitle": .10, "legalLitigation": .10, "environmental": .10,
                      "zoningRegulatory": .10, "financial": .12, "market": .10,
                      "tenantConcentration": .08, "physicalCondition": .18,
                      "regulatoryMultifamily": .12},
    "distressed":    {"ownershipTitle": .12, "legalLitigation": .12, "environmental": .10,
                      "zoningRegulatory": .10, "financial": .10, "market": .08,
                      "tenantConcentration": .08, "physicalCondition": .20,
                      "regulatoryMultifamily": .10},
}

# Strategy-specific overall-score thresholds (from risk-scoring.md edge case #4)
STRATEGY_THRESHOLDS = {
    "core":          {"proceed": 20, "with_mit": 25, "with_cau": 35, "reject": 35},
    "core-plus":     {"proceed": 25, "with_mit": 35, "with_cau": 45, "reject": 45},
    "value-add":     {"proceed": 35, "with_mit": 45, "with_cau": 55, "reject": 55},
    "opportunistic": {"proceed": 45, "with_mit": 55, "with_cau": 65, "reject": 65},
    "distressed":    {"proceed": 55, "with_mit": 65, "with_cau": 75, "reject": 75},
}


def risk_level_for_score(score: float | None) -> RiskLevel:
    """Map a 0-100 numeric score to a risk level. None → UNSCORED."""
    if score is None:
        return "UNSCORED"
    if score <= 25:
        return "LOW"
    if score <= 50:
        return "MEDIUM"
    if score <= 75:
        return "HIGH"
    return "CRITICAL"


# ---------------------------------------------------------------------------
# Hard + soft dealbreaker definitions (from risk-scoring.md)
# ---------------------------------------------------------------------------

HARD_DEALBREAKERS = (
    "Active Superfund / NPL listing",
    "Unresolvable title dispute",
    "Active condemnation proceedings",
    "Structural failure / condemned",
    "DSCR < 0.90x (no viable restructure)",
    "Active criminal investigation involving property",
    "Fraud detected in seller financials",
    "Property in active receivership (REAP or similar)",
)

SOFT_DEALBREAKERS = (
    ("Environmental contamination (Phase II)",
     "Remediation cost estimate + insurance"),
    ("Galvanized/polybutylene plumbing throughout",
     "Full repipe budget in underwriting"),
    ("Strict rent control jurisdiction",
     "Adjusted return expectations + compliance plan"),
    (">50% lease expiration in 90 days",
     "Lease-up plan + carry cost budget"),
    ("Occupancy <75%",
     "Market study + lease-up timeline + bridge financing"),
    ("Single tenant >30% of revenue",
     "Diversification plan + tenant credit analysis"),
    ("Non-conforming use without grandfathering",
     "Legal opinion on pathway to conformity"),
    ("DSCR 0.90-1.10x",
     "Debt restructure or additional equity plan"),
)


# ---------------------------------------------------------------------------
# Default checklist — seeded from the risk-scoring categories
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _DefaultItem:
    id: str
    category: str
    title: str
    default_owner: str         # "brian" / "peter" / "vendor:title-co" etc.
    default_due_offset_days: int   # days from today
    risk_factor: str           # dot-path: "<category>.<factor_name>"


DEFAULT_CHECKLIST: list[_DefaultItem] = [
    # ---- Ownership & Title ----
    _DefaultItem("ot-1", "ownershipTitle", "Order title commitment / preliminary title report",
                 "vendor:title-co", 5, "ownershipTitle.title_disputes"),
    _DefaultItem("ot-2", "ownershipTitle", "Review ownership chain (last 5 years)",
                 "brian", 14, "ownershipTitle.ownership_changes"),
    _DefaultItem("ot-3", "ownershipTitle", "Verify tax payment status — no delinquency",
                 "brian", 14, "ownershipTitle.tax_payment_history"),
    _DefaultItem("ot-4", "ownershipTitle", "Confirm entity structure (LLC, members, signers)",
                 "brian", 14, "ownershipTitle.entity_structure"),
    _DefaultItem("ot-5", "ownershipTitle", "Review easements / encumbrances on title",
                 "vendor:title-co", 21, "ownershipTitle.easements"),

    # ---- Legal & Litigation ----
    _DefaultItem("ll-1", "legalLitigation", "Court records search — active lawsuits",
                 "vendor:legal-search", 21, "legalLitigation.active_lawsuits"),
    _DefaultItem("ll-2", "legalLitigation", "Tenant litigation history (3 years)",
                 "vendor:legal-search", 21, "legalLitigation.tenant_lawsuits"),
    _DefaultItem("ll-3", "legalLitigation", "Code violations — pull city records",
                 "peter", 14, "legalLitigation.code_violations"),
    _DefaultItem("ll-4", "legalLitigation", "Permit history review",
                 "peter", 21, "legalLitigation.permit_history"),

    # ---- Environmental ----
    _DefaultItem("env-1", "environmental", "Order Phase I ESA",
                 "vendor:environmental", 14, "environmental.phase_i_esa"),
    _DefaultItem("env-2", "environmental", "Asbestos screen (pre-1981 vintage)",
                 "vendor:environmental", 21, "environmental.asbestos"),
    _DefaultItem("env-3", "environmental", "Lead paint screen (pre-1978 vintage)",
                 "vendor:environmental", 21, "environmental.lead_paint"),
    _DefaultItem("env-4", "environmental", "Flood zone verification (FEMA)",
                 "brian", 7, "environmental.flood_zone"),
    _DefaultItem("env-5", "environmental", "Mold / moisture inspection",
                 "vendor:inspection", 21, "environmental.mold_history"),
    _DefaultItem("env-6", "environmental", "Proximity to Superfund / NPL search",
                 "brian", 7, "environmental.superfund_proximity"),

    # ---- Zoning & Regulatory ----
    _DefaultItem("zr-1", "zoningRegulatory", "Zoning verification letter from city",
                 "peter", 14, "zoningRegulatory.current_use_conformity"),
    _DefaultItem("zr-2", "zoningRegulatory", "Density compliance check",
                 "peter", 14, "zoningRegulatory.density_compliance"),
    _DefaultItem("zr-3", "zoningRegulatory", "Parking compliance vs code",
                 "peter", 14, "zoningRegulatory.parking_compliance"),
    _DefaultItem("zr-4", "zoningRegulatory", "Building code (renovation triggers)",
                 "vendor:architect", 30, "zoningRegulatory.building_code_compliance"),

    # ---- Financial ----
    _DefaultItem("fin-1", "financial", "Validate T-12 against rent roll",
                 "brian", 7, "financial.seller_financials"),
    _DefaultItem("fin-2", "financial", "OpEx benchmarking vs Class C/HR",
                 "brian", 14, "financial.opex_ratio"),
    _DefaultItem("fin-3", "financial", "NOI trend analysis (3-year)",
                 "brian", 14, "financial.noi_trend"),
    _DefaultItem("fin-4", "financial", "Verify DSCR under stress (Beardsley 4-scenario)",
                 "brian", 14, "financial.dscr"),
    _DefaultItem("fin-5", "financial", "Debt yield vs lender minimum",
                 "brian", 14, "financial.debt_yield"),
    _DefaultItem("fin-6", "financial", "Cap rate spread vs amortized debt constant",
                 "brian", 7, "financial.cap_rate_spread"),
    _DefaultItem("fin-7", "financial", "In-place rent vs market gap",
                 "brian", 14, "financial.rent_vs_market"),

    # ---- Market ----
    _DefaultItem("mkt-1", "market", "Submarket population + employment growth (BLS, ACS)",
                 "brian", 7, "market.population_growth"),
    _DefaultItem("mkt-2", "market", "Supply pipeline — new construction permits (BPS)",
                 "brian", 7, "market.new_supply_pipeline"),
    _DefaultItem("mkt-3", "market", "Rent comp pull (HelloData / Apartments.com / ALN)",
                 "brian", 7, "market.rent_growth_trend"),
    _DefaultItem("mkt-4", "market", "Recent sale comps (last 12 months)",
                 "brian", 7, "market.comparable_sales_volume"),
    _DefaultItem("mkt-5", "market", "Employer concentration check",
                 "brian", 14, "market.employer_concentration"),

    # ---- Tenant Concentration ----
    _DefaultItem("tc-1", "tenantConcentration", "Rent roll: top-5 tenant concentration",
                 "brian", 7, "tenantConcentration.single_tenant_revenue_share"),
    _DefaultItem("tc-2", "tenantConcentration", "Lease expiration cluster analysis",
                 "brian", 7, "tenantConcentration.lease_expiration_clustering"),
    _DefaultItem("tc-3", "tenantConcentration", "Section 8 / government concentration",
                 "brian", 7, "tenantConcentration.government_section_8_concentration"),
    _DefaultItem("tc-4", "tenantConcentration", "Average tenant tenure",
                 "brian", 7, "tenantConcentration.average_tenant_tenure"),
    _DefaultItem("tc-5", "tenantConcentration", "Income verification quality on rent roll",
                 "brian", 7, "tenantConcentration.income_verification_quality"),

    # ---- Physical Condition ----
    _DefaultItem("pc-1", "physicalCondition", "Order Property Condition Assessment (PCA)",
                 "vendor:engineer", 14, "physicalCondition.building_envelope"),
    _DefaultItem("pc-2", "physicalCondition", "Roof age + condition",
                 "vendor:engineer", 21, "physicalCondition.roof"),
    _DefaultItem("pc-3", "physicalCondition", "HVAC system age + remaining life",
                 "vendor:engineer", 21, "physicalCondition.hvac"),
    _DefaultItem("pc-4", "physicalCondition", "Plumbing type (galv / polybutylene = redflag)",
                 "vendor:engineer", 21, "physicalCondition.plumbing_type"),
    _DefaultItem("pc-5", "physicalCondition", "Electrical panel + service capacity",
                 "vendor:engineer", 21, "physicalCondition.electrical_capacity"),
    _DefaultItem("pc-6", "physicalCondition", "Foundation + structural review",
                 "vendor:engineer", 21, "physicalCondition.foundation"),
    _DefaultItem("pc-7", "physicalCondition", "ADA compliance audit",
                 "vendor:ada", 30, "physicalCondition.ada_compliance"),
    _DefaultItem("pc-8", "physicalCondition", "Parking lot condition",
                 "vendor:engineer", 21, "physicalCondition.parking_lot"),
    _DefaultItem("pc-9", "physicalCondition", "Unit interior sample inspection",
                 "peter", 14, "physicalCondition.building_envelope"),

    # ---- Regulatory (Multifamily) ----
    _DefaultItem("rm-1", "regulatoryMultifamily", "Rent control / stabilization research",
                 "brian", 7, "regulatoryMultifamily.rent_control"),
    _DefaultItem("rm-2", "regulatoryMultifamily", "Just-cause eviction requirements",
                 "brian", 7, "regulatoryMultifamily.just_cause_eviction"),
    _DefaultItem("rm-3", "regulatoryMultifamily", "Short-term rental restrictions",
                 "brian", 7, "regulatoryMultifamily.short_term_rental_restrictions"),
    _DefaultItem("rm-4", "regulatoryMultifamily", "Habitability / inspection regime",
                 "peter", 7, "regulatoryMultifamily.habitability_inspection_regime"),
]


# ---------------------------------------------------------------------------
# State dataclass + JSON marshalling
# ---------------------------------------------------------------------------

@dataclass
class DDItem:
    id: str
    category: str
    title: str
    owner: str
    due_date: str               # ISO YYYY-MM-DD
    status: Status
    notes: str = ""
    artifacts: list[dict] = field(default_factory=list)
    risk_score: float | None = None       # 0-100
    risk_factor: str = ""
    is_dealbreaker_hit: bool = False
    dealbreaker_type: str | None = None   # "hard" | "soft"
    soft_mitigation: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "category": self.category, "title": self.title,
            "owner": self.owner, "due_date": self.due_date, "status": self.status,
            "notes": self.notes, "artifacts": self.artifacts,
            "risk_score": self.risk_score, "risk_factor": self.risk_factor,
            "is_dealbreaker_hit": self.is_dealbreaker_hit,
            "dealbreaker_type": self.dealbreaker_type,
            "soft_mitigation": self.soft_mitigation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DDItem":
        return cls(
            id=d["id"], category=d["category"], title=d["title"],
            owner=d.get("owner", "brian"),
            due_date=d.get("due_date", ""),
            status=d.get("status", "pending"),
            notes=d.get("notes", ""),
            artifacts=d.get("artifacts") or [],
            risk_score=d.get("risk_score"),
            risk_factor=d.get("risk_factor", ""),
            is_dealbreaker_hit=d.get("is_dealbreaker_hit", False),
            dealbreaker_type=d.get("dealbreaker_type"),
            soft_mitigation=d.get("soft_mitigation", ""),
        )


@dataclass
class DDState:
    deal_id: str
    investment_strategy: InvestmentStrategy
    scoring_date: str
    last_updated: str
    items: list[DDItem]
    category_scores: dict[str, float | None]
    overall_risk_score: float | None
    overall_risk_level: RiskLevel
    dealbreakers: list[dict]
    soft_dealbreakers: list[dict]
    recommendation: Recommendation | None

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id,
            "investment_strategy": self.investment_strategy,
            "scoring_date": self.scoring_date,
            "last_updated": self.last_updated,
            "items": [i.to_dict() for i in self.items],
            "category_scores": self.category_scores,
            "overall_risk_score": self.overall_risk_score,
            "overall_risk_level": self.overall_risk_level,
            "dealbreakers": self.dealbreakers,
            "soft_dealbreakers": self.soft_dealbreakers,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DDState":
        return cls(
            deal_id=d.get("deal_id", ""),
            investment_strategy=d.get("investment_strategy", "value-add"),
            scoring_date=d.get("scoring_date", ""),
            last_updated=d.get("last_updated", ""),
            items=[DDItem.from_dict(i) for i in d.get("items") or []],
            category_scores=d.get("category_scores") or {c: None for c in CATEGORIES},
            overall_risk_score=d.get("overall_risk_score"),
            overall_risk_level=d.get("overall_risk_level", "UNSCORED"),
            dealbreakers=d.get("dealbreakers") or [],
            soft_dealbreakers=d.get("soft_dealbreakers") or [],
            recommendation=d.get("recommendation"),
        )


# ---------------------------------------------------------------------------
# Bootstrap + IO
# ---------------------------------------------------------------------------

def bootstrap_default_state(deal_id: str,
                             strategy: InvestmentStrategy = "value-add",
                             ) -> DDState:
    """Build a brand-new DDState seeded with the default checklist."""
    today = dt.date.today()
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    items: list[DDItem] = []
    for d in DEFAULT_CHECKLIST:
        items.append(DDItem(
            id=d.id, category=d.category, title=d.title,
            owner=d.default_owner,
            due_date=(today + dt.timedelta(days=d.default_due_offset_days)).isoformat(),
            status="pending",
            risk_factor=d.risk_factor,
        ))
    return DDState(
        deal_id=deal_id,
        investment_strategy=strategy,
        scoring_date=now_iso,
        last_updated=now_iso,
        items=items,
        category_scores={c: None for c in CATEGORIES},
        overall_risk_score=None,
        overall_risk_level="UNSCORED",
        dealbreakers=[],
        soft_dealbreakers=[],
        recommendation=None,
    )


def load_state(folder: Path) -> DDState:
    """Load dd.json from a property folder. Bootstraps if missing.

    Routes through `core.storage` so it works against local disk (Brian's
    desktop) and Microsoft Graph (Azure App Service) identically.
    """
    from core.storage import get_storage
    from data.property_io import _rel
    storage = get_storage()
    key = f"{_rel(folder)}/dd.json"
    if not storage.is_file(key):
        return bootstrap_default_state(deal_id=folder.name)
    try:
        data = json.loads(storage.read_text(key))
        return DDState.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError):
        return bootstrap_default_state(deal_id=folder.name)


def save_state(folder: Path, state: DDState) -> None:
    """Persist dd.json. Refreshes last_updated. Storage layer handles atomic
    write semantics (LocalDiskStorage uses tempfile+rename; GraphStorage
    uses Graph PUT, atomic by API contract)."""
    from core.storage import get_storage
    from data.property_io import _rel
    storage = get_storage()
    state.last_updated = dt.datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=str)
    storage.write_text(f"{_rel(folder)}/dd.json", payload)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def compute_category_score(items: list[DDItem], category: str) -> float | None:
    """Average of all scored items in this category. None if all unscored."""
    scored = [i.risk_score for i in items
              if i.category == category and i.risk_score is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def compute_overall_score(
    items: list[DDItem],
    strategy: InvestmentStrategy,
) -> tuple[float | None, dict[str, float | None]]:
    """Strategy-weighted average across categories.

    UNSCORED categories are excluded; their weight redistributes pro-rata
    across the scored categories (per risk-scoring.md edge case #3).
    """
    cat_scores: dict[str, float | None] = {
        c: compute_category_score(items, c) for c in CATEGORIES
    }
    weights = STRATEGY_WEIGHTS[strategy]
    scored_categories = [c for c, v in cat_scores.items() if v is not None]
    if not scored_categories:
        return None, cat_scores
    total_weight = sum(weights[c] for c in scored_categories)
    if total_weight <= 0:
        return None, cat_scores
    overall = sum(
        cat_scores[c] * weights[c] / total_weight  # type: ignore[operator]
        for c in scored_categories
    )
    return overall, cat_scores


def list_open_dealbreakers(items: list[DDItem]) -> tuple[list[dict], list[dict]]:
    """Return (hard, soft) lists of open dealbreakers — items flagged as
    dealbreaker_hit with status != complete/n-a."""
    hard: list[dict] = []
    soft: list[dict] = []
    for i in items:
        if not i.is_dealbreaker_hit:
            continue
        if i.status in ("complete", "n-a"):
            continue
        entry = {
            "item_id": i.id,
            "title": i.title,
            "category": i.category,
            "notes": i.notes,
            "mitigation": i.soft_mitigation,
        }
        if i.dealbreaker_type == "hard":
            hard.append(entry)
        else:
            soft.append(entry)
    return hard, soft


def derive_recommendation(
    overall: float | None,
    hard_db: list[dict],
    soft_db: list[dict],
    strategy: InvestmentStrategy,
    unscored_categories: int,
) -> Recommendation:
    """Apply the risk-scoring.md decision rules to derive a recommendation."""
    if hard_db:
        return "REJECT"
    if unscored_categories > 2:
        return "FURTHER_DILIGENCE"
    if overall is None:
        return "FURTHER_DILIGENCE"

    thresholds = STRATEGY_THRESHOLDS[strategy]
    if overall < thresholds["proceed"] and not soft_db:
        return "PROCEED"
    if overall < thresholds["with_mit"]:
        # Verify each soft dealbreaker has a mitigation note
        for sdb in soft_db:
            if not sdb.get("mitigation") or len(sdb["mitigation"]) < 40:
                return "FURTHER_DILIGENCE"
        return "PROCEED_WITH_MITIGATIONS"
    if overall < thresholds["with_cau"]:
        return "PROCEED_WITH_CAUTION"
    return "REJECT"


def recompute_aggregates(state: DDState) -> DDState:
    """Recompute category scores, overall score, dealbreakers, recommendation."""
    overall, cat_scores = compute_overall_score(state.items, state.investment_strategy)
    hard, soft = list_open_dealbreakers(state.items)
    unscored = sum(1 for v in cat_scores.values() if v is None)
    rec = derive_recommendation(overall, hard, soft, state.investment_strategy, unscored)

    state.category_scores = cat_scores
    state.overall_risk_score = overall
    state.overall_risk_level = risk_level_for_score(overall)
    state.dealbreakers = hard
    state.soft_dealbreakers = soft
    state.recommendation = rec
    return state


# ---------------------------------------------------------------------------
# IC-readiness gate (consumed by Exec Summary / Verdict)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ICReadiness:
    is_ready: bool
    completion_pct: float        # 0.0-1.0
    open_hard_dealbreakers: int
    open_soft_dealbreakers_no_mitigation: int
    blocking_reasons: list[str]


def ic_readiness(state: DDState, min_completion: float = 0.80) -> ICReadiness:
    """Check whether the DD state is sufficient for a GO verdict.

    Gate conditions (all must hold):
      1. ≥ ``min_completion`` of items in {"complete", "n-a"}
      2. Zero open hard dealbreakers
      3. Every open soft dealbreaker has a mitigation note (≥40 chars)
    """
    total = len(state.items)
    done = sum(1 for i in state.items if i.status in ("complete", "n-a"))
    pct = done / total if total else 0.0

    hard, soft = list_open_dealbreakers(state.items)
    soft_no_mit = sum(
        1 for s in soft if not s.get("mitigation") or len(s["mitigation"]) < 40
    )

    blocking: list[str] = []
    if pct < min_completion:
        blocking.append(
            f"DD completion {pct:.0%} < required {min_completion:.0%} "
            f"({done}/{total} items done)"
        )
    if hard:
        for h in hard:
            blocking.append(f"Hard dealbreaker open: {h['title']}")
    if soft_no_mit:
        blocking.append(
            f"{soft_no_mit} soft dealbreaker(s) without documented mitigation"
        )

    return ICReadiness(
        is_ready=not blocking,
        completion_pct=pct,
        open_hard_dealbreakers=len(hard),
        open_soft_dealbreakers_no_mitigation=soft_no_mit,
        blocking_reasons=blocking,
    )


# ---------------------------------------------------------------------------
# Stats helpers — used by the UI overview card
# ---------------------------------------------------------------------------

def category_stats(state: DDState, category: str) -> dict:
    """Counts per status for one category, plus risk band."""
    items = [i for i in state.items if i.category == category]
    by_status: dict[str, int] = {}
    for i in items:
        by_status[i.status] = by_status.get(i.status, 0) + 1
    return {
        "total": len(items),
        "by_status": by_status,
        "score": state.category_scores.get(category),
        "level": risk_level_for_score(state.category_scores.get(category)),
    }
