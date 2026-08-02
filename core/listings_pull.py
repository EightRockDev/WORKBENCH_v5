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

# Bump whenever a change alters what a pull YIELDS - a scraper fix, a new
# source, a change to how ids are normalized.
#
# The freshness stamp answers "was something pulled recently, over this same
# favourite set". It did not answer "was it pulled by THIS code", and that gap
# is why the 2026-08-01 favourites-key fix sat unused: the pull that ran right
# after it stamped itself fresh, and every cycle since has skipped, so the
# rent gate stayed at 1 of 18,928 with nothing in the report to say why. A
# code fix that cannot run is indistinguishable from no fix.
PULL_GENERATION = 2

_ROW_COLS = (
    "property_id", "name", "address", "city", "source", "listing_url",
    "listing_name", "one_br_rent_low", "one_br_rent_high",
    "two_br_rent_low", "two_br_rent_high", "concession_text",
    "effective_one_br_rent", "effective_two_br_rent", "scrape_status",
    "error_message", "scraped_at")


_TEXT_COLS = frozenset({
    "property_id", "name", "address", "city", "source", "listing_url",
    "listing_name", "concession_text", "scrape_status", "error_message",
    "scraped_at"})


def _add_missing_columns(conn, table: str) -> list[str]:
    """Bring an EXISTING rent_listings up to the current column set.

    `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists,
    so every column added to `_ROW_COLS` since a machine first ran the scraper
    is simply absent there, and the INSERT dies with "table rent_listings has
    no column named <x>". That is what killed the 2026-08-01 listings step —
    losing two successful Zillow scrapes, the one data source that moves the
    rent-delta gate.

    ALTER TABLE ADD COLUMN is cheap and idempotent, so reconcile on every run
    rather than relying on anyone to notice a schema bump.
    """
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not have:
        return []
    added = []
    for col in _ROW_COLS:
        if col not in have:
            kind = "TEXT" if col in _TEXT_COLS else "REAL"
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            added.append(col)
    if added:
        print(f"  [listings] schema: added missing column(s) "
              f"{', '.join(added)} to {table}")
    return added


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
    """Favorites resolved against the property table (name + address drive
    the search step).

    Matching is NORMALIZED, not exact. Synthesized ids look like
    `<slug>-<numeric id>` and the slug changed in the Phase-0
    de-identification, so `_favorites.json` can hold `aln-134263` for a row
    now keyed `legacy-134263`. `property_io` already normalizes that, which is
    why the UI still shows those properties starred - but this query did not,
    so the scraper silently skipped every favorite saved by an older build.
    A star that does not produce a rent scrape is worse than no star: it looks
    like the source was tried and found nothing.
    """
    favs = load_favorites()
    if not favs:
        return []
    from data.db import DB_PATH
    from data.property_io import _fav_key
    if not Path(DB_PATH).is_file():
        return []
    wanted = {_fav_key(str(f)) for f in favs if str(f).strip()}
    if not wanted:
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT property_id, legacy_id, name, address, city, state
                     FROM properties ORDER BY name""").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        keys = {_fav_key(str(r[k] or "")) for k in ("property_id", "legacy_id")
                if r[k]}
        if keys & wanted:
            d = dict(r)
            d.pop("legacy_id", None)
            out.append(d)
    return out


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


def _favorites_fingerprint(universe: list[dict]) -> str:
    """A stable hash of which properties are due to be scraped, BY WHICH CODE.

    Folding `PULL_GENERATION` in makes a scraper change invalidate the stamp
    the same way starring a property does. Both are the same statement: the
    last pull is not a repeat of the one now due.
    """
    import hashlib
    ids = sorted(str(p.get("property_id") or "") for p in universe)
    payload = f"gen={PULL_GENERATION}|" + "|".join(ids)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _listing_rows(db) -> int:
    """How many rent rows the skip is claiming are good enough."""
    import sqlite3
    try:
        with sqlite3.connect(db) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM rent_listings").fetchone()[0])
    except sqlite3.Error:
        return 0


def _fingerprint_unchanged(db, fingerprint: str) -> bool:
    """True when the last successful pull covered this same favourite set.

    Stored in `etl_metadata.description` rather than a new column so this
    needs no migration on the machines already running: the column is free
    text and nothing parses it.
    """
    import sqlite3
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT description FROM etl_metadata WHERE table_name = ?",
                ("rent_listings",)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0] and f"favset={fingerprint}" in str(row[0]))


def invalidate_freshness(db_path=None) -> None:
    """Drop the "already pulled" claim after a failed attempt.

    Without this a crash is STICKY: the stamp from the last successful pull
    keeps the step skipping for the rest of the freshness window, so the
    failure is invisible and any fix shipped in the meantime cannot run. That
    is exactly what happened after the 2026-08-01 schema crash — the fix
    landed and then sat unused while every cycle reported "fresh - skipping".
    """
    import sqlite3
    db = db_path or target_db()
    try:
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE etl_metadata SET description = ? "
                " WHERE table_name = ?",
                ("Scraped rent listings; last attempt FAILED - retry due",
                 "rent_listings"))
            conn.commit()
    except Exception:
        pass


def _record_fingerprint(conn, fingerprint: str) -> None:
    try:
        conn.execute(
            "UPDATE etl_metadata SET description = ? WHERE table_name = ?",
            (f"Scraped rent listings; favset={fingerprint}", "rent_listings"))
    except Exception:
        pass


def pull_listings(db_path: Path | None = None,
                  sources: tuple = SOURCES_DEFAULT) -> int:
    """Scrape favorites' rents into rent_listings. Returns rows written
    (0 = fresh-skip / no favorites / nothing scraped - printout says)."""
    db = db_path or target_db()
    universe = favorite_universe()

    # Freshness is gated on the INPUT SET as well as the clock. Starring a
    # property is an instruction to scrape it; waiting up to 7 days to honour
    # that makes the feature look broken, and the rent gate cannot move
    # without new rows. If the favourites have changed since the last
    # successful pull, this is not a repeat of that pull.
    fingerprint = _favorites_fingerprint(universe)
    if (is_fresh(db, "rent_listings", days=REFRESH_DAYS)
            and _fingerprint_unchanged(db, fingerprint)):
        # Say what the skip is protecting. "fresh - skipping" reads as health
        # whether the last pull produced 18,000 rows or one, and for a month
        # it was one.
        print(f"  [listings] fresh (pulled within {REFRESH_DAYS} days, "
              f"{len(universe)} favourites, generation {PULL_GENERATION}) "
              f"- skipping; {_listing_rows(db):,} rent_listings rows on hand")
        return 0
    if is_fresh(db, "rent_listings", days=REFRESH_DAYS):
        print("  [listings] favourites or scraper generation changed since "
              "the last pull - re-scraping despite freshness")
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
            ({', '.join(c + ' TEXT' if c in _TEXT_COLS else c + ' REAL'
                        for c in _ROW_COLS)})""")
        _add_missing_columns(conn, "rent_listings")
        conn.executemany(
            f"INSERT INTO rent_listings ({', '.join(_ROW_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _ROW_COLS)})", rows)
        n_ok = sum(1 for r in rows
                   if r[_ROW_COLS.index('scrape_status')] == 'success')
        _stamp(conn, "rent_listings", "Scraped rent listings",
               "etl_listings (in-workbench)", len(rows))
        # Tie the stamp to the favourite set it covered, so adding a star
        # invalidates it rather than waiting out the 7 days.
        _record_fingerprint(conn, fingerprint)
    print(f"  [listings] wrote {len(rows)} rows ({n_ok} successful scrapes)")
    return len(rows)
