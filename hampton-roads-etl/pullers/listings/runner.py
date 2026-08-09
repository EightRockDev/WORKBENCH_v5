"""Main entry point: iterate ALN universe → scrape per source → return DataFrame.

Called from ``hampton_roads_etl.py`` via the ``listings`` source registry
entry. Returns a single DataFrame written to the ``rent_listings`` table.

Polite behavior:
  - One source at a time (no parallel hammering of the same host)
  - Per-source DELAY_SECONDS between requests
  - URL cache: subsequent runs skip the search step if the listing URL is
    already known (saves time + scrape budget). Cache stored in
    `rent_listings.listing_url` from the prior run.
  - Status tracking: every property gets a row, even on failure, with
    ``scrape_status`` ∈ {"success", "not_found", "blocked", "error"}.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .apartments_com import ApartmentsDotComScraper
from .base import BaseListingScraper, ScrapedListing
from .concessions import ParsedConcession, compute_effective_rent, parse_concession_text
from .property_site import PropertySiteScraper
from .rentcafe import RentCafeScraper
from .zillow import ZillowScraper


def _default_db_path() -> Path:
    """Lazy-import DB_PATH from the hampton-roads-etl `config` module.

    Done at call time (not module-import time) so this package can be
    imported from the workbench's tests/ directory without ETL's `config`
    being on the path, AND so it doesn't collide with the workbench's own
    `config.py` (no DB_PATH there).
    """
    try:
        # Use absolute import via the directory path
        import importlib.util
        etl_config_path = (
            Path(__file__).resolve().parents[2] / "config.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_etl_config", etl_config_path,
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.DB_PATH
    except Exception:
        pass
    # Fallback: assume DB is next to this file's grandparent dir
    return Path(__file__).resolve().parents[2] / "hampton_roads.db"

LOG = logging.getLogger(__name__)


# Source registry — fully implemented in W2.
SOURCES: dict[str, type[BaseListingScraper]] = {
    ApartmentsDotComScraper.SOURCE_ID: ApartmentsDotComScraper,
    RentCafeScraper.SOURCE_ID:         RentCafeScraper,
    ZillowScraper.SOURCE_ID:           ZillowScraper,
    PropertySiteScraper.SOURCE_ID:     PropertySiteScraper,
}


# ---------------------------------------------------------------------------
# ALN universe selection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Favorites + manual URL config
# ---------------------------------------------------------------------------

def _workbench_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _favorites_path() -> Path:
    return _workbench_root() / "Properties" / "_favorites.json"


def _favorite_listings_path() -> Path:
    """Where Brian provides manual listing URLs per favorite.

    Schema::
        {
          "<property_id_or_aln_id>": {
            "apartments_com": "https://www.apartments.com/green-tree-chesapeake-va/...",
            "zillow":         "https://www.zillow.com/...",
            "rentcafe":       "https://www.rentcafe.com/...",
            "property_site":  "https://greentreeapts.com/"
          },
          ...
        }

    If a favorite has an entry here, the scraper SKIPS the (often-blocked)
    search step and goes straight to the URL. If no entry, the scraper tries
    `search_by_address` (which may or may not work depending on the source's
    bot detection).
    """
    return _workbench_root() / "Properties" / "_favorite_listings.json"


def _load_favorites() -> set[str]:
    """Read the workbench's favorites file (property IDs)."""
    import json
    path = _favorites_path()
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw}


def load_favorite_listings() -> dict[str, dict[str, str]]:
    """Read `Properties/_favorite_listings.json` (property_id → source → URL).

    Returns an empty dict if the file doesn't exist yet — that's the normal
    first-run state. Brian populates it manually as he identifies each
    favorite's best listing URL.
    """
    import json
    path = _favorite_listings_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _aln_universe(scope: str) -> list[dict[str, Any]]:
    """Return ALN properties to scrape, scoped by ``scope`` token.

    Scopes:
      "favorites"    — ONLY Brian's marked favorites (Properties/_favorites.json).
                       This is the default. Tiny universe (typically <20),
                       lets Brian provide manual listing URLs per property
                       via _favorite_listings.json so we skip search steps.
      "hr_class_c"   — Hampton Roads Class C, 20-400 units (legacy/broader)
      "hr_all"       — All Hampton Roads multifamily 5+ units
      "va_class_c"   — All Virginia Class C, 20-400 units
      "va_all"       — All Virginia multifamily 5+ units

    Pulls from the workbench's properties table (ALN snapshot).
    """
    wb_db = _workbench_root() / "python_workbench" / "data" / "workbench.db"
    if not wb_db.is_file():
        LOG.error("workbench.db not found at %s", wb_db)
        return []

    if scope == "favorites":
        favs = _load_favorites()
        if not favs:
            LOG.warning("No favorites marked in Properties/_favorites.json")
            return []
        # property_id OR aln_id may match (legacy favorites use ALN numeric ids)
        placeholders = ",".join("?" * len(favs))
        with sqlite3.connect(wb_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT property_id, name, address, city, state, units "
                f"FROM properties "
                f"WHERE property_id IN ({placeholders}) "
                f"   OR aln_id IN ({placeholders}) "
                f"ORDER BY name",
                tuple(favs) * 2,
            ).fetchall()
        return [dict(r) for r in rows]

    if scope == "hr_class_c":
        where = (
            "city IN ('Norfolk','Virginia Beach','Chesapeake','Hampton',"
            "'Newport News','Portsmouth','Suffolk') "
            "AND asset_class='C' AND units BETWEEN 20 AND 400"
        )
    elif scope == "hr_all":
        where = (
            "city IN ('Norfolk','Virginia Beach','Chesapeake','Hampton',"
            "'Newport News','Portsmouth','Suffolk') AND units >= 5"
        )
    elif scope == "va_class_c":
        where = "state='VA' AND asset_class='C' AND units BETWEEN 20 AND 400"
    elif scope == "va_all":
        where = "state='VA' AND units >= 5"
    else:
        raise ValueError(f"Unknown scope {scope!r}")

    with sqlite3.connect(wb_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT property_id, name, address, city, state, units "
            f"FROM properties WHERE {where} ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# URL cache (uses last-pull's rent_listings.listing_url)
# ---------------------------------------------------------------------------

def _url_cache(source_id: str, db_path: Path | None = None) -> dict[str, str]:
    """Map property_id → cached listing_url for this source.

    Skips the search step on subsequent runs.
    """
    if db_path is None:
        db_path = _default_db_path()
    if not db_path.is_file():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT property_id, listing_url FROM rent_listings "
                "WHERE source = ? AND listing_url IS NOT NULL AND listing_url != ''",
                (source_id,),
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Per-property scrape
# ---------------------------------------------------------------------------

def _scrape_one(
    scraper: BaseListingScraper,
    aln: dict[str, Any],
    cached_url: str | None,
    manual_url: str | None = None,
) -> dict[str, Any]:
    """Scrape one property → returns a row ready for DataFrame insertion."""
    base_row: dict[str, Any] = {
        "property_id": aln["property_id"],
        "aln_name": aln["name"],
        "aln_address": aln["address"],
        "aln_city": aln["city"],
        "aln_units": aln["units"],
        "source": scraper.SOURCE_ID,
        "listing_url": None,
        "listing_name": None,
        "listing_address": None,
        "studio_rent_low": None, "studio_rent_high": None,
        "one_br_rent_low": None, "one_br_rent_high": None,
        "two_br_rent_low": None, "two_br_rent_high": None,
        "three_br_rent_low": None, "three_br_rent_high": None,
        "concession_text": None,
        "concession_months_free": 0.0,
        "concession_dollar_off": 0.0,
        "concession_confidence": "none",
        "effective_one_br_rent": None,
        "effective_two_br_rent": None,
        "amenities_count": 0,
        "photo_count": 0,
        "scrape_status": "pending",
        "error_message": None,
        "scraped_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    try:
        # 1. URL resolution priority:
        #    a) manual_url (Brian provided in _favorite_listings.json) — best
        #    b) cached_url (previous successful scrape's URL)
        #    c) live search (which may be blocked by bot detection)
        url = manual_url or cached_url
        if not url:
            url = scraper.search_by_address(aln["name"], aln["address"], aln["city"])
            if not url:
                base_row["scrape_status"] = "not_found"
                return base_row
        base_row["listing_url"] = url

        # 2. Scrape the property page
        listing = scraper.scrape_property(url)
        if listing is None:
            base_row["scrape_status"] = "blocked"
            base_row["error_message"] = "scraper returned None"
            return base_row

        base_row["listing_name"] = listing.listing_name
        base_row["listing_address"] = listing.listing_address
        base_row["amenities_count"] = len(listing.amenities)
        base_row["photo_count"] = len(listing.photo_urls)

        # 3. Floorplans → typed columns
        for fp in listing.floorplans:
            if fp.bedrooms == 0:
                base_row["studio_rent_low"] = fp.rent_low
                base_row["studio_rent_high"] = fp.rent_high
            elif fp.bedrooms == 1:
                base_row["one_br_rent_low"] = fp.rent_low
                base_row["one_br_rent_high"] = fp.rent_high
            elif fp.bedrooms == 2:
                base_row["two_br_rent_low"] = fp.rent_low
                base_row["two_br_rent_high"] = fp.rent_high
            elif fp.bedrooms == 3:
                base_row["three_br_rent_low"] = fp.rent_low
                base_row["three_br_rent_high"] = fp.rent_high

        # 4. Concession parsing
        concession = parse_concession_text(listing.concession_text)
        base_row["concession_text"] = listing.concession_text
        base_row["concession_months_free"] = concession.months_free
        base_row["concession_dollar_off"] = concession.dollar_off
        base_row["concession_confidence"] = concession.confidence

        # 5. Effective rents (per common floorplan — use midpoint of range)
        def _mid(low: float | None, high: float | None) -> float | None:
            if low is None and high is None:
                return None
            if low is None:
                return high
            if high is None:
                return low
            return (low + high) / 2.0

        one_br_mid = _mid(base_row["one_br_rent_low"], base_row["one_br_rent_high"])
        two_br_mid = _mid(base_row["two_br_rent_low"], base_row["two_br_rent_high"])
        if one_br_mid is not None:
            base_row["effective_one_br_rent"] = compute_effective_rent(one_br_mid, concession)
        if two_br_mid is not None:
            base_row["effective_two_br_rent"] = compute_effective_rent(two_br_mid, concession)

        base_row["scrape_status"] = "success"
        return base_row

    except Exception as e:
        LOG.exception("scrape failed for %s on %s", aln["name"], scraper.SOURCE_ID)
        base_row["scrape_status"] = "error"
        base_row["error_message"] = f"{type(e).__name__}: {e}"
        return base_row


# ---------------------------------------------------------------------------
# Public entry point — called by hampton_roads_etl.py
# ---------------------------------------------------------------------------

def pull_listings(
    scope: str = "favorites",
    sources: Iterable[str] = ("rentcafe", "zillow", "apartments_com", "property_site"),
    db_path: Path | None = None,
) -> pd.DataFrame:
    """Scrape rent listings for the configured scope + sources.

    Default scope is ``favorites`` — only properties Brian has marked as
    favorites in the workbench (typically <20). For each favorite, the
    runner checks ``Properties/_favorite_listings.json`` for a manual URL
    keyed by (property_id, source) and uses that directly, skipping the
    (frequently bot-blocked) search step. If no manual URL is provided for
    a given source, the runner falls back to ``search_by_address``.

    Returns a single DataFrame with one row per (property, source). Each
    row carries either a successful scrape or a status code (not_found /
    blocked / error).
    """
    if db_path is None:
        db_path = _default_db_path()
    aln_rows = _aln_universe(scope)
    manual_urls = load_favorite_listings()
    LOG.info(
        "Pulling listings: scope=%s, %d properties, sources=%s, %d manual URLs configured",
        scope, len(aln_rows), list(sources), sum(len(v) for v in manual_urls.values()),
    )

    all_rows: list[dict[str, Any]] = []
    for source_id in sources:
        scraper_cls = SOURCES.get(source_id)
        if scraper_cls is None:
            LOG.warning("Unknown source %r; skipping", source_id)
            continue
        scraper = scraper_cls()
        url_cache = _url_cache(source_id, db_path)
        for i, aln in enumerate(aln_rows, start=1):
            pid = aln["property_id"]
            manual_url = manual_urls.get(pid, {}).get(source_id)
            cached_url = url_cache.get(pid)
            row = _scrape_one(scraper, aln, cached_url, manual_url=manual_url)
            all_rows.append(row)
            LOG.info(
                "  [%s] %d/%d %s → %s (url: %s)",
                source_id, i, len(aln_rows),
                aln["name"][:30], row["scrape_status"],
                "manual" if manual_url else ("cached" if cached_url else "searched"),
            )

    return pd.DataFrame(all_rows)
