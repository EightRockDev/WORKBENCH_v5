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
    aln_id = str(prop.get("aln_id") or "")
    config_dict = _load_config()
    # Keys can be property_id OR aln_id (legacy favorites). Try both.
    cur = config_dict.get(property_id) or config_dict.get(aln_id) or {}
    effective_key = property_id if property_id in config_dict else (
        aln_id if aln_id in config_dict else property_id
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
                    n = _scrape_one_property(property_id, aln_id, prop)
                if n > 0:
                    st.success(f"Scrape complete — {n} source(s) scraped.")
                    st.rerun()
                else:
                    st.warning("No data returned. Check the URL is correct.")

        # ---- Latest scrape result for this property ----
        _render_latest_scrape(property_id, aln_id)


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


def _render_latest_scrape(property_id: str, aln_id: str) -> None:
    """Show the most-recent rent_listings rows for this property."""
    c = config.COLORS
    if not _listings_db().is_file():
        return
    try:
        with sqlite3.connect(_listings_db()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT source, scrape_status, listing_url, listing_name, "
                "       one_br_rent_low, one_br_rent_high, "
                "       two_br_rent_low, two_br_rent_high, "
                "       concession_text, concession_months_free, "
                "       effective_one_br_rent, effective_two_br_rent, "
                "       scraped_at, error_message "
                "FROM rent_listings "
                "WHERE property_id IN (?, ?) "
                "ORDER BY scraped_at DESC LIMIT 4",
                (property_id, aln_id or property_id),
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


def _scrape_one_property(property_id: str, aln_id: str, prop: dict) -> int:
    """Run the scraper for a single property + all its configured sources.

    Returns count of rows actually written to rent_listings.
    """
    # Temporarily add hampton-roads-etl to sys.path so we can import the scraper
    # modules, then IMMEDIATELY pop it. Leaving it on sys.path breaks the
    # workbench because both `hampton-roads-etl/config.py` and `python_workbench/
    # config.py` declare top-level `config` — whichever dir is first on sys.path
    # wins for `import config`, and the ETL config has no LP_PREF / LP_RESIDUAL_SPLIT
    # which causes core.waterfall to crash on next module-load. (Streamlit reruns
    # can re-trigger module imports; the wrong config gets cached and the whole
    # app breaks.)
    etl_dir = str(_WB_ROOT / "hampton-roads-etl")
    sys.path.insert(0, etl_dir)
    try:
        try:
            from pullers.listings.runner import (  # type: ignore
                SOURCES,
                _scrape_one as scrape_one_runner,
                load_favorite_listings,
            )
        except ImportError as e:
            st.error(f"Scraper import failed: {e}")
            return 0
    finally:
        try:
            sys.path.remove(etl_dir)
        except ValueError:
            pass

    manual_urls = load_favorite_listings()
    keys_to_try = [property_id, aln_id or ""]
    urls_for_property = {}
    for k in keys_to_try:
        if k and k in manual_urls:
            urls_for_property = manual_urls[k]
            break

    if not urls_for_property:
        return 0

    import pandas as pd
    rows = []
    aln_for_scraper = {
        "property_id": property_id,
        "name": prop.get("name") or "",
        "address": prop.get("address") or "",
        "city": prop.get("city") or "",
        "units": prop.get("units") or 0,
    }

    for source_id, url in urls_for_property.items():
        if source_id.startswith("_"):
            continue
        if not url:
            continue
        scraper_cls = SOURCES.get(source_id)
        if scraper_cls is None:
            continue
        scraper = scraper_cls()
        row = scrape_one_runner(scraper, aln_for_scraper, cached_url=None, manual_url=url)
        rows.append(row)

    if not rows:
        return 0

    df = pd.DataFrame(rows)
    # Append rather than replace — preserve other properties' rows
    with sqlite3.connect(_listings_db()) as conn:
        df.to_sql("rent_listings", conn, if_exists="append", index=False)
    return len(rows)
