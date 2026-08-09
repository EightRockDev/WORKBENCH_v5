"""Wave-1 national expansion: metro labelling must self-label, never mislabel
(the old code hardcoded r8_market='Hampton Roads' for every parcel)."""

from __future__ import annotations

from core import market_data as md


def test_hr_cities_map_to_hampton_roads():
    for c in ("Norfolk", "Virginia Beach", "Chesapeake"):
        assert md.metro_for(c) == "Hampton Roads"


def test_wave1_metros_self_identify_not_hampton_roads():
    assert md.metro_for("Charlotte") == "Charlotte"
    assert md.metro_for("Nashville") == "Nashville"
    assert md.metro_for("Atlanta") == "Atlanta"
    for c in ("Charlotte", "Nashville", "Atlanta", "Raleigh"):
        assert md.metro_for(c) != "Hampton Roads"


def test_unmapped_city_self_labels_rather_than_mislabelling():
    # A newly-pulled locality must never inherit the wrong metro.
    assert md.metro_for("Austin") == "Austin"
    assert md.metro_for("") == ""


def test_wave1_cities_have_fips_so_the_backbone_can_build_them():
    for c in ("Raleigh", "Charlotte", "Nashville", "Atlanta"):
        assert c in md.CITY_TO_COUNTY_FIPS_5
        assert len(md.CITY_TO_COUNTY_FIPS_5[c]) == 5
