"""Vendor addresses and assessor addresses disagree in predictable ways.

Owner screenshot 2026-08-15: the sale card read "No sale history available"
while the sales sat in the index. Exact normalized equality was the only
address fallback, and it fails on the two things the two sources always
disagree about - a leading direction and a trailing street type.
"""

from __future__ import annotations

from core.sale_history import _addr_core, _norm_addr

# (vendor style, assessor style) - all the SAME property.
REAL_MISMATCHES = [
    ("3000 S. Cape Henry", "3000 CAPE HENRY AVE"),
    ("8180 Shore Drive", "8180 N SHORE DR"),
    ("1200 W Marshall St", "1200 MARSHALL STREET"),
]


def test_core_key_matches_where_exact_equality_fails():
    for vendor, assessor in REAL_MISMATCHES:
        core_v, core_a = _addr_core(vendor), _addr_core(assessor)
        assert core_v and core_v == core_a, f"{vendor!r} vs {assessor!r}"


def test_at_least_one_case_exact_matching_genuinely_missed():
    """Guards against the looser key being pointless - if exact matching
    already caught everything, this whole path is dead weight."""
    missed = [(v, a) for v, a in REAL_MISMATCHES
              if _norm_addr(v) != _norm_addr(a)]
    assert missed, "exact matching caught them all; the core key adds nothing"


def test_different_properties_do_not_collide():
    """The key must stay tight enough to be safe: a different house number
    or a different street is a different property."""
    assert _addr_core("3000 Cape Henry Ave") != _addr_core("3001 Cape Henry Ave")
    assert _addr_core("3000 Cape Henry Ave") != _addr_core("3000 Granby St")


def test_no_house_number_yields_no_key():
    """Without a number this would match half a city - refuse to produce one."""
    assert _addr_core("Cape Henry Avenue") == ""
    assert _addr_core("") == ""
    assert _addr_core(None) == ""
