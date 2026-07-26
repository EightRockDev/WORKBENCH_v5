"""Module E - deterministic extraction QA (spec 6.3)."""

from __future__ import annotations

from core import extraction_qa as qa


def _t12(revenue=1_200_000, opex=540_000, noi=660_000, gpr=1_250_000,
         vacancy=87_500, other=37_500):
    return {
        "totalRevenue": revenue,
        "totalOpex": opex,
        "noi": noi,
        "t12_revenue": {"grossPotentialRent": gpr, "vacancy": vacancy,
                        "concessions": 0, "badDebt": 0, "otherIncome": other},
        "t12_expenses": {"payroll": 200_000, "marketing": 20_000,
                         "repairsMaintenance": 90_000, "utilities": 80_000,
                         "managementFee": 60_000, "contractServices": 0,
                         "administrative": 20_000, "other": 0},
        "t12_fixedCharges": {"realEstateTaxes": 50_000, "insurance": 20_000},
    }


def _rent_roll(n=10, market=1_000, actual=950, status="Occupied"):
    units = [{"unit": f"{i+101}", "unitType": "1BR", "status": status,
              "sqft": 700, "marketRent": market, "actualRent": actual}
             for i in range(n)]
    return {"rentRoll": {"summary": {"totalUnits": n, "occupiedUnits": n,
                                     "occupancyPct": 1.0},
                         "units": units}}


# ---------------------------------------------------------------- T-12

def test_clean_t12_passes():
    checks = qa.validate_t12(_t12())
    assert checks and all(c.passed for c in checks)


def test_broken_revenue_sum_is_an_error():
    bad = _t12(revenue=900_000)   # lines say 1.2M
    failures = [c for c in qa.validate_t12(bad) if not c.passed]
    assert any(c.id == "T12-REV-SUM" and c.severity == "error" for c in failures)


def test_broken_noi_definition_is_an_error():
    bad = _t12(noi=100_000)       # revenue - opex = 660k
    failures = [c for c in qa.validate_t12(bad) if not c.passed]
    assert any(c.id == "T12-NOI" for c in failures)


def test_vacancy_exceeding_gpr_flags_sign_error():
    bad = _t12()
    bad["t12_revenue"]["vacancy"] = 2_000_000
    failures = [c for c in qa.validate_t12(bad) if not c.passed]
    assert any(c.id == "T12-SIGN-VACANCY" for c in failures)


def test_rounding_within_tolerance_passes():
    ok = _t12(revenue=1_195_000)  # 0.4% off - rounding, not a misread
    assert all(c.passed for c in qa.validate_t12(ok) if c.id == "T12-REV-SUM")


def test_missing_document_produces_no_checks_not_failures():
    assert qa.validate_t12({}) == []


# ---------------------------------------------------------------- rent roll

def test_clean_rent_roll_passes():
    checks = qa.validate_rent_roll(_rent_roll())
    assert checks and all(c.passed for c in checks)


def test_unit_count_mismatch_is_an_error():
    s = _rent_roll(n=10)
    s["rentRoll"]["summary"]["totalUnits"] = 12
    failures = [c for c in qa.validate_rent_roll(s) if not c.passed]
    assert any(c.id == "RR-UNIT-COUNT" and c.severity == "error" for c in failures)


def test_implausible_rent_is_an_error():
    s = _rent_roll()
    s["rentRoll"]["units"][0]["actualRent"] = 95_000   # decimal slip
    failures = [c for c in qa.validate_rent_roll(s) if not c.passed]
    assert any(c.id == "RR-RENT-BAND" for c in failures)
    # and the offending unit is named
    hit = next(c for c in failures if c.id == "RR-RENT-BAND")
    assert "101" in hit.detail


def test_occupancy_over_100pct_is_an_error():
    s = _rent_roll()
    s["rentRoll"]["summary"]["occupancyPct"] = 1.15
    failures = [c for c in qa.validate_rent_roll(s) if not c.passed]
    assert any(c.id == "RR-OCC-BAND" for c in failures)


# ---------------------------------------------------------------- OM

def test_om_ppu_must_tie_to_price_over_units():
    s = {"askingPrice": 3_000_000, "totalUnits": 30, "pricePerUnit": 250_000}
    failures = [c for c in qa.validate_om(s) if not c.passed]
    assert any(c.id == "OM-PPU" for c in failures)


def test_om_cap_rate_band_catches_percent_vs_fraction():
    s = {"askingCapRate": 7.5}    # meant 0.075
    failures = [c for c in qa.validate_om(s) if not c.passed]
    assert any(c.id == "OM-CAP-BAND" and c.severity == "error" for c in failures)


# ------------------------------------------------------------ cross-document

def test_rent_roll_units_must_tie_to_om():
    s = {**_rent_roll(n=24), "totalUnits": 26}
    failures = [c for c in qa.validate_cross_document(s) if not c.passed]
    assert any(c.id == "XD-UNITS" and c.severity == "error" for c in failures)


def test_rent_roll_gpr_ties_to_t12():
    s = {**_t12(gpr=120_000 * 12), **_rent_roll(n=120, market=1_000)}
    s["totalRevenue"] = None  # keep t12 internal checks quiet
    checks = qa.validate_cross_document(s)
    gpr = next(c for c in checks if c.id == "XD-GPR")
    assert gpr.passed


def test_rent_roll_gpr_mismatch_warns():
    s = {**_t12(gpr=2_500_000), **_rent_roll(n=100, market=1_000)}  # RR says 1.2M
    checks = qa.validate_cross_document(s)
    gpr = next(c for c in checks if c.id == "XD-GPR")
    assert not gpr.passed and gpr.severity == "warning"


def test_om_noi_above_t12_says_underwrite_the_t12():
    s = {"in_place_noi": 800_000, "noi": 660_000}
    checks = qa.validate_cross_document(s)
    noi = next(c for c in checks if c.id == "XD-NOI")
    assert not noi.passed and "underwrite the T-12" in noi.detail


# ------------------------------------------------------- confidence flags

def test_low_confidence_fields_are_flagged_with_paths():
    s = {"totalRevenue": {"value": 1_200_000, "confidence": 0.55},
         "t12_revenue": {"grossPotentialRent": {"value": 1_250_000,
                                                "confidence": 0.92}}}
    flags = qa.collect_low_confidence(s)
    assert [f.key for f in flags] == ["totalRevenue"]
    assert flags[0].confidence == 0.55


def test_confident_and_raw_number_fields_are_not_flagged():
    s = {"totalRevenue": 1_200_000,
         "noi": {"value": 660_000, "confidence": 0.95}}
    assert qa.collect_low_confidence(s) == []


# ------------------------------------------------------------- full report

def test_report_blocks_on_errors_but_not_warnings():
    clean = qa.run_qa({**_t12(), **_rent_roll(n=10)})
    assert not clean.blocking

    broken = {**_t12(revenue=700_000), **_rent_roll(n=10)}
    report = qa.run_qa(broken)
    assert report.blocking and report.errors


def test_report_blocks_on_low_confidence_alone():
    s = _t12()
    s["totalRevenue"] = {"value": 1_200_000, "confidence": 0.4}
    report = qa.run_qa(s)
    assert report.blocking and report.low_confidence


def test_empty_sources_is_a_quiet_pass():
    report = qa.run_qa({})
    assert report.checks == [] and not report.blocking


def test_provenance_dict_values_read_like_raw_numbers():
    """sources.json leaves are raw numbers OR {"value": ...} dicts - both
    shapes must validate identically."""
    raw = _t12()
    wrapped = {
        "totalRevenue": {"value": raw["totalRevenue"], "confidence": 0.95},
        "totalOpex": {"value": raw["totalOpex"], "confidence": 0.95},
        "noi": {"value": raw["noi"], "confidence": 0.95},
        "t12_revenue": {k: {"value": v, "confidence": 0.9}
                        for k, v in raw["t12_revenue"].items()},
        "t12_expenses": {k: {"value": v, "confidence": 0.9}
                         for k, v in raw["t12_expenses"].items()},
        "t12_fixedCharges": {k: {"value": v, "confidence": 0.9}
                             for k, v in raw["t12_fixedCharges"].items()},
    }
    raw_ids = {(c.id, c.passed) for c in qa.validate_t12(raw)}
    wrapped_ids = {(c.id, c.passed) for c in qa.validate_t12(wrapped)}
    assert raw_ids == wrapped_ids
