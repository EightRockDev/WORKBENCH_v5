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


# --- accuracy guards (owner: "not accurate", 2026-08-15) --------------------
# The loose key matched a 26-unit apartment on S. Cape Henry to a house on
# the N. side, and showed its $313,500 sale between two couples as the
# building's history. Wrong data on an underwriting screen is worse than none.

def test_opposite_directions_never_match():
    from core.sale_history import _dirs_compatible
    assert not _dirs_compatible("3000 S. Cape Henry", "3000 N Cape Henry Ave")
    assert not _dirs_compatible("100 East Main St", "100 W Main St")


def test_missing_direction_on_one_side_still_matches():
    """The assessor routinely omits it; that must stay a match."""
    from core.sale_history import _dirs_compatible
    assert _dirs_compatible("3000 S. Cape Henry", "3000 Cape Henry Ave")
    assert _dirs_compatible("3000 Cape Henry", "3000 S Cape Henry Ave")


def test_spelled_out_direction_agrees_with_its_abbreviation():
    from core.sale_history import _dirs_compatible
    assert _dirs_compatible("100 North Main St", "100 N Main St")


def test_unit_counts_veto_an_implausible_match():
    from core.sale_history import _units_compatible
    assert not _units_compatible(26, 1)      # apartment vs house
    assert not _units_compatible(200, 4)
    assert _units_compatible(26, 28)         # assessor/vendor disagree slightly
    assert _units_compatible(26, None)       # unknown never blocks
    assert _units_compatible(None, None)
