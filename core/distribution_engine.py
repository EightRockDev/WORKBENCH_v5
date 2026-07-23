"""Run a quarterly distribution against the ledger.

Given a pot of cash available for distribution this period (e.g. quarterly
operating cash flow, or sale proceeds), apply Eight Rock's three-tier
waterfall:

  Tier 1 — Preferred Return to LPs (8% cumulative, non-compounded, on
           unreturned LP capital).
  Tier 2 — Return of LP Capital (proportional to unreturned balances).
  Tier 3 — Residual Split (70% LP / 30% GP, proportional within each side).

Outputs per-investor distribution amounts + a mechanism trace explaining
HOW the dollars flowed through each tier. The trace is what Brian shows
LPs to explain their check.

Critical convention (per ``feedback_underwriting_conventions.md``):
  - 8% pref is **cumulative non-compounded**: unpaid pref accrues but
    doesn't earn pref-on-pref. Once paid down to zero, the clock resets.
  - LPs fund 100% of equity; GPs have $0 commitment (no co-invest).
  - GPs receive the 30% promote in Tier 3 only.

This module CONSUMES the ledger but doesn't mutate it. Callers can preview
a distribution (returns a Plan) and then commit it (writes events to the
ledger via `apply_distribution`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import config

from . import lp_gp_ledger as ledger_mod
from .lp_gp_ledger import (
    DistributionTier,
    Investor,
    Ledger,
    compute_accrued_pref,
    record_distribution,
)


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class InvestorPayment:
    investor_id: str
    investor_name: str
    kind: str  # "LP" / "GP"
    pref_paid: float = 0.0
    roc_paid: float = 0.0
    residual_paid: float = 0.0
    promote_paid: float = 0.0

    @property
    def total(self) -> float:
        return self.pref_paid + self.roc_paid + self.residual_paid + self.promote_paid

    def to_dict(self) -> dict:
        return {
            "investor_id": self.investor_id,
            "investor_name": self.investor_name,
            "kind": self.kind,
            "pref_paid": self.pref_paid,
            "roc_paid": self.roc_paid,
            "residual_paid": self.residual_paid,
            "promote_paid": self.promote_paid,
            "total": self.total,
        }


@dataclass
class DistributionPlan:
    """A previewed (not-yet-committed) distribution plan."""
    available_cash: float
    as_of_date: str
    pref_rate: float = config.LP_PREF
    lp_residual_split: float = config.LP_RESIDUAL_SPLIT
    gp_residual_split: float = config.GP_RESIDUAL_SPLIT

    # Tier-level totals
    tier1_pref_total: float = 0.0
    tier2_roc_total: float = 0.0
    tier3_residual_lp_total: float = 0.0
    tier3_residual_gp_total: float = 0.0
    cash_remaining: float = 0.0   # leftover (shouldn't happen — diagnostic)

    # Per-investor breakdown
    payments: list[InvestorPayment] = field(default_factory=list)

    # Mechanism trace — human-readable per-tier explanation
    trace: list[str] = field(default_factory=list)

    @property
    def total_paid(self) -> float:
        return sum(p.total for p in self.payments)

    @property
    def lp_payments(self) -> list[InvestorPayment]:
        return [p for p in self.payments if p.kind == "LP"]

    @property
    def gp_payments(self) -> list[InvestorPayment]:
        return [p for p in self.payments if p.kind == "GP"]

    def to_dict(self) -> dict:
        return {
            "available_cash": self.available_cash,
            "as_of_date": self.as_of_date,
            "pref_rate": self.pref_rate,
            "lp_residual_split": self.lp_residual_split,
            "gp_residual_split": self.gp_residual_split,
            "tier1_pref_total": self.tier1_pref_total,
            "tier2_roc_total": self.tier2_roc_total,
            "tier3_residual_lp_total": self.tier3_residual_lp_total,
            "tier3_residual_gp_total": self.tier3_residual_gp_total,
            "cash_remaining": self.cash_remaining,
            "payments": [p.to_dict() for p in self.payments],
            "trace": self.trace,
        }


# ---------------------------------------------------------------------------
# Core algorithm — preview only, doesn't mutate ledger
# ---------------------------------------------------------------------------

def preview_distribution(
    ledger: Ledger,
    available_cash: float,
    as_of_date: dt.date | None = None,
    pref_rate: float | None = None,
    lp_residual_split: float | None = None,
    gp_residual_split: float | None = None,
) -> DistributionPlan:
    """Compute who gets paid what — without modifying the ledger.

    Brian uses this to preview a quarterly distribution. Once he's happy,
    call ``apply_distribution()`` to commit the result as ledger events.
    """
    as_of = as_of_date or dt.date.today()
    pr = pref_rate if pref_rate is not None else config.LP_PREF
    lp_split = lp_residual_split if lp_residual_split is not None else config.LP_RESIDUAL_SPLIT
    gp_split = gp_residual_split if gp_residual_split is not None else config.GP_RESIDUAL_SPLIT

    plan = DistributionPlan(
        available_cash=available_cash,
        as_of_date=as_of.isoformat(),
        pref_rate=pr,
        lp_residual_split=lp_split,
        gp_residual_split=gp_split,
    )

    if available_cash <= 0:
        plan.trace.append("Available cash <= 0; nothing to distribute.")
        plan.cash_remaining = available_cash
        return plan

    # Initialize payments for every investor (zero-filled)
    payments_by_id: dict[str, InvestorPayment] = {
        inv.investor_id: InvestorPayment(
            investor_id=inv.investor_id,
            investor_name=inv.name,
            kind=inv.kind,
        )
        for inv in ledger.investors
    }

    lps = ledger.lps()
    gps = ledger.gps()
    if not lps:
        plan.trace.append("No LPs in ledger; cannot distribute.")
        plan.cash_remaining = available_cash
        plan.payments = list(payments_by_id.values())
        return plan

    pot = available_cash

    # ---- Tier 1: Pref to LPs (proportional to accrued pref) ----
    accrued_by_lp: dict[str, float] = {
        lp.investor_id: compute_accrued_pref(ledger, lp.investor_id, as_of, pr)
        for lp in lps
    }
    total_pref_owed = sum(accrued_by_lp.values())

    if total_pref_owed > 0:
        pref_to_distribute = min(pot, total_pref_owed)
        for lp in lps:
            owed = accrued_by_lp[lp.investor_id]
            if owed > 0:
                share = pref_to_distribute * (owed / total_pref_owed)
                payments_by_id[lp.investor_id].pref_paid = share
        plan.tier1_pref_total = pref_to_distribute
        pot -= pref_to_distribute
        plan.trace.append(
            f"Tier 1 (Pref @ {pr*100:.1f}%): "
            f"${pref_to_distribute:,.0f} of ${total_pref_owed:,.0f} accrued pref paid"
            + (" (fully satisfied)" if pref_to_distribute >= total_pref_owed - 0.01
               else f" (${total_pref_owed - pref_to_distribute:,.0f} still owed)")
        )
    else:
        plan.trace.append("Tier 1: No accrued pref to pay.")

    if pot <= 0.01:
        plan.cash_remaining = pot
        plan.payments = list(payments_by_id.values())
        return plan

    # ---- Tier 2: Return of LP Capital (proportional to unreturned balance) ----
    unreturned_by_lp: dict[str, float] = {
        lp.investor_id: max(0.0, lp.unreturned_capital)
        for lp in lps
    }
    total_unreturned = sum(unreturned_by_lp.values())

    if total_unreturned > 0:
        roc_to_distribute = min(pot, total_unreturned)
        for lp in lps:
            ur = unreturned_by_lp[lp.investor_id]
            if ur > 0:
                share = roc_to_distribute * (ur / total_unreturned)
                payments_by_id[lp.investor_id].roc_paid = share
        plan.tier2_roc_total = roc_to_distribute
        pot -= roc_to_distribute
        plan.trace.append(
            f"Tier 2 (Return of Capital): "
            f"${roc_to_distribute:,.0f} of ${total_unreturned:,.0f} unreturned LP capital paid"
            + (" (fully returned)" if roc_to_distribute >= total_unreturned - 0.01
               else f" (${total_unreturned - roc_to_distribute:,.0f} still unreturned)")
        )
    else:
        plan.trace.append("Tier 2: LP capital fully returned already.")

    if pot <= 0.01:
        plan.cash_remaining = pot
        plan.payments = list(payments_by_id.values())
        return plan

    # ---- Tier 3: Residual split 70 LP / 30 GP ----
    lp_residual_pot = pot * lp_split
    gp_residual_pot = pot * gp_split

    # LP side split proportional to original commitment (not current unreturned)
    total_lp_commitment = sum(lp.commitment for lp in lps)
    if total_lp_commitment > 0:
        for lp in lps:
            if lp.commitment > 0:
                share = lp_residual_pot * (lp.commitment / total_lp_commitment)
                payments_by_id[lp.investor_id].residual_paid = share
    else:
        # No commitment basis — split evenly across LPs
        per_lp = lp_residual_pot / len(lps) if lps else 0
        for lp in lps:
            payments_by_id[lp.investor_id].residual_paid = per_lp

    # GP side — proportional to commitment (if any) or evenly. Eight Rock
    # convention: GPs typically have $0 commitment so this splits evenly
    # across however many GPs are in the ledger.
    if gps:
        total_gp_commitment = sum(g.commitment for g in gps)
        if total_gp_commitment > 0:
            for gp in gps:
                share = gp_residual_pot * (gp.commitment / total_gp_commitment)
                payments_by_id[gp.investor_id].promote_paid = share
        else:
            per_gp = gp_residual_pot / len(gps)
            for gp in gps:
                payments_by_id[gp.investor_id].promote_paid = per_gp

    plan.tier3_residual_lp_total = lp_residual_pot
    plan.tier3_residual_gp_total = gp_residual_pot
    pot = 0.0
    plan.trace.append(
        f"Tier 3 (Residual Split {int(lp_split*100)}/{int(gp_split*100)}): "
        f"${lp_residual_pot:,.0f} to LPs, ${gp_residual_pot:,.0f} to GPs"
    )

    plan.cash_remaining = pot
    plan.payments = list(payments_by_id.values())
    return plan


# ---------------------------------------------------------------------------
# Commit — apply the plan as ledger events
# ---------------------------------------------------------------------------

def apply_distribution(
    ledger: Ledger,
    plan: DistributionPlan,
    notes: str = "",
) -> int:
    """Commit a previewed plan to the ledger as distribution events.

    Returns the count of events added. Each per-investor non-zero amount
    becomes one event tagged with its tier (pref / roc / residual / promote).
    Caller should then ``ledger_mod.save(folder, ledger)`` to persist.
    """
    n = 0
    for p in plan.payments:
        for amount, tier in (
            (p.pref_paid, "pref"),
            (p.roc_paid, "roc"),
            (p.residual_paid, "residual"),
            (p.promote_paid, "promote"),
        ):
            if amount > 0.01:
                record_distribution(
                    ledger,
                    investor_id=p.investor_id,
                    amount=amount,
                    tier=tier,
                    date=plan.as_of_date,
                    notes=notes or f"Distribution {plan.as_of_date}",
                )
                n += 1
    return n
