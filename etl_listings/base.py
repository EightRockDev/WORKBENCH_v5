"""Base scraper protocol + polite HTTP client.

Every concrete scraper (Apartments.com, Zillow, Rent.com, ...) subclasses
``BaseListingScraper`` and implements two methods:

  - ``search_by_address(name, address, city)`` → URL or None
  - ``scrape_property(listing_url)`` → ScrapedListing or None

The framework handles:
  - Polite HTTP (configurable delay between requests, default 3s)
  - User-Agent rotation
  - Retry on 429/503 with exponential backoff
  - Robots.txt respect (cached per-domain)
  - Optional Playwright fallback for JS-heavy pages (Cloudflare-protected)
"""

from __future__ import annotations

import dataclasses
import logging
import random
import time
import urllib.parse
import urllib.robotparser
from typing import ClassVar, Protocol

import requests

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scraped-listing payload (returned by `scrape_property`)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class FloorplanRent:
    """One floorplan's rent range (low/high). Either side may be None."""
    bedrooms: int                  # 0 = studio, 1 = 1br, etc.
    rent_low: float | None
    rent_high: float | None
    available_units: int | None = None


@dataclasses.dataclass
class UnitAvailability:
    """One row of a listing's "Available units" table (owner ask 2026-08-03).

    A live unit board is real underwriting signal a rent RANGE hides: how many
    units are actually on the market right now, the true bed/bath mix, per-
    unit asking rent, when each turns, and which carry a concession. Every
    field is optional - sites publish different columns."""
    unit: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    sqft: int | None = None
    available: str | None = None       # "Now", a date string, or None
    base_rent: float | None = None
    special_offer: bool = False


@dataclasses.dataclass
class ScrapedListing:
    """The raw data each scraper returns. Persisted as a single row in
    ``rent_listings`` after concession parsing + effective rent calc."""

    source: str                                  # "apartments_com"
    listing_url: str
    listing_name: str | None
    listing_address: str | None
    floorplans: list[FloorplanRent] = dataclasses.field(default_factory=list)
    units: list[UnitAvailability] = dataclasses.field(default_factory=list)
    concession_text: str | None = None           # raw banner copy
    amenities: list[str] = dataclasses.field(default_factory=list)
    photo_urls: list[str] = dataclasses.field(default_factory=list)
    qualityscore_hint: float | None = None       # filled in later by Claude
    raw_html_path: str | None = None             # optional debug dump


# ---------------------------------------------------------------------------
# Base scraper
# ---------------------------------------------------------------------------

# Rotated User-Agents. Recent Chrome on Win/Mac strings — common enough to
# blend in without being deceptive (we identify the project in the request
# headers below).
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)


class BaseListingScraper:
    """Subclass + override ``search_by_address`` and ``scrape_property``."""

    SOURCE_ID: ClassVar[str] = "base"
    DISPLAY_NAME: ClassVar[str] = "Base"
    BASE_URL: ClassVar[str] = ""
    DELAY_SECONDS: ClassVar[float] = 3.0      # polite throttle between calls
    RETRY_BUDGET: ClassVar[int] = 3
    TIMEOUT_SECONDS: ClassVar[float] = 30.0
    RESPECT_ROBOTS: ClassVar[bool] = True

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            # Identify the research project. Many sites are more permissive
            # when the UA names the operator + provides contact info.
            "From": "research@eightrockcp.com",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        })
        self._last_request_at: float = 0.0
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # ---- HTTP layer ----
    def _rotate_user_agent(self) -> None:
        self.session.headers["User-Agent"] = random.choice(_USER_AGENTS)

    def _respect_throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.DELAY_SECONDS:
            time.sleep(self.DELAY_SECONDS - elapsed + random.uniform(0.1, 0.6))
        self._last_request_at = time.time()

    def _check_robots(self, url: str) -> bool:
        """Return True if the URL is fetchable per the host's robots.txt."""
        if not self.RESPECT_ROBOTS:
            return True
        parsed = urllib.parse.urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        if host_key not in self._robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{host_key}/robots.txt")
            try:
                rp.read()
                self._robots_cache[host_key] = rp
            except Exception:
                # If robots.txt is unreachable, default to permissive — most
                # large hosts intentionally serve a 200 anyway. We log so a
                # human can review.
                LOG.warning("robots.txt unreachable for %s; defaulting to allow", host_key)
                self._robots_cache[host_key] = None
        rp = self._robots_cache[host_key]
        if rp is None:
            return True
        return rp.can_fetch(self.session.headers.get("User-Agent", "*"), url)

    def get(self, url: str, **kwargs) -> requests.Response | None:
        """Polite GET with throttle + UA rotation + retry on 429/503.

        Returns None if blocked by robots.txt or after exhausting retries.
        Callers receive the Response object on success — they decide how to
        parse it (HTML, JSON, etc.).
        """
        if not self._check_robots(url):
            LOG.info("robots.txt disallows %s", url)
            return None

        for attempt in range(self.RETRY_BUDGET):
            self._respect_throttle()
            self._rotate_user_agent()
            try:
                r = self.session.get(url, timeout=self.TIMEOUT_SECONDS, **kwargs)
            except requests.RequestException as e:
                LOG.warning("GET %s failed (attempt %d): %s", url, attempt + 1, e)
                time.sleep(2 ** attempt)
                continue

            if r.status_code in (429, 503):
                wait = int(r.headers.get("Retry-After", str(2 ** (attempt + 2))))
                LOG.info("Rate-limited (%d) on %s; waiting %ds", r.status_code, url, wait)
                time.sleep(min(wait, 60))
                continue

            if r.status_code == 404:
                return r  # caller handles "not found" explicitly

            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue

            return r

        return None

    # ---- Subclass contract ----
    def search_by_address(
        self, name: str, address: str, city: str,
    ) -> str | None:
        """Find the property's listing URL on this source. None if not found."""
        raise NotImplementedError

    def scrape_property(self, listing_url: str) -> ScrapedListing | None:
        """Pull rent + concession data from a property's listing page."""
        raise NotImplementedError
