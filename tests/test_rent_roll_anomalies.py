"""Module E - rent-roll anomaly detection (spec 6.3)."""

from __future__ import annotations

from core import rent_roll_anomalies as rra


def _unit(n, plan="1BR", status="Occupied", market=1_000, actual=950,
          total=None, lease_exp=None):
    return {"unit": str(n), "unitType": plan, "status": status,
            "marketRent": market, "actualRent": actual,
            "totalCharges": total if total is not None else actual,
            "leaseExp": lease_exp}


def _sources(units, other_income=None):
    s = {"rentRoll": {"summary": {"totalUnits": len(units)}, "units": units}}
    if other_income is not None:
        s["t12_revenue"] = {"otherIncome": other_income}
    return s


# ------------------------------------------------------------- duplicates

def test_duplicate_unit_numbers_are_an_error():
    units = [_unit(101), _unit(102), _unit(101)]
    found = rra.find_duplicate_units(units)
    assert len(found) == 1
    a = found[0]
    assert a.kind == "duplicate_unit" and a.severity == "error"
    assert a.units == ["101"]


def test_no_duplicates_no_findings():
    assert rra.find_duplicate_units([_unit(101), _unit(102)]) == []


# ------------------------------------------------------------- below comp

def test_far_below_market_unit_is_flagged():
    units = [_unit(i, actual=1_000) for i in range(101, 110)]
    units.append(_unit(110, actual=400))          # 40% of the median market
    found = rra.find_below_comp_units(units)
    assert len(found) == 1
    assert found[0].units == ["110"]
    assert found[0].metric is not None and found[0].metric < 0.5


def test_ordinary_loss_to_lease_does_not_fire():
    """5-10% under market is normal - the detector must not cry wolf."""
    units = [_unit(i, actual=920) for i in range(101, 110)]
    assert rra.find_below_comp_units(units) == []


def test_vacant_units_are_not_below_comp():
    units = [_unit(i, actual=1_000) for i in range(101, 110)]
    units.append(_unit(110, status="Vacant", actual=0))
    assert rra.find_below_comp_units(units) == []


def test_comparison_is_within_floorplan_not_property_wide():
    """A 1BR at 1BR-market must not be flagged just because 3BRs rent higher."""
    units = ([_unit(i, plan="3BR", market=1_800, actual=1_750) for i in range(101, 108)]
             + [_unit(i, plan="1BR", market=950, actual=900) for i in range(201, 208)])
    assert rra.find_below_comp_units(units) == []


def test_too_few_peers_stays_quiet():
    """One unit of a floorplan has no benchmark - never judge it."""
    units = [_unit(101, plan="STU", actual=300, market=900),
             _unit(102, plan="1BR"), _unit(103, plan="1BR")]
    assert rra.find_below_comp_units(units) == []


# ------------------------------------------------------- expiration clusters

def test_cluster_month_is_flagged_with_share():
    units = ([_unit(i, lease_exp="2026-10-31") for i in range(101, 109)]     # 8 in Oct
             + [_unit(i, lease_exp=f"2027-0{1 + (i % 6)}-15") for i in range(201, 213)])
    found = rra.find_expiration_clusters(units)
    assert len(found) == 1
    a = found[0]
    assert a.kind == "expiration_cluster" and "2026-10" in a.title
    assert a.metric is not None and a.metric >= rra.CLUSTER_SHARE
    assert len(a.units) == 8


def test_even_spread_is_not_a_cluster():
    units = [_unit(100 + i, lease_exp=f"2026-{(i % 12) + 1:02d}-15")
             for i in range(48)]
    assert rra.find_expiration_clusters(units) == []


def test_falls_back_to_extracted_distribution():
    dist = [{"month": "2026-09", "expiring_count": 12},
            {"month": "2026-10", "expiring_count": 2},
            {"month": "2026-11", "expiring_count": 2}]
    found = rra.find_expiration_clusters([], distribution=dist)
    assert len(found) == 1 and "2026-09" in found[0].title


# ------------------------------------------------------------ RUBS-as-rent

def test_charges_equal_rent_with_other_income_warns():
    units = [_unit(i, actual=1_000, total=1_000) for i in range(101, 111)]
    found = rra.find_rubs_as_rent(units, _sources(units, other_income=60_000))
    assert any(a.kind == "rubs_as_rent" and a.severity == "warning" for a in found)


def test_itemized_charges_stay_quiet():
    units = [_unit(i, actual=1_000, total=1_065) for i in range(101, 111)]
    found = rra.find_rubs_as_rent(units, _sources(units, other_income=60_000))
    assert all(a.severity != "warning" for a in found)


def test_repeated_flat_premium_over_market_is_flagged():
    units = [_unit(i, market=1_000, actual=1_045, total=1_045)
             for i in range(101, 111)]
    found = rra.find_rubs_as_rent(units, _sources(units))
    flat = [a for a in found if a.metric == 45.0]
    assert flat and flat[0].severity == "info"


def test_proportional_premium_is_not_rubs():
    """True market premium varies with the unit - a spread of premia is fine."""
    units = [_unit(100 + i, market=1_000 + 30 * i, actual=1_030 + 33 * i,
                   total=1_030 + 33 * i) for i in range(10)]
    assert rra.find_rubs_as_rent(units, _sources(units)) == []


# ------------------------------------------------------------- entry point

def test_detect_anomalies_composes_all_detectors():
    units = ([_unit(i, actual=1_000) for i in range(101, 110)]
             + [_unit(110, actual=400), _unit(110, actual=400)])   # dup + below-comp
    found = rra.detect_anomalies(_sources(units))
    kinds = {a.kind for a in found}
    assert "duplicate_unit" in kinds and "below_comp" in kinds


def test_no_rent_roll_no_findings():
    assert rra.detect_anomalies({}) == []
    assert rra.detect_anomalies({"rentRoll": {"units": []}}) == []
