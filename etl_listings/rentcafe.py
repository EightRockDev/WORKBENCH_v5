"""RentCafe (Yardi) scraper.

RentCafe is Yardi's property-management portal — Yardi WANTS visibility for
their managed properties, so bot protection is much lighter than CoStar's
Apartments.com. Most HR Class B/C properties managed by Drucker + Falk, Lawson,
Decatur, etc. have RentCafe pages.

URL patterns we typically see:
  https://www.rentcafe.com/apartments-for-rent/us/va/<city>/<slug>/
  https://www.rentcafe.com/apartments/va/<city>/<slug>/floorplans.aspx

Search via RentCafe's site is unreliable (returns top-billed advertisers
across the whole region), so this scraper RELIES on Brian's manual URL in
`Properties/_favorite_listings.json`. If no manual URL, returns None and
the runner records ``scrape_status='not_found'``.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import ClassVar

from bs4 import BeautifulSoup

from .base import BaseListingScraper, FloorplanRent, ScrapedListing


class RentCafeScraper(BaseListingScraper):
    SOURCE_ID: ClassVar[str] = "rentcafe"
    DISPLAY_NAME: ClassVar[str] = "RentCafe (Yardi)"
    BASE_URL: ClassVar[str] = "https://www.rentcafe.com"
    DELAY_SECONDS: ClassVar[float] = 2.5

    def search_by_address(self, name: str, address: str, city: str) -> str | None:
        # RentCafe search returns regionally-paid placements first, not
        # actual matches. Rely on manual URL config instead.
        return None

    def scrape_property(self, listing_url: str) -> ScrapedListing | None:
        r = self.get(listing_url)
        if r is None or r.status_code != 200:
            return None
        return self._parse(r.text, listing_url)

    def _parse(self, html: str, listing_url: str) -> ScrapedListing | None:
        soup = BeautifulSoup(html, "lxml")

        name = self._select_text(soup, [
            "h1.community-name",
            "h1[itemprop='name']",
            ".community-header h1",
            ".header-property-name",
            "h1",
        ])
        address = self._select_text(soup, [
            "[itemprop='streetAddress']",
            ".community-address",
            ".header-address",
            ".address",
        ])
        concession = self._extract_concession(soup)
        floorplans = self._extract_floorplans(soup)
        amenities = self._extract_amenities(soup)
        photos = self._extract_photos(soup)

        return ScrapedListing(
            source=self.SOURCE_ID,
            listing_url=listing_url,
            listing_name=name,
            listing_address=address,
            floorplans=floorplans,
            concession_text=concession,
            amenities=amenities,
            photo_urls=photos,
        )

    @staticmethod
    def _select_text(soup, selectors):
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    return t
        return None

    @staticmethod
    def _extract_concession(soup) -> str | None:
        for sel in (
            ".specials-list",
            ".special-message",
            ".specials-container",
            ".specials",
            "[class*='Special']",
            ".rate-special",
            ".promo-banner",
        ):
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) > 3:
                    return t
        return None

    @staticmethod
    def _extract_floorplans(soup) -> list[FloorplanRent]:
        out: list[FloorplanRent] = []
        # RentCafe typically renders each floorplan as a row/card with bed/bath/rent
        rows = soup.select(
            ".floorplan-row, .fp-row, .floor-plan-info, "
            "[class*='floorplan-card'], [class*='floor-plan']"
        )
        for row in rows:
            text = row.get_text(" ", strip=True)
            beds = RentCafeScraper._parse_bedrooms(text)
            rent_low, rent_high = RentCafeScraper._parse_rent_range(text)
            if rent_low is not None or rent_high is not None:
                out.append(FloorplanRent(
                    bedrooms=beds if beds is not None else -1,
                    rent_low=rent_low, rent_high=rent_high,
                ))
        return out

    @staticmethod
    def _parse_bedrooms(text: str) -> int | None:
        t = text.lower()
        if "studio" in t or "efficiency" in t:
            return 0
        m = re.search(r"(\d+)\s*(?:br|bed|bedroom|bd)", t)
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_rent_range(text: str):
        nums = []
        for m in re.finditer(r"\$\s?([\d,]+)", text):
            try:
                v = float(m.group(1).replace(",", ""))
                if 400 <= v <= 10_000:
                    nums.append(v)
            except ValueError:
                continue
        if not nums:
            return None, None
        if len(nums) == 1:
            return nums[0], nums[0]
        return min(nums), max(nums)

    @staticmethod
    def _extract_amenities(soup) -> list[str]:
        out = []
        for sel in (".amenities-list li", ".amenity li", "[class*='amenity'] li"):
            for el in soup.select(sel):
                t = el.get_text(" ", strip=True)
                if t and len(t) < 80:
                    out.append(t)
            if out:
                break
        seen = set()
        return [a for a in out if not (a in seen or seen.add(a))][:50]

    @staticmethod
    def _extract_photos(soup) -> list[str]:
        urls = []
        for img in soup.select(".gallery img, .property-gallery img, "
                                "[class*='photo'] img, .slick-slide img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy")
            if src and src.startswith("http"):
                urls.append(src)
        return urls[:20]
