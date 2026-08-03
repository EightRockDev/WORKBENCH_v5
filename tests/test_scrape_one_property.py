"""The per-property "Scrape this property now" button (ui.listings_panel).

It used to import the v2.4.1 `hampton-roads-etl/pullers` package, which is not
in the v5 tree — so the button always failed with "No module named 'pullers'".
It now runs on the SAME in-workbench scraper stack as the nightly autopilot
(core.listings_pull + etl_listings). These tests pin that: a successful scrape
writes a generation-tagged row, and no import of `pullers` happens.
"""

from __future__ import annotations

import sqlite3
import sys

from core import listings_pull as lp
from etl_listings.base import FloorplanRent, ScrapedListing
from ui import listings_panel


class _FakeScraper:
    def __init__(self, listing):
        self._listing = listing

    def scrape_property(self, url):
        return self._listing


def _listing():
    return ScrapedListing(
        source="zillow", listing_url="u", listing_name="Madison Terrace",
        listing_address="1 Main St, Hopewell VA",
        floorplans=[FloorplanRent(1, 1300, 1400), FloorplanRent(2, 1600, 1700)],
        concession_text="1 month free", amenities=[], photo_urls=[])


def _wire(monkeypatch, db, listing):
    monkeypatch.setattr(listings_panel, "_listings_db", lambda: db)
    monkeypatch.setattr(lp, "load_manual_urls",
                        lambda: {"8R-1": {"zillow": "https://zillow/x"}})
    monkeypatch.setattr(lp, "_scraper_registry",
                        lambda: {"zillow": lambda: _FakeScraper(listing)})


def test_scrape_writes_a_generation_tagged_row(tmp_path, monkeypatch):
    db = tmp_path / "etl.db"
    _wire(monkeypatch, db, _listing())
    n = listings_panel._scrape_one_property(
        "8R-1", "", {"name": "Madison Terrace", "address": "1 Main St",
                     "city": "Hopewell"})
    assert n == 1
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT scrape_status, one_br_rent_low, two_br_rent_high, "
            "pull_generation FROM rent_listings").fetchone()
    assert row[0] == "success"
    assert row[1] == 1300 and row[2] == 1700
    assert row[3] == lp.PULL_GENERATION


def test_no_configured_url_is_a_clean_zero(tmp_path, monkeypatch):
    db = tmp_path / "etl.db"
    _wire(monkeypatch, db, _listing())
    monkeypatch.setattr(lp, "load_manual_urls", lambda: {})   # nothing saved
    assert listings_panel._scrape_one_property("8R-1", "", {}) == 0


def test_a_failing_scraper_records_error_not_crash(tmp_path, monkeypatch):
    db = tmp_path / "etl.db"
    _wire(monkeypatch, db, _listing())

    class _Boom:
        def scrape_property(self, url):
            raise RuntimeError("site blocked")
    monkeypatch.setattr(lp, "_scraper_registry", lambda: {"zillow": _Boom})
    n = listings_panel._scrape_one_property("8R-1", "", {"city": "Hopewell"})
    assert n == 1
    with sqlite3.connect(db) as conn:
        status, err = conn.execute(
            "SELECT scrape_status, error_message FROM rent_listings").fetchone()
    assert status == "error" and "site blocked" in err


def test_the_button_path_never_imports_pullers(tmp_path, monkeypatch):
    """The regression: 'No module named pullers'. The v5 button must not
    depend on the v2.4.1 ETL package at all."""
    db = tmp_path / "etl.db"
    _wire(monkeypatch, db, _listing())
    for m in [m for m in sys.modules if m.split(".")[0] == "pullers"]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(
        __import__("builtins"), "__import__",
        _blocking_import(sys.modules["builtins"].__import__))
    listings_panel._scrape_one_property(
        "8R-1", "", {"name": "X", "address": "1 Main St", "city": "Hopewell"})


def _blocking_import(real):
    def _imp(name, *a, **k):
        if name.split(".")[0] == "pullers":
            raise AssertionError("the v5 scrape button imported 'pullers'")
        return real(name, *a, **k)
    return _imp
