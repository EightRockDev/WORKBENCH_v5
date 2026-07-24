"""Phase 0 spine tests — Eight Rock native identity & taxonomy (spec §7.2/§7.4)."""

from __future__ import annotations

import pytest

from core import spine

NORFOLK_FIPS = "51710"


# ---------------------------------------------------------------------------
# §7.2 identity
# ---------------------------------------------------------------------------

def test_property_id_is_deterministic_and_native():
    a = spine.property_id(NORFOLK_FIPS, "1234-5678-90")
    b = spine.property_id(NORFOLK_FIPS, "1234567890")      # punctuation stripped
    c = spine.property_id(NORFOLK_FIPS, "1234-5678-90 ")   # whitespace/case
    assert a == b == c
    assert a.startswith("8R-51710-") and len(a.split("-")[2]) == 12
    assert "aln" not in a.lower()


def test_different_apn_or_county_gives_different_id():
    a = spine.property_id(NORFOLK_FIPS, "111")
    assert a != spine.property_id(NORFOLK_FIPS, "112")
    assert a != spine.property_id("51550", "111")


def test_fips_is_zero_padded_and_validated():
    assert spine.property_id(1710, "111").startswith("8R-01710-")
    with pytest.raises(ValueError):
        spine.property_id("517100", "111")
    with pytest.raises(ValueError):
        spine.property_id(NORFOLK_FIPS, "  ")


def test_provisional_id_for_stock_without_apn():
    pid = spine.provisional_property_id(NORFOLK_FIPS, 36.8620, -76.2650)
    assert pid.startswith("8R-51710-X") and spine.is_provisional(pid)
    fips, suffix, prov = spine.parse_property_id(pid)
    assert fips == NORFOLK_FIPS and prov and len(suffix) == 10   # 'X' + geohash9


def test_geohash_is_stable_and_locates_norfolk():
    g = spine.geohash(36.8620, -76.2650, 9)
    assert g == spine.geohash(36.8620, -76.2650, 9) and len(g) == 9
    # Nearby points share a prefix; distant ones do not.
    assert g[:5] == spine.geohash(36.8625, -76.2655, 9)[:5]
    assert g[:3] != spine.geohash(47.6, -122.3, 9)[:3]


def test_parse_rejects_foreign_ids():
    for bad in ("ALN-123", "8R-5171-abc", "", "12345"):
        with pytest.raises(ValueError):
            spine.parse_property_id(bad)


# ---------------------------------------------------------------------------
# §7.2 taxonomy — 8r_class / 8r_form
# ---------------------------------------------------------------------------

def test_8r_class_uses_eight_rock_criteria_not_aln():
    a, why_a = spine.classify_8r_class(year_built=2018, rent_percentile=0.9,
                                       permits_last_5y=4)
    c, why_c = spine.classify_8r_class(year_built=1968, rent_percentile=0.25,
                                       permits_last_5y=0, condition_flags=2)
    assert a == "A" and c in ("C", "D")
    assert any("vintage band" in w for w in why_a)
    assert any("percentile" in w for w in why_c)
    assert all("aln" not in w.lower() for w in why_a + why_c)


def test_8r_class_orders_monotonically_with_quality():
    order = {"D": 0, "C": 1, "B": 2, "A": 3}
    got = [spine.classify_8r_class(year_built=y, rent_percentile=p)[0]
           for y, p in [(1962, 0.1), (1980, 0.4), (1998, 0.65), (2020, 0.95)]]
    assert [order[g] for g in got] == sorted(order[g] for g in got)


def test_8r_class_handles_unknown_inputs():
    cls, why = spine.classify_8r_class(year_built=None, rent_percentile=None)
    assert cls in ("A", "B", "C", "D") and any("unknown" in w for w in why)


def test_vintage_bands():
    assert spine.vintage_band(2015) == "modern"
    assert spine.vintage_band(1995) == "recent"
    assert spine.vintage_band(1980) == "mature"
    assert spine.vintage_band(1960) == "vintage"
    assert spine.vintage_band(None) == "unknown"


def test_8r_form_from_assessor_use_code():
    assert spine.derive_8r_form("Garden Apartments", 26) == "garden"
    assert spine.derive_8r_form("TOWNHOUSE", 26) == "townhome"
    assert spine.derive_8r_form("Mid-Rise Elevator", 80) == "mid-rise"
    assert spine.derive_8r_form("Duplex", 2) == "small-plex"
    assert spine.derive_8r_form(None, 3) == "small-plex"
    assert spine.derive_8r_form(None, 40, stories=6) == "mid-rise"
    assert spine.derive_8r_form(None, 40, stories=12) == "high-rise"
    assert spine.derive_8r_form(None, 40) == "garden"


# ---------------------------------------------------------------------------
# §7.4 AC-P0-1 / AC-P0-2 — "not discernible" verification
# ---------------------------------------------------------------------------

def test_scan_flags_aln_references():
    assert spine.scan_text_for_aln("source_file = ALN Virginia Export.xlsx")
    assert spine.scan_text_for_aln("row['aln_id'] = 12345")
    assert spine.scan_text_for_aln("Data source: aln")


def test_scan_ignores_words_that_merely_contain_the_letters():
    assert spine.scan_text_for_aln("The walnut tree on Alnwick Road") == []
    assert spine.scan_text_for_aln("balance salant") == []


def test_record_is_clean_detects_contamination():
    dirty_key = {"property_id": "8R-51710-abc123def456", "aln_id": "999"}
    ok, problems = spine.record_is_clean(dirty_key)
    assert not ok and any("field name" in p for p in problems)

    dirty_val = {"property_id": "8R-51710-abc123def456",
                 "source_file": "ALN Virginia Property Export.xlsx"}
    ok2, problems2 = spine.record_is_clean(dirty_val)
    assert not ok2 and any("value" in p for p in problems2)

    foreign_id = {"property_id": "a3f9c0de-1234-5678-9abc-def012345678"}
    ok3, problems3 = spine.record_is_clean(foreign_id)
    assert not ok3 and any("native id" in p for p in problems3)


def test_record_is_clean_passes_a_native_record():
    clean = {"property_id": spine.property_id(NORFOLK_FIPS, "1234-5678"),
             "8r_class": "C", "8r_form": "garden", "8r_market": "Hampton Roads",
             "source_file": "assessor-norfolk-2026Q2.csv"}
    ok, problems = spine.record_is_clean(clean)
    assert ok and problems == []
