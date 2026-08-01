"""Generic property-site scraper.

The "I'll provide any URL" scraper. Brian points it at the property's own
website (e.g. andoverapartments.com/floor-plans) and the scraper uses
Claude to extract structured rent + concession data from arbitrary HTML.

Each Class C apartment community has its own marketing website. They're
all different (Wordpress, Squarespace, custom builds, Yardi templates,
RealPage Sightplan, etc.) so no fixed selectors work across them. Claude
handles the variability.

Cost: each scrape calls Claude once (~$0.003 per property at sonnet-4.5
pricing, or ~$0.001 at haiku). For 20 favorites × weekly = ~$0.50/week.

This is the catch-all source — typically reserved for properties not
covered by RentCafe / Zillow / Apartments.com. Brian configures the URL
in ``_favorite_listings.json`` under the ``property_site`` source key.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import ClassVar

from bs4 import BeautifulSoup

from .base import BaseListingScraper, FloorplanRent, ScrapedListing

LOG = logging.getLogger(__name__)


_EXTRACTION_PROMPT = """\
You are extracting structured data from an apartment community's website.
Return ONLY valid JSON matching this exact schema (no other text, no markdown):

{
  "name": "<property name as displayed on the page>",
  "address": "<street, city, state, zip if visible>",
  "concession_text": "<the special offer / concession banner text, exactly as shown; null if no concession>",
  "floorplans": [
    {"bedrooms": <0 for studio, 1, 2, 3>, "rent_low": <int>, "rent_high": <int>},
    ...
  ],
  "amenities": ["<amenity 1>", "<amenity 2>", ...]
}

Rules:
- bedrooms 0 = studio/efficiency
- rent_low and rent_high are monthly rent in dollars. Use the same value
  for both if only one rent is shown
- If a floorplan shows "Call for pricing" or no rent, skip it
- concession_text: capture the literal copy ("1 Month Free", "$500 off",
  "Look + Lease — $750", etc.). null if there's no concession banner.
- Maximum 6 floorplans (typical Class C has 1-4 plans).
- Maximum 20 amenities.

HTML content follows:

"""


class PropertySiteScraper(BaseListingScraper):
    SOURCE_ID: ClassVar[str] = "property_site"
    DISPLAY_NAME: ClassVar[str] = "Property's own website (AI-extracted)"
    BASE_URL: ClassVar[str] = ""
    DELAY_SECONDS: ClassVar[float] = 2.0
    RESPECT_ROBOTS: ClassVar[bool] = True

    # How much HTML to send to Claude. Most property pages are 200-800 KB;
    # we strip nav/footer first, then truncate.
    _MAX_HTML_CHARS: ClassVar[int] = 30_000

    def search_by_address(self, name: str, address: str, city: str) -> str | None:
        # No search — Brian provides the URL directly per property.
        return None

    def scrape_property(self, listing_url: str) -> ScrapedListing | None:
        r = self.get(listing_url)
        if r is None or r.status_code != 200:
            return None
        return self._parse(r.text, listing_url)

    def _parse(self, html: str, listing_url: str) -> ScrapedListing | None:
        # Strip script/style/nav/footer/aside to focus Claude on content
        cleaned = self._reduce_html(html)

        # Call Claude to extract structured data
        data = self._extract_via_claude(cleaned)
        if data is None:
            return None

        floorplans: list[FloorplanRent] = []
        for fp in data.get("floorplans") or []:
            try:
                beds = int(fp.get("bedrooms", -1))
                rent_low = float(fp.get("rent_low") or 0) or None
                rent_high = float(fp.get("rent_high") or 0) or None
                if rent_low is None and rent_high is None:
                    continue
                floorplans.append(FloorplanRent(
                    bedrooms=beds, rent_low=rent_low, rent_high=rent_high,
                ))
            except (TypeError, ValueError):
                continue

        return ScrapedListing(
            source=self.SOURCE_ID,
            listing_url=listing_url,
            listing_name=data.get("name"),
            listing_address=data.get("address"),
            floorplans=floorplans,
            concession_text=data.get("concession_text"),
            amenities=list(data.get("amenities") or [])[:50],
            photo_urls=[],   # we don't extract photos from generic sites
        )

    @staticmethod
    def _reduce_html(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "header",
                          "footer", "aside", "iframe", "svg"]):
            tag.decompose()
        # Get reasonably-tight HTML (drops whitespace)
        text = soup.prettify()
        return text[:PropertySiteScraper._MAX_HTML_CHARS]

    @staticmethod
    def _extract_via_claude(cleaned_html: str) -> dict | None:
        """Use Claude to extract structured data. Returns None on any error."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            LOG.warning("ANTHROPIC_API_KEY not set — property_site scraper inactive")
            return None
        try:
            import anthropic
        except ImportError:
            LOG.warning("anthropic package not available — property_site scraper inactive")
            return None

        # AC-11.2: an org with ai_enabled off must reach no model at all.
        # Placed on the line that BUILDS the client, so a new surface
        # cannot forget the check and still get one.
        from core import ai_gate
        ai_gate.require_ai(
            'Listing page parsing',
            'Other listing sources still run; only this LLM-assisted one is skipped.',
            ai_gate.current_org_id())
        client = anthropic.Anthropic(api_key=api_key)
        try:
            msg = client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=2000,
                messages=[{"role": "user", "content": _EXTRACTION_PROMPT + cleaned_html}],
            )
            content = msg.content[0].text if msg.content else ""
            # Strip any code fence wrappers Claude might add
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
            data = json.loads(content)
            return data if isinstance(data, dict) else None
        except Exception as e:
            LOG.warning("property_site Claude extraction failed: %s", e)
            return None
