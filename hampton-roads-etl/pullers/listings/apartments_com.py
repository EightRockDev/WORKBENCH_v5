"""Apartments.com scraper.

Property pages live at ``apartments.com/<property-slug>/`` and contain:
  - Property name + address in <h1> / address block
  - Floorplan tabs with rent ranges (selectable by bedroom count)
  - Concession callout — usually in a banner above the fold or in a
    "Special Offers" section
  - Amenities list, photo URLs

Apartments.com IS protected by Cloudflare and rate-limits scrapers. This
implementation uses polite ``requests``-based scraping which works at
weekly cadence + 3-sec delay; production runs at higher volume should
swap to a Playwright-based fetcher (subclass override of ``get()``).
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import ClassVar

from bs4 import BeautifulSoup

from .base import BaseListingScraper, FloorplanRent, ScrapedListing

LOG = logging.getLogger(__name__)


class ApartmentsDotComScraper(BaseListingScraper):
    SOURCE_ID: ClassVar[str] = "apartments_com"
    DISPLAY_NAME: ClassVar[str] = "Apartments.com"
    BASE_URL: ClassVar[str] = "https://www.apartments.com"
    DELAY_SECONDS: ClassVar[float] = 3.0     # polite

    # ---- search ----
    def search_by_address(
        self, name: str, address: str, city: str,
    ) -> str | None:
        """Try to find the property's listing URL.

        Strategy:
          1. Direct: search by "<name> <city>" (most reliable for branded properties)
          2. Fallback: search by street address
          3. Take the first organic result; verify the result's listing
             address contains the street number of the ALN address before
             accepting (guards against name collisions).
        """
        query = self._build_query(name, address, city)
        url = f"{self.BASE_URL}/search/?q={urllib.parse.quote(query)}"
        r = self.get(url)
        if r is None or r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "lxml")
        # Apartments.com search result links — multiple selectors over the years
        for sel in (
            "a.property-link",
            "article.placard a.property-link",
            "a[data-listingid]",
            ".placard-content a",
        ):
            link = soup.select_one(sel)
            if link and link.get("href"):
                href = link["href"]
                if not href.startswith("http"):
                    href = urllib.parse.urljoin(self.BASE_URL, href)
                # Sanity-check: the candidate URL should look like a property page
                if "/search/" not in href and self.BASE_URL in href:
                    return self._verify_address_match(href, address)
        return None

    @staticmethod
    def _build_query(name: str, address: str, city: str) -> str:
        # Strip "Apartments" suffix from the ALN name — it's noise
        n = re.sub(r"\s+apartments?\b", "", name.strip(), flags=re.IGNORECASE)
        return f"{n} {city}".strip()

    def _verify_address_match(
        self,
        candidate_url: str,
        aln_address: str,
    ) -> str | None:
        """Sanity-check that the candidate listing's address matches the ALN
        street number, to avoid name-collision misses. Returns the URL if
        good, None if it's clearly a different property."""
        r = self.get(candidate_url)
        if r is None or r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        # Extract the displayed address from the listing
        for sel in (
            "h2.propertyAddressContainer",
            ".propertyAddress",
            "[class*='propertyAddress']",
            ".header-address",
        ):
            addr_el = soup.select_one(sel)
            if addr_el:
                listing_addr = addr_el.get_text(" ", strip=True)
                # Match: shared street number?
                aln_num = self._first_number(aln_address)
                listing_num = self._first_number(listing_addr)
                if aln_num and listing_num and aln_num == listing_num:
                    return candidate_url
                # Soft pass: street name overlap (handles cases where the
                # complex's main address differs from ALN's leasing-office
                # address — common for multi-parcel complexes)
                if self._street_name_overlap(aln_address, listing_addr):
                    return candidate_url
                return None
        # No address visible — accept tentatively, downstream scrape will
        # confirm or reject
        return candidate_url

    @staticmethod
    def _first_number(s: str) -> str:
        m = re.search(r"\b(\d+)\b", s or "")
        return m.group(1) if m else ""

    @staticmethod
    def _street_name_overlap(a: str, b: str) -> bool:
        words_a = set(re.findall(r"[a-z]{4,}", (a or "").lower()))
        words_b = set(re.findall(r"[a-z]{4,}", (b or "").lower()))
        return len(words_a & words_b) >= 1

    # ---- property scrape ----
    def scrape_property(self, listing_url: str) -> ScrapedListing | None:
        r = self.get(listing_url)
        if r is None or r.status_code != 200:
            return None
        return self._parse_property_html(r.text, listing_url)

    def _parse_property_html(
        self,
        html: str,
        listing_url: str,
    ) -> ScrapedListing | None:
        """Parse an Apartments.com property page.

        Robust to layout changes: tries multiple selectors per field, returns
        partial data when fields are missing rather than failing.
        """
        soup = BeautifulSoup(html, "lxml")

        name = self._select_text(soup, [
            "h1.propertyName",
            "h1[class*='propertyName']",
            "h1",
        ])
        address = self._select_text(soup, [
            "h2.propertyAddressContainer",
            ".propertyAddress",
            "[class*='propertyAddress']",
            ".header-address",
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

    # ---- field extractors ----
    @staticmethod
    def _select_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    return t
        return None

    @staticmethod
    def _extract_concession(soup: BeautifulSoup) -> str | None:
        """Find the concession banner/callout. Multiple selectors because
        Apartments.com has rotated the markup several times."""
        for sel in (
            ".specials-data",
            "[class*='specials']",
            "[class*='Special']",
            ".rentSpecialsRow",
            ".specialsLabel",
            ".specials-container",
        ):
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) > 3:
                    return t
        return None

    @staticmethod
    def _extract_floorplans(soup: BeautifulSoup) -> list[FloorplanRent]:
        """Parse the floorplan / pricing section.

        Apartments.com renders floorplan rows with bedroom count + rent range.
        Selectors are intentionally broad — the page layout has changed
        several times.
        """
        out: list[FloorplanRent] = []
        # Try the modern "pricingGridItem" layout first
        rows = soup.select(
            ".pricingGridItem, .rentRollRow, [class*='floorplanRow'], "
            "[class*='pricing-grid']"
        )
        for row in rows:
            text = row.get_text(" ", strip=True)
            beds = ApartmentsDotComScraper._parse_bedrooms(text)
            rent_low, rent_high = ApartmentsDotComScraper._parse_rent_range(text)
            if rent_low is not None or rent_high is not None:
                out.append(FloorplanRent(
                    bedrooms=beds if beds is not None else -1,
                    rent_low=rent_low,
                    rent_high=rent_high,
                ))
        return out

    @staticmethod
    def _parse_bedrooms(text: str) -> int | None:
        t = text.lower()
        if "studio" in t:
            return 0
        m = re.search(r"(\d+)\s*(?:br|bed|bedroom)", t)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _parse_rent_range(text: str) -> tuple[float | None, float | None]:
        """Find one or two dollar amounts that look like rents ($500-$5000)."""
        numbers = []
        for m in re.finditer(r"\$\s?([\d,]+)", text):
            try:
                v = float(m.group(1).replace(",", ""))
                if 400 <= v <= 10_000:
                    numbers.append(v)
            except ValueError:
                continue
        if not numbers:
            return None, None
        if len(numbers) == 1:
            return numbers[0], numbers[0]
        return min(numbers), max(numbers)

    @staticmethod
    def _extract_amenities(soup: BeautifulSoup) -> list[str]:
        out: list[str] = []
        for sel in (".amenityCard", ".specGroup li", "[class*='amenity'] li"):
            for el in soup.select(sel):
                t = el.get_text(" ", strip=True)
                if t and len(t) < 80:
                    out.append(t)
            if out:
                break
        # Dedup while preserving order
        seen: set[str] = set()
        return [a for a in out if not (a in seen or seen.add(a))][:50]

    @staticmethod
    def _extract_photos(soup: BeautifulSoup) -> list[str]:
        urls: list[str] = []
        for img in soup.select(
            ".gallery img, .photos img, [class*='photo'] img, "
            ".carousel img"
        ):
            src = img.get("src") or img.get("data-src")
            if src and src.startswith("http"):
                urls.append(src)
        return urls[:20]  # cap to avoid blowing up storage
