"""Module E - deterministic extraction QA (spec 6.3).

Every number a model pulls out of a T-12, rent roll or OM is checked against
arithmetic that must hold regardless of how the document was worded. Per spec
11 the core stays LLM-free: nothing in this module calls a model, so the
validators run identically whether AI extraction is on or off, and per spec
7.4 "no raw LLM output is ever persisted unchecked" - this is the gate.

Three kinds of check:

  * **Internal consistency** - line items must sum to the totals printed on the
    same statement (a mis-read line shows up as a broken sum).
  * **Cross-document ties** - the rent roll unit count must equal the OM unit
    count; rent-roll potential rent must tie to T-12 gross potential rent. A
    document read correctly in isolation can still be the *wrong document*.
  * **Sanity bands** - a $47 rent or a 340% cap rate is a decimal-point or
    units error, not a market.

Severity drives behavior, so it is deliberate:
  ``error``   - arithmetic that cannot legitimately break. Blocks auto-apply.
  ``warning`` - a real tie-out miss that a human should look at, but which has
                honest explanations (mid-month rent roll vs full-year T-12).
  ``info``    - context, never blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]

# Tolerances. Tight where arithmetic must close exactly, loose where two
# documents describe overlapping-but-different periods.
EXACT = 0.0
SUM_TOLERANCE = 0.02          # 2% - rounding and a "miscellaneous" line
CROSS_DOC_TOLERANCE = 0.05    # 5% - rent roll is a snapshot, T-12 is a year
LOW_CONFIDENCE = 0.70         # below this, a field is queued for human confirm

# Sanity bands: (minimum, maximum) for values that are unambiguous errors
# outside the range. Deliberately wide - this catches decimal slips, not
# aggressive underwriting.
SANITY_BANDS: dict[str, tuple[float, float]] = {
    "monthly_rent":  (200.0, 25_000.0),
    "unit_sqft":     (150.0, 6_000.0),
    "total_units":   (1.0, 5_000.0),
    "cap_rate":      (0.01, 0.25),
    "occupancy":     (0.0, 1.0),
    "price_per_unit": (5_000.0, 1_500_000.0),
}


@dataclass(frozen=True)
class QACheck:
    """One validation result."""
    id: str
    title: str
    passed: bool
    severity: Severity
    detail: str
    expected: float | None = None
    actual: float | None = None
    variance_pct: float | None = None


@dataclass(frozen=True)
class FieldFlag:
    """An extracted field the model wasn't sure about."""
    key: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class QAReport:
    checks: list[QACheck] = field(default_factory=list)
    low_confidence: list[FieldFlag] = field(default_factory=list)

    @property
    def failures(self) -> list[QACheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def errors(self) -> list[QACheck]:
        return [c for c in self.failures if c.severity == "error"]

    @property
    def warnings(self) -> list[QACheck]:
        return [c for c in self.failures if c.severity == "warning"]

    @property
    def blocking(self) -> bool:
        """True when the extraction must not be auto-applied without review.

        Either arithmetic that cannot legitimately break has broken, or a field
        came back below the confidence bar.
        """
        return bool(self.errors) or bool(self.low_confidence)

    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.passed)
        bits = [f"{passed}/{len(self.checks)} checks passed"]
        if self.errors:
            bits.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            bits.append(f"{len(self.warnings)} warning(s)")
        if self.low_confidence:
            bits.append(f"{len(self.low_confidence)} low-confidence field(s)")
        return "; ".join(bits)


# ---------------------------------------------------------------------------
# Value access - sources.json leaves are either a raw number or a provenance
# dict {"value": ..., "confidence": ...}. Both shapes must read the same.
# ---------------------------------------------------------------------------

def _num(value: Any) -> float | None:
    """Coerce a sources.json leaf to a float, or None when absent/unusable."""
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence(value: Any) -> float | None:
    if isinstance(value, dict):
        return _num(value.get("confidence"))
    return None


def _variance(expected: float, actual: float) -> float:
    """Relative difference, guarding a zero denominator."""
    if expected == 0:
        return 0.0 if actual == 0 else 1.0
    return abs(actual - expected) / abs(expected)


def _tie_check(
    check_id: str,
    title: str,
    expected: float | None,
    actual: float | None,
    tolerance: float,
    severity: Severity,
    units: str = "",
    actual_label: str = "extracted",
    expected_label: str = "stated on the document",
) -> QACheck | None:
    """Compare two figures that should agree. None when either side is absent -
    a missing figure is not a failed tie-out, it is simply not checkable.

    BOTH figures come from the uploaded document — one printed on it, one
    derived by summing what was extracted from it. The wording matters: an
    earlier "X vs Y expected" phrasing read as though the workbench held a
    prior expectation about the deal and was contradicting the owner's own
    statement, on a first-ever upload. It never does; these are always
    self-consistency checks.
    """
    if expected is None or actual is None:
        return None
    var = _variance(expected, actual)
    passed = var <= tolerance
    fmt = (lambda v: f"{v:,.0f}{units}") if abs(expected) >= 100 else (lambda v: f"{v:,.4g}{units}")
    detail = (
        f"{fmt(actual)} {actual_label} vs {fmt(expected)} {expected_label}"
        + (f" — {var:.1%} apart" if not passed else "")
    )
    return QACheck(check_id, title, passed, severity, detail,
                   expected=expected, actual=actual, variance_pct=var)


def _band_check(check_id: str, title: str, value: float | None,
                band: str, severity: Severity = "error") -> QACheck | None:
    if value is None:
        return None
    lo, hi = SANITY_BANDS[band]
    passed = lo <= value <= hi
    return QACheck(
        check_id, title, passed, severity,
        f"{value:,.4g} is outside the plausible range {lo:,.4g}-{hi:,.4g}"
        if not passed else f"{value:,.4g} within range",
        actual=value,
    )


# ---------------------------------------------------------------------------
# Per-document validators
# ---------------------------------------------------------------------------

_T12_REVENUE_LINES = ("grossPotentialRent", "vacancy", "concessions", "badDebt",
                      "otherIncome")
_T12_LOSS_LINES = ("vacancy", "concessions", "badDebt")


def validate_t12(sources: dict[str, Any]) -> list[QACheck]:
    """T-12 internal arithmetic: line items must tie to the printed totals."""
    checks: list[QACheck] = []
    rev = sources.get("t12_revenue") or {}
    exp = sources.get("t12_expenses") or {}
    fixed = sources.get("t12_fixedCharges") or {}

    total_revenue = _num(sources.get("totalRevenue"))
    total_opex = _num(sources.get("totalOpex"))
    noi = _num(sources.get("noi"))

    # Revenue: GPR + other income - losses. Losses are extracted as POSITIVE
    # numbers per the ingest prompt, so they subtract here.
    gpr = _num(rev.get("grossPotentialRent"))
    other = _num(rev.get("otherIncome"))
    losses = [_num(rev.get(k)) for k in _T12_LOSS_LINES]
    if gpr is not None and any(v is not None for v in losses):
        derived = gpr + (other or 0.0) - sum(v for v in losses if v is not None)
        checks.append(_tie_check(
            "T12-REV-SUM", "Revenue lines tie to Total Revenue",
            total_revenue, derived, SUM_TOLERANCE, "error",
            actual_label="from adding up the revenue lines",
            expected_label="on the statement's Total Revenue line"))

    # Operating expenses + fixed charges must tie to Total Opex.
    line_items = [_num(v) for v in list(exp.values()) + list(fixed.values())]
    present = [v for v in line_items if v is not None]
    if present and total_opex is not None:
        checks.append(_tie_check(
            "T12-OPEX-SUM", "Expense lines tie to Total Operating Expenses",
            total_opex, sum(present), SUM_TOLERANCE, "error",
            actual_label=f"from adding up the {len(present)} expense lines read",
            expected_label="on the statement's Total Operating Expenses line"))

    # NOI is definitional.
    if total_revenue is not None and total_opex is not None:
        checks.append(_tie_check(
            "T12-NOI", "Revenue - Expenses ties to NOI",
            total_revenue - total_opex, noi, SUM_TOLERANCE, "error",
            actual_label="on the statement's NOI line",
            expected_label="from Total Revenue minus Total Operating Expenses"))

    # A loss line larger than gross potential rent is a sign convention error.
    if gpr is not None:
        for key in _T12_LOSS_LINES:
            v = _num(rev.get(key))
            if v is not None and v > gpr:
                checks.append(QACheck(
                    f"T12-SIGN-{key.upper()}",
                    f"{key} exceeds gross potential rent",
                    False, "error",
                    f"{key} of {v:,.0f} exceeds GPR of {gpr:,.0f} - "
                    "likely a sign or column misread",
                    expected=gpr, actual=v))

    return [c for c in checks if c is not None]


def validate_rent_roll(sources: dict[str, Any]) -> list[QACheck]:
    """Rent-roll internal consistency: the unit list must match its summary."""
    checks: list[QACheck] = []
    rr = sources.get("rentRoll") or {}
    summary = rr.get("summary") or {}
    units = rr.get("units") or []

    stated_units = _num(summary.get("totalUnits")) or _num(sources.get("totalUnits"))
    if units and stated_units is not None:
        checks.append(_tie_check(
            "RR-UNIT-COUNT", "Unit rows match the stated unit count",
            stated_units, float(len(units)), EXACT, "error", units=" units"))

    occupied_stated = _num(summary.get("occupiedUnits"))
    if units:
        counted = float(sum(
            1 for u in units
            if str(u.get("status", "")).strip().lower() in ("occupied", "current", "o")))
        if occupied_stated is not None:
            checks.append(_tie_check(
                "RR-OCCUPIED-COUNT", "Occupied rows match stated occupied units",
                occupied_stated, counted, EXACT, "warning", units=" units"))

    occ_pct = _num(summary.get("occupancyPct"))
    if occ_pct is not None:
        checks.append(_band_check("RR-OCC-BAND", "Occupancy within 0-100%",
                                  occ_pct, "occupancy"))
        if occupied_stated is not None and stated_units:
            checks.append(_tie_check(
                "RR-OCC-PCT", "Occupancy % ties to the unit counts",
                occupied_stated / stated_units, occ_pct, SUM_TOLERANCE, "warning"))

    if stated_units is not None:
        checks.append(_band_check("RR-UNITS-BAND", "Unit count plausible",
                                  stated_units, "total_units"))

    # Per-unit sanity. Report once per band rather than once per unit so a
    # badly-parsed file produces a readable report instead of 200 rows.
    bad_rent = [u.get("unit") for u in units
                if (r := _num(u.get("actualRent"))) is not None and r > 0
                and not (SANITY_BANDS["monthly_rent"][0] <= r <= SANITY_BANDS["monthly_rent"][1])]
    if bad_rent:
        checks.append(QACheck(
            "RR-RENT-BAND", "Unit rents within a plausible range", False, "error",
            f"{len(bad_rent)} unit(s) outside "
            f"${SANITY_BANDS['monthly_rent'][0]:,.0f}-${SANITY_BANDS['monthly_rent'][1]:,.0f}/mo: "
            + ", ".join(str(u) for u in bad_rent[:5])
            + (" ..." if len(bad_rent) > 5 else ""),
            actual=float(len(bad_rent))))

    bad_sqft = [u.get("unit") for u in units
                if (s := _num(u.get("sqft"))) is not None and s > 0
                and not (SANITY_BANDS["unit_sqft"][0] <= s <= SANITY_BANDS["unit_sqft"][1])]
    if bad_sqft:
        checks.append(QACheck(
            "RR-SQFT-BAND", "Unit square footage plausible", False, "warning",
            f"{len(bad_sqft)} unit(s) outside "
            f"{SANITY_BANDS['unit_sqft'][0]:,.0f}-{SANITY_BANDS['unit_sqft'][1]:,.0f} sqft",
            actual=float(len(bad_sqft))))

    return [c for c in checks if c is not None]


def validate_om(sources: dict[str, Any]) -> list[QACheck]:
    """OM internal arithmetic: the broker's own headline numbers must agree."""
    checks: list[QACheck] = []
    price = _num(sources.get("askingPrice"))
    units = _num(sources.get("totalUnits"))
    ppu = _num(sources.get("pricePerUnit"))
    cap = _num(sources.get("askingCapRate"))
    in_place_noi = _num(sources.get("in_place_noi"))

    if price is not None and units:
        checks.append(_tie_check(
            "OM-PPU", "Price per unit ties to price / units",
            price / units, ppu, SUM_TOLERANCE, "warning"))
    if price and in_place_noi is not None:
        checks.append(_tie_check(
            "OM-CAP", "Asking cap rate ties to NOI / price",
            in_place_noi / price, cap, SUM_TOLERANCE, "warning"))
    checks.append(_band_check("OM-CAP-BAND", "Cap rate plausible", cap, "cap_rate"))
    checks.append(_band_check("OM-PPU-BAND", "Price per unit plausible", ppu,
                              "price_per_unit", severity="warning"))
    checks.append(_band_check("OM-UNITS-BAND", "Unit count plausible", units,
                              "total_units"))
    return [c for c in checks if c is not None]


def validate_cross_document(sources: dict[str, Any]) -> list[QACheck]:
    """Ties between documents - the checks the spec calls out by name.

    A document can be internally perfect and still be the wrong document for
    this deal. These are the checks that catch that.
    """
    checks: list[QACheck] = []
    rr = sources.get("rentRoll") or {}
    summary = rr.get("summary") or {}
    units = rr.get("units") or []

    # Rent-roll unit count ties to the OM.
    om_units = _num(sources.get("totalUnits"))
    rr_units = _num(summary.get("totalUnits")) or (float(len(units)) if units else None)
    checks.append(_tie_check(
        "XD-UNITS", "Rent-roll unit count ties to the OM",
        om_units, rr_units, EXACT, "error", units=" units"))

    # Rent-roll potential rent (annualized) ties to T-12 gross potential rent.
    rev = sources.get("t12_revenue") or {}
    t12_gpr = _num(rev.get("grossPotentialRent"))
    market_rents = [_num(u.get("marketRent")) for u in units]
    present = [v for v in market_rents if v is not None and v > 0]
    if present and t12_gpr is not None:
        checks.append(_tie_check(
            "XD-GPR", "Rent-roll potential rent ties to T-12 gross potential rent",
            t12_gpr, sum(present) * 12.0, CROSS_DOC_TOLERANCE, "warning"))

    # In-place NOI on the OM vs the T-12 the seller supplied. A broker
    # pro-forma above the actuals is normal and expected - flag, never block.
    om_noi = _num(sources.get("in_place_noi"))
    t12_noi = _num(sources.get("noi"))
    if om_noi is not None and t12_noi is not None:
        c = _tie_check("XD-NOI", "OM in-place NOI ties to the T-12",
                       t12_noi, om_noi, CROSS_DOC_TOLERANCE, "warning")
        if c is not None and not c.passed and om_noi > t12_noi:
            c = QACheck(
                c.id, c.title, False, "warning",
                c.detail + " - OM NOI is above the T-12 actuals; underwrite the T-12",
                c.expected, c.actual, c.variance_pct)
        checks.append(c)

    return [c for c in checks if c is not None]


# ---------------------------------------------------------------------------
# Per-field confidence
# ---------------------------------------------------------------------------

def collect_low_confidence(sources: dict[str, Any],
                           threshold: float = LOW_CONFIDENCE) -> list[FieldFlag]:
    """Walk the extraction and flag every field the model wasn't sure about.

    Recurses because sources.json nests (`t12_revenue.grossPotentialRent`).
    """
    flags: list[FieldFlag] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "value" in node:
                conf = _confidence(node)
                if conf is not None and conf < threshold:
                    flags.append(FieldFlag(
                        path, conf,
                        f"confidence {conf:.0%} below the {threshold:.0%} bar"))
                return
            for key, val in node.items():
                walk(val, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")

    walk(sources, "")
    return sorted(flags, key=lambda f: f.confidence)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_qa(sources: dict[str, Any],
           confidence_threshold: float = LOW_CONFIDENCE) -> QAReport:
    """Every validator that applies to what is present in `sources`.

    Missing documents are skipped, not failed: a deal with only a T-12 gets the
    T-12 checks and no cross-document ties.
    """
    if not sources:
        return QAReport()
    checks = (validate_t12(sources) + validate_rent_roll(sources)
              + validate_om(sources) + validate_cross_document(sources))
    return QAReport(checks=checks,
                    low_confidence=collect_low_confidence(sources, confidence_threshold))
