"""Zillow Rentals scraper.

Zillow has medium-strength bot detection (Akamai-like) but direct-URL
fetches often succeed where searches don't. As with RentCafe, this scraper
RELIES on Brian's manual URL — search is not implemented because Zillow's
search results page is JavaScript-heavy and aggressively rate-limited.

URL patterns:
  https://www.zillow.com/apartments/<city>-<state>/<property-slug>/<id>/
  https://www.zillow.com/b/<community-slug>-<id>/

Zillow embeds rich JSON-LD structured data in their pages — we look there
first (clean schema), then fall back to HTML scraping.
"""

from __future__ import annotations

import json
import re
from typing import ClassVar

from bs4 import BeautifulSoup

from .base import BaseListingScraper, FloorplanRent, ScrapedListing


class ZillowScraper(BaseListingScraper):
    SOURCE_ID: ClassVar[str] = "zillow"
    DISPLAY_NAME: ClassVar[str] = "Zillow Rentals"
    BASE_URL: ClassVar[str] = "https://www.zillow.com"
    DELAY_SECONDS: ClassVar[float] = 4.0   # slightly slower; Zillow is touchy

    def search_by_address(self, name: str, address: str, city: str) -> str | None:
        # Zillow's rental search is heavily JS-driven + rate-limited. Rely
        # on manual URL config.
        return None

    def scrape_property(self, listing_url: str) -> ScrapedListing | None:
        r = self.get(listing_url)
        if r is None or r.status_code != 200:
            return None
        return self._parse(r.text, listing_url)

    def _parse(self, html: str, listing_url: str) -> ScrapedListing | None:
        soup = BeautifulSoup(html, "lxml")

        # Try JSON-LD first — Zillow embeds Property + RentalListings schema
        json_data = self._extract_json_ld(soup)

        name = self._select_text(soup, [
            "h1.community-name",
            "h1[class*='community']",
            "h1[class*='Heading']",
            "h1",
        ])
        if not name and json_data:
            name = json_data.get("name")

        address = self._select_text(soup, [
            ".address-line",
            "h4[class*='address']",
            "[class*='Address']",
        ])
        if not address and json_data:
            addr_obj = json_data.get("address") or {}
            if isinstance(addr_obj, dict):
                parts = [
                    addr_obj.get("streetAddress"),
                    addr_obj.get("addressLocality"),
                    addr_obj.get("addressRegion"),
                    addr_obj.get("postalCode"),
                ]
                address = ", ".join(p for p in parts if p)

        concession = self._extract_concession(soup)
        floorplans = self._extract_floorplans(soup, json_data)
        amenities = self._extract_amenities(soup)
        photos = self._extract_photos(soup, json_data)

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
    def _extract_json_ld(soup) -> dict | None:
        for el in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(el.string or "")
                if isinstance(data, dict):
                    return data
                if isinstance(data, list) and data:
                    return data[0]
            except (json.JSONDecodeError, TypeError):
                continue
        return None

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
            "[class*='Special']",
            "[class*='special']",
            "[class*='Promo']",
            ".incentive",
            ".banner-message",
        ):
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) > 3 and ("free" in t.lower() or "off" in t.lower()
                                          or "month" in t.lower() or "$" in t):
                    return t
        return None

    @staticmethod
    def _extract_floorplans(soup, json_data: dict | None) -> list[FloorplanRent]:
        out: list[FloorplanRent] = []

        # JSON-LD path first — cleaner
        if json_data:
            offers = json_data.get("offers") or json_data.get("itemListElement")
            if isinstance(offers, dict):
                offers = [offers]
            if isinstance(offers, list):
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    price = offer.get("price") or offer.get("lowPrice")
                    high = offer.get("highPrice") or price
                    if price:
                        try:
                            rent_low = float(price)
                            rent_high = float(high) if high else rent_low
                            # bedrooms from name/desc
                            desc = (offer.get("name") or offer.get("description") or "").lower()
                            beds = ZillowScraper._parse_bedrooms(desc) or -1
                            out.append(FloorplanRent(
                                bedrooms=beds,
                                rent_low=rent_low, rent_high=rent_high,
                            ))
                        except (TypeError, ValueError):
                            continue
            if out:
                return out

        # HTML fallback
        rows = soup.select(
            "[class*='floorplan'], [class*='FloorPlan'], "
            "[class*='unit-card'], [class*='UnitCard']"
        )
        for row in rows:
            text = row.get_text(" ", strip=True)
            beds = ZillowScraper._parse_bedrooms(text)
            rent_low, rent_high = ZillowScraper._parse_rent_range(text)
            if rent_low is not None or rent_high is not None:
                out.append(FloorplanRent(
                    bedrooms=beds if beds is not None else -1,
                    rent_low=rent_low, rent_high=rent_high,
                ))
        return out

    @staticmethod
    def _parse_bedrooms(text: str) -> int | None:
        t = text.lower()
        if "studio" in t:
            return 0
        m = re.search(r"(\d+)\s*(?:bd|br|bed|bedroom)", t)
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
        for sel in ("[class*='amenity'] li", "[class*='Amenity'] li",
                    ".amenities li", ".features li"):
            for el in soup.select(sel):
                t = el.get_text(" ", strip=True)
                if t and len(t) < 80:
                    out.append(t)
            if out:
                break
        seen = set()
        return [a for a in out if not (a in seen or seen.add(a))][:50]

    @staticmethod
    def _extract_photos(soup, json_data: dict | None) -> list[str]:
        urls = []
        if json_data:
            imgs = json_data.get("image")
            if isinstance(imgs, str):
                urls.append(imgs)
            elif isinstance(imgs, list):
                urls.extend(str(u) for u in imgs if u)
        if not urls:
            for img in soup.select("img"):
                src = img.get("src") or img.get("data-src")
                if src and src.startswith("http") and (
                    "photo" in src or "image" in src or "media" in src
                ):
                    urls.append(src)
        return urls[:20]
