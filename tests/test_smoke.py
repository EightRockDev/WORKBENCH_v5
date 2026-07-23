"""Smoke test: verifies the package imports and `config.py` constants are sane.

Real test coverage for calc / waterfall / IRR / comps / sensitivity / verdict
will be added when those modules get implementations.
"""

from __future__ import annotations

import config


def test_config_imports():
    # Sanity: locked GO bars per Brian's 2026-05-06 ratification.
    assert config.GO_CAP == 0.075
    assert config.GO_DSCR == 1.30
    assert config.GO_COC == 0.06


def test_waterfall_constants():
    assert config.LP_PREF == 0.08
    assert config.LP_RESIDUAL_SPLIT == 0.70
    assert config.GP_RESIDUAL_SPLIT == 0.30
    assert config.GP_COINVEST == 0.0


def test_am_fee_defaults():
    assert config.AM_FEE_PCT == 0.04
    assert config.AM_FEE_EXIT_YEAR == 0.0


def test_amortization_locked_at_25_years():
    assert config.AMORT_MONTHS == 300
    assert config.AMORT_YEARS == 25


def test_lp_irr_target():
    assert config.LP_IRR_TARGET == 0.15
    assert config.LP_EQUITY_MULTIPLE_TARGET == 1.8


def test_comps_buckets():
    assert config.COMPS_BUCKET1_MAX == 8
    assert config.COMPS_BUCKET2_MAX == 4
    assert config.COMPS_TOTAL_MAX == 12
