"""Workbench-native rent-listings runner (the rent-gate data source).

The scrapers (etl_listings/) are the proven hampton-roads-etl ones,
ported verbatim; this runner replaces the old repo-layout-dependent one
so the pilot host can actually run it. Scope is FAVORITES only (the
properties the owner marked - typically <20), matching the original
design: polite, bounded, and aimed at the 50-deal replay set the rent
gate measures.

Flow per favorite x source: manual URL (Properties/_favorite_listings
.json) -> last successful URL from rent_listings -> search_by_address.
Scraped rows land in rent_listings in the ETL db; the next backbone
build ingests them through the crosswalk (rent_signal.
apply_listings_rents) and they beat the FMR estimate.

Chained-cycle safe: freshness-gated (7 days) like every public pull.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from core.public_data import _stamp, is_fresh, target_db

SOURCES_DEFAULT = ("rentcafe", "zillow", "apartments_com", "property_site")
REFRESH_DAYS = 7

_ROW_COLS = (
    "property_id", "name", "address", "city", "source", "listing_url",
    "listing_name", "one_br_rent_low", "one_br_rent_high",
    "two_br_rent_low", "two_br_rent_high", "concession_text",
    "effective_one_br_rent", "effective_two_br_rent", "scrape_status",
    "error_message", "scraped_at")


def _properties_root() -> Path:
    from data.property_io import PROPERTIES_ROOT
    return Path(PROPERTIES_ROOT)


def load_favorites() -> list[str]:
    import json
    fp = _properties_root() / "_favorites.json"
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return [str(x) for x in data if x] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_manual_urls() -> dict:
    import json
    fp = _properties_root() / "_favorite_listings.json"
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def favorite_universe() -> list[dict]:
    """Favorites resolved against the legacy properties table (name +
    address drive the search step)."""
    favs = load_favorites()
    if not favs:
        return []
    from data.db import DB_PATH
    if not Path(DB_PATH).is_file():
        return []
    marks = ",".join("?" for _ in favs)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""SELECT property_id, name, address, city, state
                      FROM properties
                     WHERE property_id IN ({marks}) OR aln_id IN ({marks})
                     ORDER BY name""", tuple(favs) * 2).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _rent_bands(listing) -> dict:
    """FloorplanRent list -> 1BR/2BR low/high + concession-adjusted
    effective rents (same midpoint math as the original runner)."""
    from etl_listings.concessions import (compute_effective_rent,
                                          parse_concession_text)
    out = {k: None for k in ("one_br_rent_low", "one_br_rent_high",
                             "two_br_rent_low", "two_br_rent_high",
                             "effective_one_br_rent",
                             "effective_two_br_rent")}
    for fp in (listing.floorplans or []):
        key = {1: "one_br", 2: "two_br"}.get(fp.bedrooms)
        if not key:
            continue
        lo, hi = fp.rent_low, fp.rent_high
        if lo is not None:
            cur = out[f"{key}_rent_low"]
            out[f"{key}_rent_low"] = lo if cur is None else min(cur, lo)
        if hi is not None:
            cur = out[f"{key}_rent_high"]
            out[f"{key}_rent_high"] = hi if cur is None else max(cur, hi)
    conc = parse_concession_text(listing.concession_text or "",
                                 use_ai_fallback=False)
    for key in ("one_br", "two_br"):
        lo = out[f"{key}_rent_low"]
        hi = out[f"{key}_rent_high"]
        mid = ((lo + hi) / 2 if lo is not None and hi is not None
               else lo if lo is not None else hi)
        if mid is not None:
            out[f"effective_{key}_rent"] = compute_effective_rent(mid, conc)
    return out


def _cached_url(conn: sqlite3.Connection, pid: str, source: str) -> str | None:
    try:
        row = conn.execute(
            """SELECT listing_url FROM rent_listings
                WHERE property_id = ? AND source = ?
                  AND scrape_status = 'success' AND listing_url != ''
                ORDER BY scraped_at DESC LIMIT 1""", (pid, source)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def pull_listings(db_path: Path | None = None,
                  sources: tuple = SOURCES_DEFAULT) -> int:
    """Scrape favorites' rents into rent_listings. Returns rows written
    (0 = fresh-skip / no favorites / nothing scraped - printout says)."""
    db = db_path or target_db()
    if is_fresh(db, "rent_listings", days=REFRESH_DAYS):
        print("  [listings] fresh (pulled within "
              f"{REFRESH_DAYS} days) - skipping")
        return 0
    universe = favorite_universe()
    if not universe:
        print("  [listings] no favorites marked (Properties/_favorites"
              ".json) - star properties in the app to enable rent scraping")
        return 0
    from etl_listings.apartments_com import ApartmentsDotComScraper
    from etl_listings.property_site import PropertySiteScraper
    from etl_listings.rentcafe import RentCafeScraper
    from etl_listings.zillow import ZillowScraper
    registry = {"rentcafe": RentCafeScraper, "zillow": ZillowScraper,
                "apartments_com": ApartmentsDotComScraper,
                "property_site": PropertySiteScraper}
    manual = load_manual_urls()
    rows: list[tuple] = []
    now = dt.datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db) as conn:
        for source_id in sources:
            cls = registry.get(source_id)
            if cls is None:
                continue
            scraper = cls()
            for prop in universe:
                pid = prop["property_id"]
                base = {"property_id": pid, "name": prop.get("name"),
                        "address": prop.get("address"),
                        "city": prop.get("city"), "source": source_id,
                        "listing_url": "", "listing_name": None,
                        "one_br_rent_low": None, "one_br_rent_high": None,
                        "two_br_rent_low": None, "two_br_rent_high": None,
                        "concession_text": None,
                        "effective_one_br_rent": None,
                        "effective_two_br_rent": None,
                        "scrape_status": "not_found",
                        "error_message": None, "scraped_at": now}
                try:
                    url = (manual.get(pid, {}).get(source_id)
                           or _cached_url(conn, pid, source_id)
                           or scraper.search_by_address(
                               prop.get("name") or "",
                               prop.get("address") or "",
                               prop.get("city") or ""))
                    if url:
                        base["listing_url"] = url
                        listing = scraper.scrape_property(url)
                        if listing is not None:
                            base.update(_rent_bands(listing))
                            base["listing_name"] = listing.listing_name
                            base["concession_text"] = listing.concession_text
                            base["scrape_status"] = "success"
                except Exception as e:      # noqa: BLE001 - one bad site never kills the run
                    base["scrape_status"] = "error"
                    base["error_message"] = f"{type(e).__name__}: {e}"
                rows.append(tuple(base[c] for c in _ROW_COLS))
                print(f"  [listings] {source_id}: "
                      f"{(prop.get('name') or pid)[:34]} -> "
                      f"{base['scrape_status']}")
    if not rows:
        return 0
    with sqlite3.connect(db) as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS rent_listings
            ({', '.join(c + ' TEXT' if c in ('property_id', 'name',
                'address', 'city', 'source', 'listing_url', 'listing_name',
                'concession_text', 'scrape_status', 'error_message',
                'scraped_at') else c + ' REAL' for c in _ROW_COLS)})""")
        conn.executemany(
            f"INSERT INTO rent_listings ({', '.join(_ROW_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _ROW_COLS)})", rows)
        n_ok = sum(1 for r in rows
                   if r[_ROW_COLS.index('scrape_status')] == 'success')
        _stamp(conn, "rent_listings", "Scraped rent listings",
               "etl_listings (in-workbench)", len(rows))
    print(f"  [listings] wrote {len(rows)} rows ({n_ok} successful scrapes)")
    return len(rows)
