"""Availability-board / vacancy signal (owner ask 2026-08-03).

Ingest the per-unit board a listing publishes and derive underwriting signal:
at-least-N vacancy, unit mix, per-unit rent range, concession count, next
turn. Wording stays conservative - the board is a FLOOR on vacancy, never the
rent roll.
"""

from __future__ import annotations

import json

from core import unit_signal as us
from etl_listings.base import UnitAvailability


def _board():
    # The screenshot: all 2br/1ba, six units, two "Now", two special offers.
    return [
        UnitAvailability("10", 2, 1, None, "Now", 1050),
        UnitAvailability("34", 2, 1, None, "Sep 18", 1050),
        UnitAvailability("26", 2, 1, None, "Aug 18", 1050),
        UnitAvailability("52", 2, 1, None, "Now", 1050),
        UnitAvailability("61", 2, 1, None, "Aug 18", 1199, special_offer=True),
        UnitAvailability("40", 2, 1, None, "Aug 25", 1199, special_offer=True),
    ]


def test_counts_and_mix_from_the_board():
    s = us.summarize_units(_board())
    assert s["units_available"] == 6
    assert s["units_available_now"] == 2
    assert s["unit_mix"] == "all 2br/1ba"
    assert s["unit_rent_min"] == 1050 and s["unit_rent_max"] == 1199
    assert s["units_special_offers"] == 2


def test_next_available_prefers_now_then_earliest_date():
    s = us.summarize_units(_board())
    assert s["next_available"] == "Now"
    no_now = [u for u in _board() if u.available != "Now"]
    assert us.summarize_units(no_now)["next_available"] == "Aug 18"


def test_mixed_mix_is_broken_down_not_called_all():
    board = [UnitAvailability("1", 1, 1, None, "Now", 900),
             UnitAvailability("2", 2, 1, None, "Now", 1100),
             UnitAvailability("3", 2, 1, None, "Now", 1100)]
    s = us.summarize_units(board)
    assert s["unit_mix"].startswith("2x 2br/1ba") or "2x 2br/1ba" in s["unit_mix"]
    assert "1x 1br/1ba" in s["unit_mix"]


def test_empty_board_is_all_none_zero():
    s = us.summarize_units([])
    assert s["units_available"] == 0 and s["units_json"] is None
    assert s["unit_mix"] is None and s["next_available"] is None


def test_board_snapshot_is_persisted_as_json():
    s = us.summarize_units(_board())
    parsed = json.loads(s["units_json"])
    assert len(parsed) == 6 and parsed[0]["unit"] == "10"


def test_headline_reads_like_the_owner_asked():
    line = us.headline(us.summarize_units(_board()))
    assert "at least 6 units available (2 now)" in line
    assert "all 2br/1ba" in line
    assert "$1,050" in line and "$1,199" in line
    assert "2 concessions" in line


def test_headline_is_none_for_an_empty_board():
    assert us.headline(us.summarize_units([])) is None


# ---- integration through the row builder -------------------------------

def test_listings_pull_row_carries_the_signal():
    from core import listings_pull as lp
    from etl_listings.base import ScrapedListing

    listing = ScrapedListing(
        source="zillow", listing_url="u", listing_name="Madison Terrace",
        listing_address="1 Main St", floorplans=[], units=_board())
    sig = lp._unit_signal(listing)
    assert sig["units_available"] == 6
    assert sig["unit_mix"] == "all 2br/1ba"
    # every availability column the schema expects is present
    for col in ("units_available", "units_available_now", "next_available",
                "unit_mix", "unit_rent_min", "unit_rent_max",
                "units_special_offers", "units_json"):
        assert col in sig


def test_floorplan_fallback_when_no_unit_board():
    """Before a scraper parses the unit table, floorplan available_units
    counts still give a partial vacancy floor."""
    from core import listings_pull as lp
    from etl_listings.base import FloorplanRent, ScrapedListing

    listing = ScrapedListing(
        source="zillow", listing_url="u", listing_name="X", listing_address="a",
        floorplans=[FloorplanRent(1, 900, 950, available_units=2),
                    FloorplanRent(2, 1100, 1200, available_units=3)])
    sig = lp._unit_signal(listing)
    assert sig["units_available"] == 5
    assert sig["unit_mix"] is None      # no per-unit board to derive mix
