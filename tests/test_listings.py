"""Tests for the rent-listings scraper system.

Coverage:
  - ConcessionParser regex handling (10+ canonical patterns)
  - Effective rent math (months-free, dollar-off, edge cases)
  - ApartmentsDotComScraper HTML parsing against fixture pages
  - Runner-level: per-property row construction, status tracking,
    URL cache behavior

Skips live integration tests by default. Run live tests with:
  pytest tests/test_listings.py -m live
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make hampton-roads-etl importable from tests
_ETL_DIR = Path(__file__).resolve().parents[2] / "hampton-roads-etl"
if str(_ETL_DIR) not in sys.path:
    sys.path.insert(0, str(_ETL_DIR))

from pullers.listings.apartments_com import ApartmentsDotComScraper  # noqa: E402
from pullers.listings.concessions import (  # noqa: E402
    ParsedConcession,
    compute_effective_rent,
    parse_concession_text,
)
from pullers.listings.rentcafe import RentCafeScraper  # noqa: E402
from pullers.listings.zillow import ZillowScraper  # noqa: E402

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "listings"


# ---------------------------------------------------------------------------
# ConcessionParser — regex coverage
# ---------------------------------------------------------------------------

class TestConcessionRegex:
    """Each test = one canonical concession pattern from real Apartments.com listings."""

    def test_one_month_free(self):
        c = parse_concession_text("1 month free on 12-month lease", use_ai_fallback=False)
        assert c.months_free == 1.0
        assert c.lease_term_months == 12
        assert c.confidence == "regex"

    def test_two_months_free(self):
        c = parse_concession_text("2 months free!", use_ai_fallback=False)
        assert c.months_free == 2.0
        assert c.has_concession

    def test_one_and_half_months(self):
        c = parse_concession_text("Get 1.5 months free", use_ai_fallback=False)
        assert c.months_free == 1.5

    def test_word_form_two_months(self):
        c = parse_concession_text("Special: two months free rent", use_ai_fallback=False)
        assert c.months_free == 2.0

    def test_six_weeks_free(self):
        c = parse_concession_text("6 weeks free on select units", use_ai_fallback=False)
        assert c.months_free == pytest.approx(6 / 4.33, rel=0.01)

    def test_dollar_off(self):
        c = parse_concession_text("$500 off first month", use_ai_fallback=False)
        assert c.dollar_off == 500.0
        assert c.months_free == 0.0

    def test_dollar_off_with_comma(self):
        c = parse_concession_text("$1,000 off your first month's rent",
                                  use_ai_fallback=False)
        assert c.dollar_off == 1000.0

    def test_look_and_lease(self):
        c = parse_concession_text("Look + Lease — $750 off", use_ai_fallback=False)
        assert c.dollar_off == 750.0

    def test_move_in_special(self):
        c = parse_concession_text("Move-in special: $1,500", use_ai_fallback=False)
        assert c.dollar_off == 1500.0

    def test_no_concession(self):
        c = parse_concession_text("Welcome home!", use_ai_fallback=False)
        assert not c.has_concession
        assert c.confidence == "none"

    def test_empty_input(self):
        c = parse_concession_text(None, use_ai_fallback=False)
        assert not c.has_concession

    def test_implicit_free_rent(self):
        c = parse_concession_text("Get free rent today", use_ai_fallback=False)
        # Should default to 1 month
        assert c.months_free == 1.0


# ---------------------------------------------------------------------------
# Effective rent math
# ---------------------------------------------------------------------------

class TestEffectiveRent:
    def test_no_concession_returns_asking(self):
        c = ParsedConcession()
        assert compute_effective_rent(1500.0, c) == 1500.0

    def test_one_month_free_on_twelve(self):
        """$1,500 asking × 11/12 = $1,375 effective"""
        c = ParsedConcession(months_free=1.0, lease_term_months=12)
        assert compute_effective_rent(1500.0, c) == pytest.approx(1375.0)

    def test_two_months_free_on_twelve(self):
        c = ParsedConcession(months_free=2.0, lease_term_months=12)
        assert compute_effective_rent(1500.0, c) == pytest.approx(1250.0)

    def test_dollar_off(self):
        """$1,500 × 12 = $18,000; less $1,000 off = $17,000 / 12 = $1,416.67"""
        c = ParsedConcession(dollar_off=1000.0, lease_term_months=12)
        assert compute_effective_rent(1500.0, c) == pytest.approx(1416.67, abs=0.01)

    def test_zero_asking_safe(self):
        c = ParsedConcession(months_free=1.0)
        assert compute_effective_rent(0.0, c) == 0.0

    def test_negative_effective_floored_at_zero(self):
        """Dollar-off > annual rent → effective floored at 0 (data error guard)"""
        c = ParsedConcession(dollar_off=100_000.0, lease_term_months=12)
        result = compute_effective_rent(500.0, c)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Apartments.com HTML parsing
# ---------------------------------------------------------------------------

class TestApartmentsHtmlParse:
    def _parse(self, fixture_name: str):
        html = (_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        scraper = ApartmentsDotComScraper()
        return scraper._parse_property_html(html, "https://test/property/")

    def test_parses_name_and_address(self):
        listing = self._parse("apartments_with_concession.html")
        assert listing.listing_name == "Green Tree Apartments"
        assert "Green Tree Circle" in listing.listing_address
        assert listing.source == "apartments_com"

    def test_extracts_floorplans(self):
        listing = self._parse("apartments_with_concession.html")
        beds = {fp.bedrooms for fp in listing.floorplans}
        assert 1 in beds
        assert 2 in beds
        assert 3 in beds
        # 1BR should have $1,425-$1,575 range
        one_br = next(fp for fp in listing.floorplans if fp.bedrooms == 1)
        assert one_br.rent_low == 1425.0
        assert one_br.rent_high == 1575.0

    def test_extracts_concession_banner(self):
        listing = self._parse("apartments_with_concession.html")
        assert listing.concession_text is not None
        assert "1 Month Free" in listing.concession_text

    def test_no_concession_when_missing(self):
        listing = self._parse("apartments_no_concession.html")
        assert listing.concession_text is None
        # But floorplans should still parse
        assert len(listing.floorplans) >= 2

    def test_extracts_amenities(self):
        listing = self._parse("apartments_with_concession.html")
        assert any("Pool" in a for a in listing.amenities)
        assert any("Pet" in a for a in listing.amenities)

    def test_extracts_photos(self):
        listing = self._parse("apartments_with_concession.html")
        assert len(listing.photo_urls) >= 1
        assert all(u.startswith("http") for u in listing.photo_urls)

    def test_handles_weeks_free_concession(self):
        listing = self._parse("apartments_weeks_free.html")
        assert listing.concession_text is not None
        assert "6 Weeks Free" in listing.concession_text
        # Verify the concession parser handles this format
        c = parse_concession_text(listing.concession_text, use_ai_fallback=False)
        assert c.months_free == pytest.approx(6 / 4.33, rel=0.01)

    def test_studio_floorplan_parsed(self):
        listing = self._parse("apartments_no_concession.html")
        studio = next(
            (fp for fp in listing.floorplans if fp.bedrooms == 0), None,
        )
        assert studio is not None
        assert studio.rent_low == 1200.0


# ---------------------------------------------------------------------------
# Address-match heuristics
# ---------------------------------------------------------------------------

class TestAddressMatch:
    def test_first_number_extracted(self):
        s = ApartmentsDotComScraper
        assert s._first_number("749 Green Tree Circle") == "749"
        assert s._first_number("Suite 100, 200 Main St") == "100"
        assert s._first_number("no number here") == ""

    def test_street_name_overlap_positive(self):
        s = ApartmentsDotComScraper
        assert s._street_name_overlap(
            "749 Green Tree Circle",
            "700 Green Tree Circle",
        )

    def test_street_name_overlap_negative(self):
        s = ApartmentsDotComScraper
        assert not s._street_name_overlap(
            "749 Green Tree Circle",
            "100 Driftwood Lane",
        )

    def test_query_strips_apartments_suffix(self):
        q = ApartmentsDotComScraper._build_query(
            "Green Tree Apartments", "749 Green Tree Cir", "Chesapeake",
        )
        assert "Apartments" not in q
        assert "Green Tree" in q
        assert "Chesapeake" in q


# ---------------------------------------------------------------------------
# End-to-end integration via mocked HTTP (no live calls)
# ---------------------------------------------------------------------------

class TestIntegrationMocked:
    """Build a ScrapedListing → run runner's row builder → assert DataFrame row."""

    def test_runner_row_construction_success(self, monkeypatch):
        from pullers.listings.runner import _scrape_one
        from pullers.listings.base import FloorplanRent, ScrapedListing

        # Stub scraper that returns a known result
        class FakeScraper:
            SOURCE_ID = "apartments_com"

            def search_by_address(self, name, address, city):
                return "https://apartments.com/fake/"

            def scrape_property(self, url):
                return ScrapedListing(
                    source="apartments_com",
                    listing_url=url,
                    listing_name="Fake Property",
                    listing_address="123 Test Lane, Norfolk, VA",
                    floorplans=[
                        FloorplanRent(bedrooms=1, rent_low=1300, rent_high=1400),
                        FloorplanRent(bedrooms=2, rent_low=1600, rent_high=1700),
                    ],
                    concession_text="1 month free",
                )

        aln = {
            "property_id": "test-1",
            "name": "Fake Property",
            "address": "123 Test Lane",
            "city": "Norfolk",
            "units": 100,
        }
        row = _scrape_one(FakeScraper(), aln, cached_url=None)

        assert row["scrape_status"] == "success"
        assert row["one_br_rent_low"] == 1300
        assert row["two_br_rent_high"] == 1700
        # Effective rent should be discounted by 1/12
        # 1BR mid = (1300+1400)/2 = 1350; effective = 1350 * 11/12 = 1237.5
        assert row["effective_one_br_rent"] == pytest.approx(1237.5)
        assert row["concession_months_free"] == 1.0

    def test_runner_handles_not_found(self):
        from pullers.listings.runner import _scrape_one

        class FakeScraper:
            SOURCE_ID = "apartments_com"
            def search_by_address(self, name, address, city):
                return None
            def scrape_property(self, url):
                raise AssertionError("should not be called")

        aln = {
            "property_id": "test-2", "name": "Missing", "address": "999 Nowhere",
            "city": "Norfolk", "units": 50,
        }
        row = _scrape_one(FakeScraper(), aln, cached_url=None)
        assert row["scrape_status"] == "not_found"
        assert row["listing_url"] is None

    def test_runner_handles_blocked(self):
        from pullers.listings.runner import _scrape_one

        class FakeScraper:
            SOURCE_ID = "apartments_com"
            def search_by_address(self, name, address, city):
                return "https://apartments.com/fake/"
            def scrape_property(self, url):
                return None  # simulating block

        aln = {
            "property_id": "test-3", "name": "Blocked", "address": "1 Test",
            "city": "Norfolk", "units": 50,
        }
        row = _scrape_one(FakeScraper(), aln, cached_url=None)
        assert row["scrape_status"] == "blocked"

    def test_url_cache_skips_search(self):
        """If a cached URL is provided, scraper should skip search step."""
        from pullers.listings.runner import _scrape_one
        from pullers.listings.base import ScrapedListing

        search_called = []

        class FakeScraper:
            SOURCE_ID = "apartments_com"
            def search_by_address(self, *a, **kw):
                search_called.append(True)
                return None
            def scrape_property(self, url):
                return ScrapedListing(
                    source="apartments_com",
                    listing_url=url,
                    listing_name="Cached",
                    listing_address="",
                )

        aln = {
            "property_id": "test-4", "name": "Cached", "address": "1 Test",
            "city": "Norfolk", "units": 50,
        }
        row = _scrape_one(
            FakeScraper(), aln,
            cached_url="https://apartments.com/cached/",
        )
        assert not search_called, "search should not be called when URL cached"
        assert row["listing_url"] == "https://apartments.com/cached/"
        assert row["scrape_status"] == "success"


# ---------------------------------------------------------------------------
# Live integration — marked, skipped by default
# ---------------------------------------------------------------------------

class TestRentCafeParse:
    def _parse(self, fixture_name: str):
        html = (_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        return RentCafeScraper()._parse(html, "https://test/")

    def test_basic_parse(self):
        listing = self._parse("rentcafe_basic.html")
        assert listing.listing_name == "Andover"
        assert "Norfolk" in (listing.listing_address or "")
        assert listing.source == "rentcafe"

    def test_floorplans(self):
        listing = self._parse("rentcafe_basic.html")
        beds = {fp.bedrooms for fp in listing.floorplans}
        assert {1, 2, 3}.issubset(beds)
        one_br = next(fp for fp in listing.floorplans if fp.bedrooms == 1)
        assert one_br.rent_low == 1295
        assert one_br.rent_high == 1395

    def test_concession(self):
        listing = self._parse("rentcafe_basic.html")
        assert listing.concession_text is not None
        assert "$500 off" in listing.concession_text

    def test_amenities(self):
        listing = self._parse("rentcafe_basic.html")
        assert "Pool" in listing.amenities

    def test_search_returns_none(self):
        """RentCafe search is unreliable — scraper relies on manual URLs."""
        assert RentCafeScraper().search_by_address("X", "Y", "Z") is None


class TestZillowParse:
    def _parse(self, fixture_name: str):
        html = (_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        return ZillowScraper()._parse(html, "https://test/")

    def test_uses_json_ld(self):
        listing = self._parse("zillow_with_jsonld.html")
        assert listing.listing_name == "Driftwood Apartments"
        assert "140 Driftwood Lane" in (listing.listing_address or "")
        assert "Virginia Beach" in (listing.listing_address or "")

    def test_floorplans_from_offers(self):
        listing = self._parse("zillow_with_jsonld.html")
        # JSON-LD offers should produce 2 floorplans
        assert len(listing.floorplans) == 2
        rents = sorted(fp.rent_low for fp in listing.floorplans if fp.rent_low)
        assert 1395 in rents
        assert 1650 in rents

    def test_concession_extracted_from_html(self):
        listing = self._parse("zillow_with_jsonld.html")
        # Concession is in <div class="special-message"> on the page
        assert listing.concession_text is not None
        assert "$500" in listing.concession_text

    def test_photos_from_json_ld(self):
        listing = self._parse("zillow_with_jsonld.html")
        assert len(listing.photo_urls) >= 1
        assert all(u.startswith("http") for u in listing.photo_urls)

    def test_search_returns_none(self):
        assert ZillowScraper().search_by_address("X", "Y", "Z") is None


class TestSourceRegistry:
    """Verify all four scrapers are registered + can be looked up."""

    def test_registry_includes_all_four_sources(self):
        from pullers.listings.runner import SOURCES
        assert "apartments_com" in SOURCES
        assert "rentcafe" in SOURCES
        assert "zillow" in SOURCES
        assert "property_site" in SOURCES

    def test_default_pull_listings_sources(self):
        """Default `pull_listings` should try all 4 sources for redundancy."""
        import inspect
        from pullers.listings.runner import pull_listings
        sig = inspect.signature(pull_listings)
        default_sources = sig.parameters["sources"].default
        assert "rentcafe" in default_sources
        assert "zillow" in default_sources
        assert "apartments_com" in default_sources
        assert "property_site" in default_sources

    def test_default_scope_is_favorites(self):
        import inspect
        from pullers.listings.runner import pull_listings
        sig = inspect.signature(pull_listings)
        assert sig.parameters["scope"].default == "favorites"


@pytest.mark.skip(reason="Live test — hits real Apartments.com. Run manually with -m live")
@pytest.mark.live
def test_live_apartments_dot_com_smoke():
    """Hit Apartments.com for one known HR property. Skip by default."""
    scraper = ApartmentsDotComScraper()
    url = scraper.search_by_address(
        "Green Tree", "749 Green Tree Circle", "Chesapeake",
    )
    assert url is not None
    assert "apartments.com" in url

    listing = scraper.scrape_property(url)
    assert listing is not None
    assert listing.listing_name
    assert len(listing.floorplans) > 0
