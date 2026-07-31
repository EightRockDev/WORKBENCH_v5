"""In-workbench listings runner: bands math, freshness gate, no-favorites."""

from __future__ import annotations

import sqlite3

from core import listings_pull as lp
from core.public_data import _stamp
from etl_listings.base import FloorplanRent, ScrapedListing


def _listing(floorplans, concession=None):
    return ScrapedListing(
        source="zillow", listing_url="u", listing_name="Test Apts",
        listing_address="1 Main St", floorplans=floorplans,
        concession_text=concession, amenities=[], photo_urls=[])


def test_rent_bands_midpoints_and_concessions():
    bands = lp._rent_bands(_listing([
        FloorplanRent(1, 1400, 1600),
        FloorplanRent(1, 1350, None),      # widens the 1BR low
        FloorplanRent(2, 1800, 2000),
        FloorplanRent(0, 1100, 1200),      # studio ignored in bands
    ], concession="One month free on a 12 month lease"))
    assert bands["one_br_rent_low"] == 1350
    assert bands["one_br_rent_high"] == 1600
    # 1BR mid = 1475, minus 1 month free over 12 -> ~1352
    assert 1340 < bands["effective_one_br_rent"] < 1360
    assert 1740 < bands["effective_two_br_rent"] < 1760


def test_freshness_gate_and_no_favorites(tmp_path, monkeypatch):
    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        _stamp(conn, "rent_listings", "t", "u", 12)
    assert lp.pull_listings(db) == 0            # fresh-skip, no network
    # Stale + no favorites -> polite no-op, still no network.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE etl_metadata SET last_pull_at = '2020-01-01'")
    monkeypatch.setattr(lp, "favorite_universe", lambda: [])
    assert lp.pull_listings(db) == 0
