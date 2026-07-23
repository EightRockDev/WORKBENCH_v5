"""IRR + equity multiple helpers.

`numpy_financial.irr` is the primary solver — fast, well-tested, used by Excel.
Bisection is the fallback for ill-conditioned cash flow vectors (e.g. flows
that don't change sign, where IRR is undefined). Returns `None` rather than
NaN so callers can guard cleanly.

Two IRRs are exposed for every deal per `SUMMARY-FORMAT.md`:
  - **Project IRR** — gross deal return, indifferent to GP promote.
                      Cash flow vector: [-equity, op_yr1, ..., op_yrN + exit_net]
  - **LP IRR**      — net to investor after the waterfall. Computed on the
                      LP cashflow vector returned by `core.waterfall.run_waterfall`.

Brian's GO targets (from config): LP IRR ≥ 15%, Project IRR ≥ 18%, EM ≥ 1.8x.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy_financial as npf


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def npv(rate: float, cashflows: Sequence[float]) -> float:
    """Net present value at `rate` for a sequence of period-spaced cashflows.

    `cashflows[0]` is treated as period 0 (no discount), `cashflows[1]`
    discounted by (1+rate), etc.
    """
    if rate <= -1.0:
        raise ValueError(f"rate must be > -1, got {rate}")
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cashflows))


def irr(cashflows: Sequence[float], guess: float = 0.10) -> float | None:
    """Annualized IRR. Returns None if no real root in [-99%, +1000%].

    Tries `numpy_financial.irr` first; falls back to bisection if NumPy returns
    NaN or raises (which can happen for cash flows with no sign change or
    pathological multi-root cases).

    Args:
        cashflows: period-spaced flows, conventionally `[-equity, cf1, ..., cfN]`.
        guess: hint for the root finder (used by NumPy; ignored by bisection).
    """
    if not cashflows or len(cashflows) < 2:
        return None
    # IRR requires at least one sign change in the cash flow vector
    has_pos = any(cf > 0 for cf in cashflows)
    has_neg = any(cf < 0 for cf in cashflows)
    if not (has_pos and has_neg):
        return None

    cf_list = list(cashflows)

    # Primary: numpy_financial.irr
    try:
        result = npf.irr(cf_list)
        if result is not None and not math.isnan(float(result)):
            return float(result)
    except (ValueError, ZeroDivisionError, FloatingPointError):
        pass

    # Fallback: bisection on rate
    return _bisect_irr(cf_list)


def _bisect_irr(
    cashflows: list[float],
    lo: float = -0.999,
    hi: float = 10.0,
    tol: float = 1e-7,
    max_iter: int = 200,
) -> float | None:
    """Bisection on rate to find NPV=0. Returns None if NPV doesn't bracket zero."""
    f_lo = npv(lo, cashflows)
    f_hi = npv(hi, cashflows)
    if f_lo * f_hi > 0:
        return None  # no sign change → no root in this range
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, cashflows)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Project / LP IRR helpers
# ---------------------------------------------------------------------------

def project_irr(
    *,
    equity_raise: float,
    annual_cashflows: Sequence[float],
    exit_proceeds_net: float,
) -> float | None:
    """Project-level IRR — gross deal return, indifferent to GP promote.

    Builds the cash flow vector:
        [-equity_raise, cf_yr1, cf_yr2, ..., cf_yr(N-1), cf_yrN + exit_proceeds_net]

    `annual_cashflows` is the per-year operating CF for years 1..N
    (length N). `exit_proceeds_net` is added to the final year only.
    """
    if not annual_cashflows:
        return None
    flows: list[float] = [-equity_raise]
    flows.extend(list(annual_cashflows[:-1]))
    flows.append(annual_cashflows[-1] + exit_proceeds_net)
    return irr(flows)


def lp_irr(lp_cashflows: Sequence[float]) -> float | None:
    """LP IRR — net to investor after the waterfall runs.

    Pass the `lp_cashflows` array from `core.waterfall.run_waterfall` directly:
    `[-equity_raise, lp_yr1, lp_yr2, ..., lp_yrN]`.
    """
    return irr(lp_cashflows)


def equity_multiple(equity_raise: float, total_distributions: float) -> float:
    """Total cash returned / cash invested. >= 1.0 means break-even or better.

    Returns 0.0 if `equity_raise` is non-positive (callers should guard against
    invalid inputs upstream rather than relying on this default).
    """
    if equity_raise <= 0:
        return 0.0
    return total_distributions / equity_raise
