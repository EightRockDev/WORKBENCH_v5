"""Per-property listing-URLs panel.

Renders on the Performance & Market tab. Lets Brian paste a listing URL
for the current property without opening _favorite_listings.json by hand,
then triggers an on-demand scrape for that property+source.

Storage: writes to ``Properties/_favorite_listings.json`` (same file the
Monday cron reads). UI mirrors what's there.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

import config
from ui.components import section_card


_WB_ROOT = Path(__file__).resolve().parent.parent.parent
_LISTINGS_PATH = _WB_ROOT / "Properties" / "_favorite_listings.json"

def _listings_db() -> Path:
    """The ETL db via the ONE resolver - the old hard-coded sibling path
    (hampton-roads-etl/) left this panel empty on the pilot host even
    with the db present at data/hampton_roads.db."""
    from core.etl_db import preferred_location, resolve_etl_db
    return resolve_etl_db() or preferred_location()

# Sources the scraper SUPPORTS (actual scrapers in hampton-roads-etl/pullers/listings/runner.py)
_SCRAPER_SOURCES = ("rentcafe", "zillow", "apartments_com", "property_site")

# Sources DISPLAYED in the URL panel (Brian 2026-05-29 v2.0.10).
# Order: scrapeable sources first, then display-only sources (saved + linkable
# but not scraped — clicking them in Latest Scrape Results opens the listing).
# When Brian saves a URL for a display-only source it persists to
# _favorite_listings.json but the scraper skips it. He can use these as
# bookmarks for the property's marketing presence across the web.
_DISPLAY_SOURCES = (
    # Scrapeable
    "rentcafe",
    "zillow",
    "apartments_com",
    "property_site",
    # Display-only (no scraper — kept as quick-jump links)
    "apartmentlist",
    "apartmentguide",
    "rent_com",
    "trulia",
    "hotpads",
    "zumper",
    "realtor_com",
    "forrent",
    "padmapper",
    "costar",
    "loopnet",
    "craigslist",
    "facebook_marketplace",
)

# Backward-compat alias — old code referenced _SOURCES
_SOURCES = _DISPLAY_SOURCES


def render_listing_urls_panel(prop: dict[str, Any]) -> None:
    """Section card for managing this property's listing URLs."""
    if not prop or not prop.get("property_id"):
        return

    property_id = str(prop["property_id"])
    legacy_id = str(prop.get("legacy_id") or "")
    config_dict = _load_config()
    # Keys can be property_id OR legacy_id (legacy favorites). Try both.
    cur = config_dict.get(property_id) or config_dict.get(legacy_id) or {}
    effective_key = property_id if property_id in config_dict else (
        legacy_id if legacy_id in config_dict else property_id
    )

    c = config.COLORS

    with section_card(
        "Rent Listing URLs",
        icon="🔗",
        accent="ac",
        subtitle=None,
    ):
        # ---- Property Marketing Sites heading (Brian 5/29 v2.0.10) ----
        st.markdown(
            f'<div style="font-size:13px;font-weight:700;color:{c["tx"]};'
            f'margin-top:2px;margin-bottom:8px;letter-spacing:-0.005em">'
            f'Property Marketing Sites</div>'
            f'<div style="font-size:11px;color:{c["tx3"]};margin-bottom:10px">'
            f'Paste the property listing URL from any site below. Saved URLs '
            f'become clickable jump-points in the Latest Scrape Results.</div>',
            unsafe_allow_html=True,
        )

        # ---- Existing URLs ----
        any_url = any(cur.get(s) for s in _SOURCES)
        if any_url:
            st.markdown(
                f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
                f'letter-spacing:0.7px;font-weight:600;margin-bottom:6px">'
                f'Configured for this property</div>',
                unsafe_allow_html=True,
            )
            for source in _SOURCES:
                url = cur.get(source) or ""
                if url:
                    _render_url_row(effective_key, source, url, cur)
        else:
            st.caption("No URLs configured yet — paste one below.")

        # ---- Add new URL ----
        with st.form(f"add_listing_url_{property_id}", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                new_url = st.text_input(
                    "URL",
                )
            with col2:
                new_source = st.selectbox(
                    "Source",
                    options=list(_SOURCES),
                    format_func=_pretty_source,
                )
            submitted = st.form_submit_button("Save URL", type="primary")
            if submitted and new_url.strip():
                clean_url = _clean_url(new_url.strip())
                cur[new_source] = clean_url
                cur.setdefault("_property_name", prop.get("name", ""))
                config_dict[effective_key] = cur
                _save_config(config_dict)
                st.success(f"Saved {_pretty_source(new_source)} URL.")
                st.rerun()

        # ---- Scrape now (for this property only) ----
        st.divider()
        col_a, col_b = st.columns([2, 4])
        with col_a:
            if st.button(
                "🔄 Scrape this property now",
                key=f"scrape_now_{property_id}",
                help="Runs the scraper just for this property (all configured sources). ~10-30 sec.",
                disabled=not any_url,
            ):
                with st.spinner("Scraping..."):
                    n = _scrape_one_property(property_id, legacy_id, prop)
                if n > 0:
                    st.success(f"Scrape complete — {n} source(s) scraped.")
                    st.rerun()
                else:
                    st.warning("No data returned. Check the URL is correct.")

        # ---- Latest scrape result for this property ----
        _render_latest_scrape(property_id, legacy_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pretty_source(s: str) -> str:
    return {
        # Scrapeable
        "rentcafe":             "RentCafe (Yardi)",
        "zillow":                "Zillow Rentals",
        "apartments_com":        "Apartments.com",
        "property_site":         "Property's own site",
        # Display-only quick-jump links (Brian 5/29 v2.0.10)
        "apartmentlist":         "Apartment List",
        "apartmentguide":        "Apartment Guide",
        "rent_com":              "Rent.com",
        "trulia":                "Trulia Rentals",
        "hotpads":               "HotPads",
        "zumper":                "Zumper",
        "realtor_com":           "Realtor.com Rentals",
        "forrent":               "ForRent.com",
        "padmapper":             "Padmapper",
        "costar":                "CoStar",
        "loopnet":               "LoopNet",
        "craigslist":            "Craigslist",
        "facebook_marketplace":  "Facebook Marketplace",
    }.get(s, s)


def _clean_url(url: str) -> str:
    """Strip Google's url redirect wrapper if present."""
    import urllib.parse
    if "google.com/url" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "url" in qs:
                return qs["url"][0]
        except Exception:
            pass
    return url


def _load_config() -> dict:
    if not _LISTINGS_PATH.is_file():
        return {}
    try:
        raw = json.loads(_LISTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        # Strip _comment / _example_* meta keys
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except json.JSONDecodeError:
        return {}


def _save_config(data: dict) -> None:
    """Atomic write — preserves _comment / _example_* keys if any exist on disk."""
    existing: dict = {}
    if _LISTINGS_PATH.is_file():
        try:
            existing = json.loads(_LISTINGS_PATH.read_text(encoding="utf-8")) or {}
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            existing = {}

    # Merge: keep _comment + _example_*, overwrite real entries from `data`
    merged = {k: v for k, v in existing.items() if k.startswith("_")}
    merged.update(data)

    _LISTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(_LISTINGS_PATH.parent),
        delete=False, suffix=".tmp",
    ) as tmp:
        json.dump(merged, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_LISTINGS_PATH)


def _render_url_row(prop_key: str, source: str, url: str, cur: dict) -> None:
    c = config.COLORS
    col_label, col_url, col_action = st.columns([2, 5, 1])
    with col_label:
        st.markdown(
            f'<div style="padding:6px 0;font-size:12px;font-weight:600;color:{c["tx2"]}">'
            f'{_pretty_source(source)}</div>',
            unsafe_allow_html=True,
        )
    with col_url:
        st.markdown(
            f'<div style="padding:6px 0;font-size:12px;color:{c["bl"]};'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'<a href="{url}" target="_blank" style="color:{c["bl"]}">{url}</a></div>',
            unsafe_allow_html=True,
        )
    with col_action:
        if st.button("🗑", key=f"del_{prop_key}_{source}", help="Remove this URL"):
            del cur[source]
            cfg = _load_config()
            cfg[prop_key] = cur
            _save_config(cfg)
            st.rerun()


def _render_latest_scrape(property_id: str, legacy_id: str) -> None:
    """Show the most-recent rent_listings rows for this property."""
    c = config.COLORS
    if not _listings_db().is_file():
        return
    try:
        with sqlite3.connect(_listings_db()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * "
                "FROM rent_listings "
                "WHERE property_id IN (?, ?) "
                "ORDER BY scraped_at DESC LIMIT 4",
                (property_id, legacy_id or property_id),
            ).fetchall()
    except sqlite3.Error:
        return

    if not rows:
        return

    # Latest scrape results — small inline squares (Brian 5/29 v2.0.9), each
    # CLICKABLE as a quick-jump to the listing URL when one's stored
    # (Brian 5/29 v2.0.11). When a square has no listing_url (e.g., not_found
    # results), it renders as a non-link div so the cursor doesn't lie.
    squares_html = (
        f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600;margin-top:14px;margin-bottom:6px">'
        f'Latest scrape results</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">'
    )
    for r in rows:
        status = r["scrape_status"]
        status_color = {
            "success": c["gn"],
            "not_found": c["tx3"],
            "blocked": c["rd"],
            "error": c["rd"],
        }.get(status, c["tx3"])
        timestamp = (r["scraped_at"] or "")[:16].replace("T", " ")
        listing_url = (r["listing_url"] or "").strip()
        is_link = bool(listing_url)

        # Inner content (status badge + source name + timestamp)
        inner = (
            f'<span style="background:{status_color};color:#fff;font-size:9px;'
            f'font-weight:700;padding:2px 7px;border-radius:5px;'
            f'text-transform:uppercase;letter-spacing:0.4px">{status}</span>'
            f'<div style="font-size:12px;font-weight:600;color:{c["tx"]};'
            f'margin-top:6px">{_pretty_source(r["source"])}</div>'
            f'<div style="font-size:10px;color:{c["tx3"]};margin-top:2px;'
            f'font-family:\'JetBrains Mono\',monospace">{timestamp}</div>'
        )

        if is_link:
            # Clickable anchor square — opens listing URL in a new tab.
            squares_html += (
                f'<a href="{listing_url}" target="_blank" rel="noopener noreferrer" '
                f'title="Open {_pretty_source(r["source"])} listing"'
                f'style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
                f'border-radius:8px;padding:8px 10px;min-width:150px;max-width:180px;'
                f'flex:0 0 auto;text-decoration:none;display:block;'
                f'transition:border-color 0.15s, transform 0.15s;cursor:pointer"'
                f'onmouseover="this.style.borderColor=\'{c["ac"]}\';this.style.transform=\'translateY(-1px)\'"'
                f'onmouseout="this.style.borderColor=\'{c["bdr"]}\';this.style.transform=\'translateY(0)\'">'
                f'{inner}</a>'
            )
        else:
            # Non-link div (e.g., NOT_FOUND with no URL captured)
            squares_html += (
                f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
                f'border-radius:8px;padding:8px 10px;min-width:150px;max-width:180px;'
                f'flex:0 0 auto">'
                f'{inner}</div>'
            )
    squares_html += '</div>'
    st.markdown(squares_html, unsafe_allow_html=True)

    _render_availability(rows, c)


def _render_availability(rows, c) -> None:
    """Vacancy / unit-mix signal from the availability board (owner ask
    2026-08-03). Uses the most recent successful scrape that captured units."""
    from core import unit_signal as us

    best = None
    for r in rows:
        keys = r.keys() if hasattr(r, "keys") else []
        if "units_available" not in keys or not r["units_available"]:
            continue
        best = r
        break
    if best is None:
        return
    sig = {k: (best[k] if k in best.keys() else None) for k in (
        "units_available", "units_available_now", "next_available",
        "unit_mix", "unit_rent_min", "unit_rent_max", "units_special_offers")}
    line = us.headline(sig)
    if not line:
        return
    st.markdown(
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {c["ac"]};border-radius:6px;padding:8px 12px;'
        f'margin-bottom:8px;font-size:12px;color:{c["tx"]}">'
        f'🏘️ <b>Availability</b> — {line}'
        f'<div style="font-size:10px;color:{c["tx3"]};margin-top:2px">'
        f'From the listing\'s advertised units; a floor on vacancy, not the '
        f'full rent roll.</div></div>',
        unsafe_allow_html=True)


def _scrape_one_property(property_id: str, legacy_id: str, prop: dict) -> int:
    """Run the scraper for a single property + all its configured sources.

    Uses the IN-WORKBENCH scraper stack (`core.listings_pull` + `etl_listings`),
    not the old v2.4.1 `hampton-roads-etl/pullers` package — importing that
    package is what raised "No module named 'pullers'" (it isn't in the v5
    tree). The nightly autopilot pull already runs on this same stack, so the
    button and the autopilot now share one code path and one row shape.

    Returns count of rows written to rent_listings.
    """
    import datetime as dt

    from core import listings_pull as lp

    registry = lp._scraper_registry()
    manual = lp.load_manual_urls()
    urls_for_property = {}
    for k in (property_id, legacy_id or ""):
        if k and k in manual:
            urls_for_property = manual[k]
            break
    if not urls_for_property:
        return 0

    db = _listings_db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []
    with sqlite3.connect(db) as conn:
        for source_id, url in urls_for_property.items():
            if source_id.startswith("_") or not url:
                continue
            cls = registry.get(source_id)
            if cls is None:
                continue
            base = {
                "property_id": property_id, "name": prop.get("name"),
                "address": prop.get("address"), "city": prop.get("city"),
                "source": source_id, "listing_url": url, "listing_name": None,
                "one_br_rent_low": None, "one_br_rent_high": None,
                "two_br_rent_low": None, "two_br_rent_high": None,
                "concession_text": None, "effective_one_br_rent": None,
                "effective_two_br_rent": None, "scrape_status": "not_found",
                "error_message": None, "scraped_at": now,
                "pull_generation": lp.PULL_GENERATION,
                **lp._UNIT_SIGNAL_DEFAULTS,
            }
            try:
                listing = cls().scrape_property(url)
                if listing is not None:
                    base.update(lp._rent_bands(listing))
                    base.update(lp._unit_signal(listing))
                    base["listing_name"] = listing.listing_name
                    base["concession_text"] = listing.concession_text
                    base["scrape_status"] = "success"
            except Exception as e:      # noqa: BLE001 - one bad site never kills the run
                base["scrape_status"] = "error"
                base["error_message"] = f"{type(e).__name__}: {e}"
            rows.append(tuple(base[c] for c in lp._ROW_COLS))

        if not rows:
            return 0
        conn.execute(f"""CREATE TABLE IF NOT EXISTS rent_listings
            ({', '.join(c + ' TEXT' if c in lp._TEXT_COLS else c + ' REAL'
                        for c in lp._ROW_COLS)})""")
        lp._add_missing_columns(conn, "rent_listings")
        conn.executemany(
            f"INSERT INTO rent_listings ({', '.join(lp._ROW_COLS)}) "
            f"VALUES ({', '.join('?' for _ in lp._ROW_COLS)})", rows)
        conn.commit()
    return len(rows)
