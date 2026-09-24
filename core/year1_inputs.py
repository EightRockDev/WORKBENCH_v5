"""Year-1 GPR + expenses for the cash-flow projection - ONE derivation.

Every surface that runs `core.calc.build_cashflow` (Underwriting header,
Returns tab, V2 stat bar, exec summary, waterfall, artifact engine) must get
its Year-1 inputs from `derive_year1_inputs()` here. Until V5.67.1.0.0 four
hand-copied versions of this logic lived in four files and two of them had
drifted; the copies are gone and this module is the only place the rule is
written down.

The rule (owner report 2026-09-24, Hampton Community Townhomes):

  * The NOI dial is the anchor. At the dialed vacancy, with the reposition
    spike at zero and the post-sale adjustments off, Year-1 NOI in the
    projection equals `deal.noi` exactly. The going-in cap, DSCR, cash-on-cash
    and debt yield already ran off the dial; the IRR, equity multiple and
    stabilized DSCR now run off the same number instead of a silently
    different one rebuilt from the T-12 file.

  * A T-12 `totalRevenue` is COLLECTED income (EGI) - vacancy, concessions,
    bad debt and abatements are already out of it. It is grossed up to a
    potential-rent figure (`egi / (1 - vacancy)`) before `build_cashflow`
    takes vacancy back out, so vacancy is charged exactly once and Year-1
    EGI reproduces the T-12 at the dialed vacancy. Handing collected income
    to the model as if it were GPR took ~7% off twice (13.7% with the
    default spike) and drove a 6.7%-cap deal to a -22% IRR.

  * Expenses are whatever reconciles the T-12 revenue to the dial:
    `egi - deal.noi`. When the dial sits on the T-12 NOI that is the T-12
    opex line; when the analyst dials NOI up or down, the change flows through
    every projection instead of only the cap-rate card.

  * The reposition spike (`vac_spike_pp` / `stabilization_months`) and the
    post-sale tax reassessment / insurance escalator are OVERLAYS applied on
    top of the anchored Year-1, exactly as before.

Without a T-12 the legacy derivation stands: solve GPR from the dial NOI and
the Class C expense ratio (`NOI = GPR x (1 - vac - er)`), which was already
anchored to the dial.

Deterministic and LLM-free (spec Section 11). No Streamlit imports here.
"""

from __future__ import annotations

from typing import Any

import config
from core.mill_rates import DEFAULT_REASSESSMENT_RATIO, estimated_post_sale_tax

__all__ = ["apply_expense_adjustments", "derive_year1_inputs", "scalar"]


def scalar(v: Any) -> float | None:
    """Unwrap a sources.json value to a float, or None if there isn't one.

    Entries are stored either bare (`1234`) or provenance-wrapped
    (`{"value": 1234, "source": "T12"}`), and nested groups like
    `t12_fixedCharges.realEstateTaxes` use the wrapped form too. Reading one
    without unwrapping put a dict into an arithmetic comparison and crashed
    the whole Underwriting tab, so every read goes through here.
    """
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def derive_year1_inputs(
    deal: Any,
    sources: dict[str, Any] | None,
    units: int | None,
    *,
    city: str | None = None,
    pre_sale_tax: float | dict | None = None,
) -> tuple[float, float]:
    """Return (year1_gpr, year1_expenses) for `core.calc.build_cashflow`.

    `deal` is a `data.property_io.DealState` (typed loosely so this module
    stays importable from anywhere in `core/`). Only `noi`, `pp`,
    `vacancy_frac`, `tax_reassessment_on` and `insurance_escalator_on` are read.

    Invariant (pinned by tests/test_year1_inputs.py): with the returned pair,
    `build_cashflow(year1_vacancy_pct=deal.vacancy_frac, ...)` produces
    `rows[0].noi == deal.noi` whenever both post-sale adjustments are off.
    """
    vac = float(deal.vacancy_frac)

    if sources:
        egi = scalar(sources.get("totalRevenue"))
        # Pull pre-sale tax from sources.json if available
        if pre_sale_tax is None:
            t12 = sources.get("t12_fixedCharges")
            if isinstance(t12, dict):
                pre_sale_tax = scalar(t12.get("realEstateTaxes"))
        if egi and egi > 0 and vac < 1.0:
            # Collected income -> potential rent, so the model's single
            # vacancy deduction lands back on the T-12 figure.
            gpr = egi / (1.0 - vac)
            # Whatever reconciles collected income to the dialed NOI. A dial
            # above collected income is a data-entry error, not a model
            # input; expenses floor at zero rather than going negative.
            expenses = max(egi - float(deal.noi), 0.0)
            return gpr, apply_expense_adjustments(
                expenses, units, deal,
                city=city, purchase_price=deal.pp,
                pre_sale_tax=pre_sale_tax,
            )

    # No T-12: derive from NOI + expense ratio. NOI = (1 - vac) * GPR - expenses;
    # expenses = expense_ratio * GPR. Solve for GPR and expenses using the
    # Class C default 45% expense ratio (per config).
    er = config.EXPENSE_RATIOS.get("C", 0.45)
    # NOI = GPR * (1 - vac) - GPR * er = GPR * (1 - vac - er)
    denom = (1.0 - vac - er)
    if denom <= 0:
        # Pathological inputs; fall back to NOI / 0.5 as gpr
        gpr = float(deal.noi) / 0.5
    else:
        gpr = float(deal.noi) / denom
    expenses = gpr * er
    return gpr, apply_expense_adjustments(
        expenses, units, deal,
        city=city, purchase_price=deal.pp,
        pre_sale_tax=pre_sale_tax,
    )


def apply_expense_adjustments(
    base_expenses: float,
    units: int | None,
    deal: Any,
    *,
    city: str | None = None,
    purchase_price: float | None = None,
    pre_sale_tax: float | dict | None = None,
) -> float:
    """Apply post-sale tax reassessment + agency-debt insurance premium.

    Reassessment uses the FULL Beardsley formula when city + purchase price
    are known: new_tax = (purchase x 85%) x (mill_rate / 100). The DELTA
    over the seller's pre-sale tax is added to base expenses. Falls back
    to the conservative +6% opex proxy when inputs are missing.
    """
    adjusted = base_expenses
    # Callers should pass a number, but this runs against user-edited JSON -
    # normalize rather than trust, so a bad file degrades to the fallback
    # estimate instead of taking down the tab.
    pre_sale_tax = scalar(pre_sale_tax)
    if deal.tax_reassessment_on:
        if city and purchase_price and purchase_price > 0:
            new_tax = estimated_post_sale_tax(purchase_price, city, DEFAULT_REASSESSMENT_RATIO)
            # If we know the seller's old tax line, add the DELTA. Otherwise
            # add the full new tax assuming the seller's tax was already
            # baked into base_expenses at a roughly 30%-of-opex share that
            # we can't extract - so fall back to delta-vs-implied-old-tax.
            if pre_sale_tax and pre_sale_tax > 0:
                delta = new_tax - pre_sale_tax
                adjusted += max(delta, 0.0)
            else:
                # Estimate seller's old tax as ~30% of base opex, add only the
                # difference. This is more accurate than the flat +6% proxy.
                implied_old_tax = base_expenses * 0.30
                delta = new_tax - implied_old_tax
                adjusted += max(delta, 0.0)
        else:
            # No city / price known - fall back to flat +6% proxy
            adjusted += base_expenses * 0.06
    if deal.insurance_escalator_on and units:
        adjusted += 50.0 * float(units)
    return adjusted
