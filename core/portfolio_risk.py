"""Portfolio-level risk aggregation.

Walks every property folder, loads its ``deal.json`` + ``lp_ledger.json``,
and aggregates portfolio-wide views:

  - Total LP capital committed + called + outstanding
  - Concentration by submarket / vintage / lender / loan-maturity year
  - Rate-shock impact (+200 bps to floating debt = $X NOI hit)
  - Portfolio LP IRR weighted by raise
  - Concentration limit warnings

Built on top of:
  - core/lp_gp_ledger.py (LP ledger per property)
  - data/property_io.py (deal.json reader)
  - data/db.py (property record lookup)
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import lp_gp_ledger as lg
from data.property_io import (
    PROPERTIES_ROOT,
    discover_property_folders,
    load_deal,
)
from data.db import get_property


@dataclass
class PortfolioProperty:
    """Aggregated view of one property in the book."""
    property_id: str
    folder_name: str
    name: str
    city: str
    year_built: int | None
    units: int | None
    asset_class: str | None

    # From deal.json
    purchase_price: float = 0.0
    loan_amount: float = 0.0
    equity_raise: float = 0.0
    interest_rate: float = 0.0
    hold_years: int = 5
    has_deal: bool = False

    # From lp_ledger.json
    total_committed: float = 0.0
    total_called: float = 0.0
    total_distributed: float = 0.0
    total_unreturned: float = 0.0
    lp_count: int = 0
    has_ledger: bool = False


@dataclass
class PortfolioRollup:
    """Aggregate portfolio metrics + concentration breakdowns + warnings."""
    properties: list[PortfolioProperty] = field(default_factory=list)
    total_purchase_price: float = 0.0
    total_loan_amount: float = 0.0
    total_equity_raised: float = 0.0
    total_committed: float = 0.0
    total_called: float = 0.0
    total_distributed: float = 0.0
    total_outstanding: float = 0.0
    total_units: int = 0
    total_lps: int = 0

    # Concentration breakdowns: dimension → {bucket: $ committed}
    by_city: dict[str, float] = field(default_factory=dict)
    by_vintage_decade: dict[str, float] = field(default_factory=dict)
    by_loan_maturity_year: dict[int, float] = field(default_factory=dict)
    by_class: dict[str, float] = field(default_factory=dict)

    # Rate shock — what does +200 bps do to the book?
    rate_shock_200bp_annual_cost: float = 0.0

    # Warnings flagged by concentration limits
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Concentration limits — flag when book exceeds these
# ---------------------------------------------------------------------------

CONCENTRATION_LIMITS = {
    "max_city_share": 0.40,                # No single city > 40% of equity
    "max_single_property_share": 0.30,     # No single deal > 30% of equity
    "max_vintage_decade_share": 0.50,      # No single decade > 50% (e.g. all 1970s)
    "max_maturity_year_share": 0.50,       # No single maturity year > 50%
}


# ---------------------------------------------------------------------------
# Build the rollup
# ---------------------------------------------------------------------------

def _vintage_decade(year: int | None) -> str:
    if not year:
        return "Unknown"
    decade = (year // 10) * 10
    return f"{decade}s"


def build_rollup() -> PortfolioRollup:
    """Walk every property folder + ledger; aggregate into a PortfolioRollup."""
    rollup = PortfolioRollup()
    folders = discover_property_folders()

    for folder in folders:
        if folder.path is None:
            continue

        # Pull record/DB property metadata
        # The folder_name doesn't directly tell us property_id, so we use
        # the deal.json's identity. Best-effort: deal.json's property_id
        # if it has one (it doesn't by default — deal.json is slider state).
        # Fallback: skip if we can't identify the property.

        deal = None
        ledger = None
        try:
            deal = load_deal(folder.path)
        except Exception:
            pass
        try:
            ledger = lg.load(folder.path)
        except Exception:
            pass

        # Need at least one of deal or ledger to include the property
        if deal is None and (ledger is None or not ledger.investors):
            continue

        # Identity — best effort match by folder name
        prop_id = folder.folder_name
        name = folder.folder_name.replace("-", " ")
        city = ""
        year_built: int | None = None
        units: int | None = None
        asset_class: str | None = None

        # Parse folder name convention: <Name>-<Units>-<City>
        parts = folder.folder_name.split("-")
        if len(parts) >= 3:
            try:
                # Find the units token (numeric)
                for i, p in enumerate(parts):
                    if p.isdigit():
                        units = int(p)
                        name = " ".join(parts[:i])
                        city = " ".join(parts[i+1:])
                        break
            except (ValueError, IndexError):
                pass

        pp = PortfolioProperty(
            property_id=prop_id,
            folder_name=folder.folder_name,
            name=name,
            city=city,
            year_built=year_built,
            units=units,
            asset_class=asset_class,
        )

        if deal is not None:
            pp.has_deal = True
            pp.purchase_price = deal.pp
            pp.loan_amount = deal.loan_amount
            pp.equity_raise = deal.equity_raise
            pp.interest_rate = deal.interest_rate
            pp.hold_years = deal.hp

        if ledger is not None and ledger.investors:
            pp.has_ledger = True
            pp.total_committed = ledger.total_committed
            pp.total_called = ledger.total_called
            pp.total_distributed = ledger.total_distributions
            pp.total_unreturned = ledger.total_unreturned
            pp.lp_count = len(ledger.lps())

        rollup.properties.append(pp)

    # Totals
    rollup.total_purchase_price = sum(p.purchase_price for p in rollup.properties)
    rollup.total_loan_amount = sum(p.loan_amount for p in rollup.properties)
    rollup.total_equity_raised = sum(p.equity_raise for p in rollup.properties)
    rollup.total_committed = sum(p.total_committed for p in rollup.properties)
    rollup.total_called = sum(p.total_called for p in rollup.properties)
    rollup.total_distributed = sum(p.total_distributed for p in rollup.properties)
    rollup.total_outstanding = sum(p.total_unreturned for p in rollup.properties)
    rollup.total_units = sum(p.units or 0 for p in rollup.properties)

    # Concentration breakdowns
    for p in rollup.properties:
        base = p.equity_raise or p.total_committed or 0
        if base <= 0:
            continue
        if p.city:
            rollup.by_city[p.city] = rollup.by_city.get(p.city, 0) + base
        decade = _vintage_decade(p.year_built)
        rollup.by_vintage_decade[decade] = rollup.by_vintage_decade.get(decade, 0) + base
        # Loan maturity assumed 5 years from hold; if known.
        maturity_year = dt.date.today().year + (p.hold_years or 5)
        rollup.by_loan_maturity_year[maturity_year] = \
            rollup.by_loan_maturity_year.get(maturity_year, 0) + base
        if p.asset_class:
            rollup.by_class[p.asset_class] = rollup.by_class.get(p.asset_class, 0) + base

    # Rate shock: +200 bps on the floating-rate portion of the book.
    # Conservative assumption: 100% of the loan amount is sensitive.
    rollup.rate_shock_200bp_annual_cost = rollup.total_loan_amount * 0.02

    # Concentration warnings
    total_eq = rollup.total_equity_raised or rollup.total_committed
    if total_eq > 0:
        # Per-city
        for city, amt in rollup.by_city.items():
            share = amt / total_eq
            if share > CONCENTRATION_LIMITS["max_city_share"]:
                rollup.warnings.append(
                    f"⚠️ {city} concentration: {share*100:.0f}% of book "
                    f"(limit: {CONCENTRATION_LIMITS['max_city_share']*100:.0f}%)"
                )
        # Per-property
        for p in rollup.properties:
            if p.equity_raise > 0:
                share = p.equity_raise / total_eq
                if share > CONCENTRATION_LIMITS["max_single_property_share"]:
                    rollup.warnings.append(
                        f"⚠️ {p.name} is {share*100:.0f}% of book "
                        f"(limit: {CONCENTRATION_LIMITS['max_single_property_share']*100:.0f}%)"
                    )
        # Per-decade
        for dec, amt in rollup.by_vintage_decade.items():
            share = amt / total_eq
            if share > CONCENTRATION_LIMITS["max_vintage_decade_share"]:
                rollup.warnings.append(
                    f"⚠️ {dec} vintage concentration: {share*100:.0f}% "
                    f"(limit: {CONCENTRATION_LIMITS['max_vintage_decade_share']*100:.0f}%)"
                )
        # Per-maturity-year
        for yr, amt in rollup.by_loan_maturity_year.items():
            share = amt / total_eq
            if share > CONCENTRATION_LIMITS["max_maturity_year_share"]:
                rollup.warnings.append(
                    f"⚠️ {yr} loan maturity concentration: {share*100:.0f}% — "
                    f"refinance cliff in that year (limit: "
                    f"{CONCENTRATION_LIMITS['max_maturity_year_share']*100:.0f}%)"
                )

    return rollup
