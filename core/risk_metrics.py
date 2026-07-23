"""Refi / Exit Stress Test (Beardsley's 4-scenario panel).

The single most-important risk metric for a value-add multifamily deal. At the
planned exit/refi point, simulate four scenarios:

  1. Base               — proforma assumptions
  2. Operational Shock  — 10% revenue drop (typically drives ~24% NOI drop)
  3. Capital Markets    — interest rate +25%, cap rate +15%
  4. Both               — realistic worst-case combining (2) and (3)

For each scenario, compute the maximum hypothetical refi loan that today's
NOI could support (LOWEST of: DSCR-implied, LTV-implied, debt-yield-implied),
and PASS/FAIL based on whether that loan ≥ the existing balance at exit.

A failure means you'd be a forced seller at the wrong time — exactly the trap
2020-22 vintage value-add operators hit in 2024-25 (Matrix Feb 2026 directly
flags this as the current opportunity).
"""

from __future__ import annotations

from dataclasses import dataclass

from .calc import amortizing_payment


# Lender stress-case minimums (industry standard for stabilized multifamily)
MIN_DSCR        = 1.25   # Stress DSCR (vs 1.20 underwriting min)
MAX_LTV         = 0.70   # 70% LTV stress (vs 75% nominal)
MIN_DEBT_YIELD  = 0.07   # 7% debt yield


@dataclass(frozen=True)
class RefiScenario:
    name: str
    description: str
    noi_at_exit: float
    cap_rate: float
    interest_rate: float
    amort_months: int

    @property
    def implied_value(self) -> float:
        """NOI / cap rate — what a buyer (or appraiser) would pay."""
        if self.cap_rate <= 0:
            return 0.0
        return self.noi_at_exit / self.cap_rate

    @property
    def max_loan_dscr(self) -> float:
        """Largest loan whose annual P&I keeps DSCR ≥ MIN_DSCR.
        Loan = NOI / (MIN_DSCR × debt constant)."""
        constant = amortizing_payment(1.0, self.interest_rate, self.amort_months)
        if constant <= 0:
            return 0.0
        return self.noi_at_exit / (MIN_DSCR * constant)

    @property
    def max_loan_ltv(self) -> float:
        """Largest loan whose LTV ≤ MAX_LTV against the implied value."""
        return self.implied_value * MAX_LTV

    @property
    def max_loan_debt_yield(self) -> float:
        """Largest loan whose Debt Yield ≥ MIN_DEBT_YIELD."""
        if MIN_DEBT_YIELD <= 0:
            return 0.0
        return self.noi_at_exit / MIN_DEBT_YIELD

    @property
    def max_loan(self) -> float:
        """The 3-test minimum — what a stress-tested lender would actually fund."""
        return min(self.max_loan_dscr, self.max_loan_ltv, self.max_loan_debt_yield)


@dataclass(frozen=True)
class RefiTestResult:
    scenario: RefiScenario
    existing_loan_balance: float
    binding_constraint: str       # "DSCR" / "LTV" / "Debt Yield"

    @property
    def passes(self) -> bool:
        return self.scenario.max_loan >= self.existing_loan_balance

    @property
    def cushion(self) -> float:
        """How much headroom (positive) or deficit (negative) vs existing balance."""
        return self.scenario.max_loan - self.existing_loan_balance

    @property
    def cushion_pct(self) -> float:
        """Cushion as % of existing balance."""
        if self.existing_loan_balance <= 0:
            return 0.0
        return self.cushion / self.existing_loan_balance


def _binding_constraint(scenario: RefiScenario) -> str:
    candidates = {
        "DSCR": scenario.max_loan_dscr,
        "LTV": scenario.max_loan_ltv,
        "Debt Yield": scenario.max_loan_debt_yield,
    }
    return min(candidates.items(), key=lambda kv: kv[1])[0]


def run_refi_exit_test(
    *,
    base_noi_at_exit: float,
    base_exit_cap: float,
    base_interest_rate: float,
    amort_months: int,
    existing_loan_balance: float,
    operational_shock_pct: float = 0.10,
    cap_rate_shock_pct: float = 0.15,
    interest_rate_shock_pct: float = 0.25,
) -> dict[str, RefiTestResult]:
    """Run all 4 refi/exit scenarios. Returns a dict keyed by scenario name.

    Args:
      base_noi_at_exit: forecasted NOI in the exit year (pre-shock)
      base_exit_cap: market cap rate at exit (decimal, e.g. 0.075)
      base_interest_rate: market refi rate at exit (decimal)
      amort_months: refi amortization (300 = 25 yr is Eight Rock standard)
      existing_loan_balance: principal owed at exit
      operational_shock_pct: revenue haircut for op-shock scenarios (default 10%)
      cap_rate_shock_pct: cap rate widening % for capital-markets scenarios
      interest_rate_shock_pct: interest rate widening % for capital-markets

    Operational shock: a 10% revenue drop typically drives ~24% NOI drop because
    expenses are mostly fixed. We model this directly (NOI × (1 − op_shock × 2.4))
    with the multiplier capped to be conservative.
    """
    op_noi_multiplier = max(0.0, 1.0 - operational_shock_pct * 2.4)

    scenarios = {
        "Base": RefiScenario(
            name="Base",
            description="Proforma assumptions at exit — no shocks.",
            noi_at_exit=base_noi_at_exit,
            cap_rate=base_exit_cap,
            interest_rate=base_interest_rate,
            amort_months=amort_months,
        ),
        "Op Shock": RefiScenario(
            name="Op Shock",
            description=(
                f"Operational shock: {operational_shock_pct*100:.0f}% revenue drop "
                f"→ ~{(1-op_noi_multiplier)*100:.0f}% NOI drop (fixed expenses)"
            ),
            noi_at_exit=base_noi_at_exit * op_noi_multiplier,
            cap_rate=base_exit_cap,
            interest_rate=base_interest_rate,
            amort_months=amort_months,
        ),
        "Capital Markets": RefiScenario(
            name="Capital Markets",
            description=(
                f"Capital markets shock: rates +{interest_rate_shock_pct*100:.0f}%, "
                f"cap rates +{cap_rate_shock_pct*100:.0f}%"
            ),
            noi_at_exit=base_noi_at_exit,
            cap_rate=base_exit_cap * (1.0 + cap_rate_shock_pct),
            interest_rate=base_interest_rate * (1.0 + interest_rate_shock_pct),
            amort_months=amort_months,
        ),
        "Both": RefiScenario(
            name="Both",
            description=(
                "Realistic worst-case: operational + capital-markets stress combined"
            ),
            noi_at_exit=base_noi_at_exit * op_noi_multiplier,
            cap_rate=base_exit_cap * (1.0 + cap_rate_shock_pct),
            interest_rate=base_interest_rate * (1.0 + interest_rate_shock_pct),
            amort_months=amort_months,
        ),
    }

    return {
        name: RefiTestResult(
            scenario=scen,
            existing_loan_balance=existing_loan_balance,
            binding_constraint=_binding_constraint(scen),
        )
        for name, scen in scenarios.items()
    }
