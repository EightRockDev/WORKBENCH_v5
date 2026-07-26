"""Module E - rent-roll anomaly detection (spec 6.3).

Four patterns the spec names, each deterministic (spec 11 - no model calls):

  * **Below-comp units** - occupied units paying far under the property's own
    market rent for the same floorplan. Loss-to-lease is upside, but a unit at
    a fraction of its neighbors' rent is usually an undisclosed arrangement
    (employee unit, side deal, stale row).
  * **Duplicate units** - the same unit number listed twice. Double-counted
    income inflates GPR and every metric derived from it.
  * **Expiration clusters** - too many leases expiring in the same month.
    Concentrated rollover is vacancy risk the sensitivity grid can't see if
    nobody surfaces it.
  * **RUBS-as-rent** - utility reimbursements folded into the rent column.
    RUBS income is real but it is not rent: it doesn't grow at rent growth and
    lenders underwrite it separately. Detected when a unit's total charges
    equal its actual rent while the property elsewhere reports utility income,
    or when rent is a suspiciously exact split above market.

Every finding carries the unit numbers involved so the analyst can open the
source document and check the exact rows.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

AnomalyKind = Literal["below_comp", "duplicate_unit", "expiration_cluster",
                      "rubs_as_rent"]
Severity = Literal["error", "warning", "info"]

# Bars, chosen so ordinary loss-to-lease does NOT fire.
BELOW_COMP_FRACTION = 0.75      # occupied & below 75% of floorplan market rent
BELOW_COMP_MIN_PEERS = 2        # need at least this many rented peers to judge
CLUSTER_SHARE = 0.25            # >=25% of expiring leases in one month
CLUSTER_MIN_UNITS = 5           # and at least this many leases in that month
RUBS_TOLERANCE = 0.01           # exact-to-the-dollar match band


@dataclass(frozen=True)
class Anomaly:
    kind: AnomalyKind
    severity: Severity
    title: str
    detail: str
    units: list[str] = field(default_factory=list)
    metric: float | None = None


def _num(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_occupied(unit: dict) -> bool:
    return str(unit.get("status", "")).strip().lower() in ("occupied", "current", "o")


def _floorplan(unit: dict) -> str:
    return str(unit.get("unitType") or unit.get("floorplan") or "?").strip().upper()


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def find_duplicate_units(units: list[dict]) -> list[Anomaly]:
    counts = Counter(str(u.get("unit", "")).strip() for u in units if u.get("unit"))
    dupes = sorted(label for label, n in counts.items() if n > 1)
    if not dupes:
        return []
    return [Anomaly(
        "duplicate_unit", "error",
        f"{len(dupes)} unit number(s) appear more than once",
        "Duplicate rows double-count income and inflate GPR, occupancy and "
        "every derived metric. Check the source rent roll for these rows: "
        + ", ".join(dupes[:10]) + (" ..." if len(dupes) > 10 else ""),
        units=dupes)]


def find_below_comp_units(units: list[dict]) -> list[Anomaly]:
    """Occupied units paying far below the same floorplan's market rent.

    The comp is the property's own market-rent column per floorplan (median of
    peers), so this needs no external data and works on any rent roll.
    """
    by_plan: dict[str, list[dict]] = defaultdict(list)
    for u in units:
        if _is_occupied(u):
            by_plan[_floorplan(u)].append(u)

    flagged: list[tuple[str, float]] = []
    for plan, members in by_plan.items():
        market = [m for u in members if (m := _num(u.get("marketRent"))) and m > 0]
        if len(market) < BELOW_COMP_MIN_PEERS + 1:
            continue
        benchmark = statistics.median(market)
        if benchmark <= 0:
            continue
        for u in members:
            actual = _num(u.get("actualRent"))
            if actual is not None and 0 < actual < benchmark * BELOW_COMP_FRACTION:
                flagged.append((str(u.get("unit", "?")), actual / benchmark))

    if not flagged:
        return []
    flagged.sort(key=lambda t: t[1])
    worst = flagged[0]
    return [Anomaly(
        "below_comp", "warning",
        f"{len(flagged)} occupied unit(s) rent far below their floorplan's market",
        f"Below {BELOW_COMP_FRACTION:.0%} of the floorplan median - often an "
        "employee unit, a side arrangement, or a stale row rather than "
        f"loss-to-lease. Worst: unit {worst[0]} at {worst[1]:.0%} of market.",
        units=[t[0] for t in flagged],
        metric=worst[1])]


def find_expiration_clusters(units: list[dict],
                             distribution: list[dict] | None = None) -> list[Anomaly]:
    """Months holding an outsized share of lease expirations.

    Prefers per-unit lease-exp dates; falls back to the extracted
    `leaseExpirationDistribution` when the unit table lacks dates.
    """
    by_month: Counter[str] = Counter()
    unit_by_month: dict[str, list[str]] = defaultdict(list)
    for u in units:
        exp = str(u.get("leaseExp") or "").strip()
        if len(exp) >= 7 and exp[:4].isdigit():
            month = exp[:7]
            by_month[month] += 1
            unit_by_month[month].append(str(u.get("unit", "?")))
    if not by_month and distribution:
        for row in distribution:
            month = str(row.get("month", "")).strip()
            n = _num(row.get("expiring_count"))
            if month and n:
                by_month[month] += int(n)

    total = sum(by_month.values())
    if total == 0:
        return []
    out: list[Anomaly] = []
    for month, n in sorted(by_month.items()):
        share = n / total
        if n >= CLUSTER_MIN_UNITS and share >= CLUSTER_SHARE:
            out.append(Anomaly(
                "expiration_cluster", "warning",
                f"{n} leases ({share:.0%} of expirations) roll in {month}",
                "Concentrated rollover is vacancy risk in a single month - "
                "stagger renewals or model that month's downside explicitly.",
                units=unit_by_month.get(month, []),
                metric=share))
    return out


def find_rubs_as_rent(units: list[dict], sources: dict | None = None) -> list[Anomaly]:
    """Utility reimbursements folded into the rent column.

    Two deterministic signals:
      * The property's T-12 reports utility/other income, yet unit totalCharges
        exactly equals actualRent across the roll - charges that should be
        itemized are inside the rent number.
      * Occupied units whose actual rent EXCEEDS their market rent by a small
        flat amount repeated across many units (a fixed RUBS charge, not a
        premium - premia are proportional, RUBS is flat).
    """
    if not units:
        return []
    sources = sources or {}
    rev = sources.get("t12_revenue") or {}
    other_income = _num(rev.get("otherIncome"))

    rows = [(str(u.get("unit", "?")), _num(u.get("actualRent")),
             _num(u.get("totalCharges")), _num(u.get("marketRent")))
            for u in units if _is_occupied(u)]

    out: list[Anomaly] = []

    # Signal 1: other income exists but charges == rent everywhere.
    with_charges = [(label, r, t) for label, r, t, _m in rows
                    if r and t and r > 0 and t > 0]
    if other_income and other_income > 0 and len(with_charges) >= 5:
        identical = [label for label, r, t in with_charges
                     if abs(t - r) <= max(1.0, r * RUBS_TOLERANCE)]
        if len(identical) == len(with_charges):
            out.append(Anomaly(
                "rubs_as_rent", "warning",
                "Total charges equal rent on every unit despite reported other income",
                f"The T-12 shows {other_income:,.0f} of other income, but no "
                "unit itemizes charges beyond rent - RUBS/utility income may be "
                "folded into the rent column. Underwrite rent and RUBS "
                "separately; RUBS does not grow at rent growth.",
                units=identical[:10]))

    # Signal 2: repeated flat premium of rent over market.
    premia = [(label, r - m) for label, r, _t, m in rows
              if r and m and r > m > 0]
    if len(premia) >= 5:
        rounded = Counter(round(p / 5.0) * 5 for _label, p in premia)
        flat, n = rounded.most_common(1)[0]
        if flat >= 20 and n >= max(5, int(0.5 * len(premia))):
            hit = [label for label, p in premia if round(p / 5.0) * 5 == flat]
            out.append(Anomaly(
                "rubs_as_rent", "info",
                f"{n} unit(s) carry the same flat ${flat:,.0f} premium over market",
                "A repeated flat premium usually means a fixed monthly charge "
                "(RUBS, pet rent, tech fee) inside the rent column rather than "
                "true market premium. Confirm what the charge is before "
                "underwriting it as rent.",
                units=hit[:10],
                metric=float(flat)))

    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def detect_anomalies(sources: dict[str, Any]) -> list[Anomaly]:
    """All rent-roll anomaly detectors against a property's sources.json."""
    rr = (sources or {}).get("rentRoll") or {}
    units = rr.get("units") or []
    if not units:
        return []
    distribution = (sources or {}).get("leaseExpirationDistribution")
    return (find_duplicate_units(units)
            + find_below_comp_units(units)
            + find_expiration_clusters(units, distribution)
            + find_rubs_as_rent(units, sources))
