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


def test_a_scraper_change_invalidates_the_freshness_stamp(monkeypatch):
    """The gap that kept the rent gate at 1 of 18,928.

    The stamp said "pulled recently over these favourites" and nothing more,
    so the pull that ran immediately after a scraper fix stamped itself fresh
    and every cycle since skipped. A fix that cannot run is not a fix.
    """
    universe = [{"property_id": "8R-51710-000000000001"},
                {"property_id": "8R-51710-000000000002"}]
    before = lp._favorites_fingerprint(universe)
    monkeypatch.setattr(lp, "PULL_GENERATION", lp.PULL_GENERATION + 1)
    assert lp._favorites_fingerprint(universe) != before, (
        "bumping PULL_GENERATION must force a re-pull")


def test_the_fingerprint_still_tracks_the_favourite_set(monkeypatch):
    """Folding the generation in must not stop starring from mattering."""
    one = [{"property_id": "8R-51710-000000000001"}]
    two = one + [{"property_id": "8R-51710-000000000002"}]
    assert lp._favorites_fingerprint(one) != lp._favorites_fingerprint(two)
    # and it is order-independent, so a reshuffled favourites file is not a
    # spurious re-scrape of 18,000 properties
    assert (lp._favorites_fingerprint(two)
            == lp._favorites_fingerprint(list(reversed(two))))


def test_the_skip_line_reports_how_many_rows_it_is_protecting(tmp_path, capsys,
                                                              monkeypatch):
    """"fresh - skipping" read as health for a month while the table held one
    row. The count belongs in the line that justifies the skip."""
    db = tmp_path / "etl.db"
    universe = [{"property_id": "8R-51710-000000000001"}]
    with sqlite3.connect(db) as conn:
        _stamp(conn, "rent_listings", "t", "u", 12)
        conn.execute("CREATE TABLE rent_listings (property_id TEXT)")
        conn.executemany("INSERT INTO rent_listings VALUES (?)",
                         [("a",), ("b",), ("c",)])
        conn.execute("UPDATE etl_metadata SET description = ? "
                     "WHERE table_name = 'rent_listings'",
                     (f"favset={lp._favorites_fingerprint(universe)}",))
    monkeypatch.setattr(lp, "favorite_universe", lambda: universe)
    assert lp.pull_listings(db) == 0
    out = capsys.readouterr().out
    assert "skipping" in out
    assert "3 rent_listings rows" in out


class _InstantScraper:
    """Answers immediately; every property resolves to an attempted row."""

    def search_by_address(self, name, address, city):
        return "http://example.test/listing"

    def scrape_property(self, url):
        return None                     # not_found - still a recorded attempt


def _pull(db, monkeypatch, universe, budget=3600):
    monkeypatch.setattr(lp, "favorite_universe", lambda: universe)
    monkeypatch.setattr(lp, "_scraper_registry",
                        lambda: {"zillow": _InstantScraper})
    monkeypatch.setattr(lp, "load_manual_urls", lambda: {})
    monkeypatch.setattr(lp, "TIME_BUDGET_S", budget)
    return lp.pull_listings(db, sources=("zillow",))


def _rows(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM rent_listings").fetchone()[0]


def test_an_exhausted_budget_defers_work_and_withholds_the_stamp(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """A truncated pull must not call itself fresh — the stamp is what stops
    the next cycle from finishing the job."""
    db = tmp_path / "etl.db"
    universe = [{"property_id": f"8R-51710-{i:012d}"} for i in range(3)]
    assert _pull(db, monkeypatch, universe, budget=-1) == 0
    assert "deferred" in capsys.readouterr().out
    assert _rows(db) == 0
    # No stamp -> the next cycle is NOT fresh and picks the work up.
    assert _pull(db, monkeypatch, universe) == 3
    assert _rows(db) == 3
    # Completed pull stamps: the cycle after that skips.
    assert _pull(db, monkeypatch, universe) == 0
    assert _rows(db) == 3


def test_a_resumed_pull_skips_pairs_already_paid_for(tmp_path, monkeypatch):
    """Adding a star re-scrapes the NEW property only; scraper politeness
    throttles make re-paying for the old ones hours of waste."""
    db = tmp_path / "etl.db"
    p1 = {"property_id": "8R-51710-000000000001"}
    p2 = {"property_id": "8R-51710-000000000002"}
    assert _pull(db, monkeypatch, [p1]) == 1
    assert _pull(db, monkeypatch, [p1, p2]) == 1     # only p2 is new work
    assert _rows(db) == 2


def test_a_generation_bump_rescrapes_despite_recent_rows(tmp_path, monkeypatch):
    """Rows written by the previous code are recent on the clock but are not
    attempts of THIS pull — the whole point of the generation token."""
    db = tmp_path / "etl.db"
    universe = [{"property_id": "8R-51710-000000000001"},
                {"property_id": "8R-51710-000000000002"}]
    assert _pull(db, monkeypatch, universe) == 2
    monkeypatch.setattr(lp, "PULL_GENERATION", lp.PULL_GENERATION + 1)
    assert _pull(db, monkeypatch, universe) == 2     # both re-attempted
    assert _rows(db) == 4


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
