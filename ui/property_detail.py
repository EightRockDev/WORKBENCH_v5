"""Property Detail tab — subject info, property record data, notes, sale history, rent roll."""

from __future__ import annotations

import base64
import mimetypes
import os
import platform
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import config
from data.parsers import ParseResult, combine_blocks, parse_uploaded_document
from data.property_io import (
    PropertyFolder,
    ensure_property_folder,
    find_property_photo,
    is_favorite,
    load_assessment_history,
    load_notes,
    load_sales,
    load_sources,
    merge_sources,
    save_notes,
    save_property_photo,
    toggle_favorite,
)
from ui.components import section_card


def _open_folder_in_explorer(path: Path) -> None:
    """Open the given directory in the OS file explorer.

    Streamlit runs locally on Brian's machine, so launching a desktop process
    works. Provides a visible toast either way so clicks always feel
    responsive.

    Bug fix v0.86: prior implementation used `os.startfile(path)` which on
    Windows DOES open Explorer but doesn't bring it to the foreground —
    Brian clicked the button, Explorer opened behind other windows, looked
    broken. Switched to `subprocess.Popen(['explorer', path])` which
    consistently surfaces the new window. Falls back to `os.startfile` if
    explorer isn't on PATH for some reason.
    """
    p = str(path)
    if not path.exists():
        st.error(f"Folder doesn't exist on disk: {p}")
        return

    system = platform.system()
    try:
        if system == "Windows":
            # `explorer.exe` returns 1 on success (quirk) — don't check the
            # return code. Popen detaches so Streamlit doesn't block.
            subprocess.Popen(["explorer", p], shell=False)
        elif system == "Darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
        st.toast(f"📁 Opening `{path.name}` in your file explorer…", icon="📁")
    except (OSError, FileNotFoundError) as e:
        # Last-ditch fallback for Windows: try os.startfile if explorer.exe
        # isn't on PATH (rare but possible in locked-down environments).
        if system == "Windows":
            try:
                os.startfile(p)  # type: ignore[attr-defined]
                st.toast(
                    f"📁 Opened `{path.name}` (check the taskbar — Explorer "
                    "may have opened behind your browser).",
                    icon="📁",
                )
                return
            except OSError:
                pass
        st.error(
            f"Could not open folder: {e}\n\n"
            f"Path: `{p}`\n\nYou can open it manually in File Explorer."
        )


def _fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


# Column-name patterns used by `_format_money_columns` to auto-format
# dataframe cells. Hits anything containing these substrings (case-insensitive).
_DOLLAR_COL_PATTERNS = ("price", "amount", "value", "cost", "rent", "charge")
_PER_SQFT_PATTERNS = ("per_sqft", "persqft", "/sqft", "rentpersqft", "rent_per_sqft")


def _format_money_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Pre-format dollar columns in a DataFrame so `st.dataframe` renders
    `$X,XXX,XXX` instead of raw integers. Per Brian's rule (memory file
    `feedback_financial_formatting.md`), every dollar value gets the $ sign
    and comma grouping; $/sqft uses two decimal places.
    """
    df = df.copy()
    for col in df.columns:
        col_lower = str(col).lower().replace(" ", "_")
        if any(p in col_lower for p in _PER_SQFT_PATTERNS):
            df[col] = df[col].apply(
                lambda v: f"${float(v):.2f}"
                if pd.notna(v) and isinstance(v, (int, float)) and not isinstance(v, bool)
                else v
            )
        elif any(p in col_lower for p in _DOLLAR_COL_PATTERNS):
            df[col] = df[col].apply(
                lambda v: f"${float(v):,.0f}"
                if pd.notna(v) and isinstance(v, (int, float)) and not isinstance(v, bool)
                else v
            )
    return df


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_dec(v: Any, decimals: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _google_maps_url(prop: dict[str, Any]) -> str:
    """Build a Google Maps URL for the property.

    Prefers lat/lng coordinates (most accurate — no geocoding ambiguity)
    when available; falls back to a free-text address query.
    """
    lat = prop.get("latitude")
    lng = prop.get("longitude")
    if lat is not None and lng is not None:
        # @lat,lng + a search query for the property name disambiguates the pin
        name = prop.get("name") or ""
        query = urllib.parse.quote_plus(f"{name} {lat},{lng}")
        return f"https://www.google.com/maps/search/?api=1&query={query}"

    # Fallback: address-based search
    parts = [
        prop.get("address") or "",
        prop.get("city") or "",
        prop.get("state") or "VA",
        prop.get("zip") or "",
    ]
    address_string = ", ".join(p for p in parts if p)
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote_plus(address_string)}"


def _photo_data_uri(photo_path: Path) -> str:
    """Read a photo file from disk and return a `data:image/...;base64,...`
    URI suitable for inlining into an `<img src=...>` tag.

    Inlining as base64 avoids needing a static-file URL route, which
    Streamlit doesn't expose for arbitrary user files.
    """
    mime = mimetypes.guess_type(str(photo_path))[0] or "image/jpeg"
    data = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _render_header(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    """Top header: ONE white card holding (1) photo + Photo Upload text link
    on the left, (2) property name + address with Google Maps link in the
    middle, (3) Favorited + Open Folder buttons on the right.

    Per Brian 5/29 v2.0.17:
      - Favorited + Open Folder buttons moved INSIDE the card (were a
        separate right-hand column outside the card).
      - The chunky upload popover button replaced with a small text
        link "Photo Upload" styled to match the Google Maps link, placed
        under the photo.
    """
    c = config.COLORS
    name = prop.get("name", "—")
    address = prop.get("address") or ""
    city = prop.get("city") or ""
    state = prop.get("state") or "VA"
    zip_code = prop.get("zip") or ""

    maps_url = _google_maps_url(prop)
    photo_path = find_property_photo(folder.path) if folder is not None else None
    fav = is_favorite(prop)
    fav_label = "⭐ Favorited" if fav else "☆ Favorite"

    # Single bordered card: photo+link | text | actions
    with st.container(border=True):
        # Marker for CSS scoping (Photo Upload popover styled as text link)
        st.markdown(
            '<div class="v2-photo-upload-mark"></div>',
            unsafe_allow_html=True,
        )
        col_photo, col_text, col_actions = st.columns(
            [1.1, 3.5, 1.4],
            vertical_alignment="center",
        )

        with col_photo:
            if photo_path is not None:
                photo_uri = _photo_data_uri(photo_path)
                st.markdown(
                    f'<a href="{photo_uri}" target="_blank" rel="noopener" '
                    f'style="display:block">'
                    f'<img src="{photo_uri}" alt="{name}" '
                    f'style="width:100%;max-width:160px;height:100px;'
                    f'object-fit:cover;border-radius:6px;'
                    f'border:1px solid {c["bdr"]};display:block;'
                    f'margin-bottom:6px"/>'
                    f'</a>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="width:100%;max-width:160px;height:100px;'
                    f'border:1px dashed {c["bdr"]};border-radius:6px;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'color:{c["tx3"]};font-size:11px;text-align:center;'
                    f'line-height:1.3;margin-bottom:6px">'
                    f'No photo<br>(use link below)</div>',
                    unsafe_allow_html=True,
                )
            # "Photo Upload" — text link (styled popover trigger). CSS in
            # property_detail's inline style block targets the popover
            # button under .v2-photo-upload-mark and strips it down to a
            # small link that matches "↗ Google Maps".
            with st.popover(
                "↗ Photo Upload",
                use_container_width=False,
            ):
                _render_photo_upload(prop, folder)

        with col_text:
            st.markdown(
                f'<a href="{maps_url}" target="_blank" rel="noopener" '
                f'style="text-decoration:none;color:inherit;display:block">'
                f'<div style="font-size:19px;font-weight:600;color:{c["tx"]};'
                f'display:flex;align-items:center;gap:6px">'
                f'{name}'
                f'<span style="color:{c["ac2"]};font-size:13px;font-weight:400" '
                f'title="Open in Google Maps">📍</span>'
                f'</div>'
                f'<div style="font-size:13px;color:{c["tx2"]};margin-top:6px">'
                f'{address}{", " if address else ""}{city}, {state} {zip_code} '
                f'<span style="color:{c["ac2"]};margin-left:6px">↗ Google Maps</span>'
                f'</div></a>',
                unsafe_allow_html=True,
            )

        with col_actions:
            # Favorite toggle — saved state in Properties/_favorites.json
            if st.button(
                fav_label,
                key=f"fav_btn_{prop.get('property_id', '')}",
                use_container_width=True,
                type="primary" if fav else "secondary",
                help="Toggle favorite. Use the sidebar's ⭐ filter to see all favorites.",
            ):
                toggle_favorite(prop)
                st.rerun()

            if folder is not None:
                if st.button(
                    "📁 Open Folder",
                    key=f"open_folder_{folder.folder_name}",
                    use_container_width=True,
                    help=f"Open `{folder.folder_name}` in your file explorer",
                ):
                    _open_folder_in_explorer(folder.path)
            else:
                st.markdown(
                    f"<div style='font-size:10px;color:{c['tx3']};text-align:center;"
                    f"line-height:1.4;margin-top:6px'>📁 no folder yet<br>"
                    f"(upload a doc to create)</div>",
                    unsafe_allow_html=True,
                )

    # CSS that styles the Photo Upload popover trigger as a small text link
    # — only active when the .v2-photo-upload-mark marker is in the DOM
    # (so it only affects this header). Sibling selector reaches the popover
    # button that follows the marker.
    st.markdown(
        f"""
<style>
.v2-photo-upload-mark {{ display: none; }}
.v2-photo-upload-mark ~ div [data-testid="stPopover"] button {{
  background: transparent !important;
  border: none !important;
  color: {c['ac2']} !important;
  padding: 0 !important;
  font-size: 12px !important;
  font-weight: 400 !important;
  text-decoration: none !important;
  min-height: 0 !important;
  height: auto !important;
  box-shadow: none !important;
  width: auto !important;
}}
.v2-photo-upload-mark ~ div [data-testid="stPopover"] button:hover {{
  text-decoration: underline !important;
  color: {c['ac']} !important;
  background: transparent !important;
}}
</style>""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Property Card v2 (Brian 5/29 v2.0.19)
#
#   • Pulls each field from the best source available: rent roll → T-12 →
#     OM → DB (property record) → manual override. Manual overrides win when set.
#   • Tags every row with a source color so the analyst can see at a glance
#     which fields are auto-extracted vs entered by hand:
#         🟢 src_rr   — rent roll (sources.json rentRoll.summary)
#         🟠 src_t12  — T-12 (sources.json t12_* blocks)
#         🟣 src_om   — OM / marketing materials (sources.json om / property_site)
#         ⚪/🟦 db    — property record (grey src_ref pre-flip, teal src_8r
#                       once SPINE_READ_SOURCE = "8r")
#         🥇 src_user — manual entry / override
#   • "Status" row removed entirely — it was an internal record/custom flag.
#   • Edit button opens a form to override any field. Overrides are saved
#     to `property_card_overrides.json` in the property folder so they
#     survive across V1↔V2 reruns and across sessions.
# ---------------------------------------------------------------------------

_PROPERTY_CARD_OVERRIDES_FILE = "property_card_overrides.json"

# Brian 5/29 v2.0.22 — multifamily product types for the Type dropdown.
# Ordered by relevance to Eight Rock's Mid-Atlantic value-add thesis.
_MULTIFAMILY_TYPES: list[str] = [
    "Garden-Style",          # 1-3 stories, surface parking — most value-add stock
    "Townhomes",
    "Walk-Up",               # 2-4 stories no elevator
    "Low-Rise",              # 1-3 stories
    "Mid-Rise",              # 4-6 stories
    "High-Rise",             # 7+ stories
    "Cottages",
    "Mixed-Use",             # retail/commercial ground floor
    "Student Housing",
    "Senior / 55+",
    "Affordable / LIHTC",
    "Single-Family Rental",
    "Manufactured Home Comm.",
]

# Brian 5/29 v2.0.22 — most common multifamily property-management
# systems in 2026. Sourced from NMHC's 2025 PMS Adoption Survey + Eight
# Rock's own broker conversations. Top 7 + "Other".
_PM_SOFTWARE_OPTIONS: list[str] = [
    "AppFolio",
    "Yardi (Voyager / Breeze)",
    "RealPage (OneSite)",
    "Entrata",
    "ResMan",
    "Buildium",
    "Rent Manager",
    "Other",
]

# Fields the analyst can override via the Edit form. Excludes computed
# fields (rent_per_sqft) and auto-derived fields (market, submarket) per
# Brian 5/29 v2.0.22 — he doesn't want to manually choose those.
_EDITABLE_FIELDS: set[str] = {
    "units", "year_built", "last_remodel", "asset_class",
    "property_type", "occupancy_pct", "avg_sqft", "avg_rent",
    "owner", "manager", "management_company", "pm_software",
    "asset_or_fee",
}

# Field labels in display order. The "Status" row is intentionally gone.
_PROPERTY_CARD_FIELDS: list[tuple[str, str]] = [
    ("units",                "Units"),
    ("year_built",           "Year Built"),
    ("last_remodel",         "Last Remodel"),
    ("asset_class",          "Class"),
    ("property_type",        "Type"),
    ("market",               "Market"),
    ("submarket",            "Submarket"),
    ("occupancy_pct",        "Occupancy"),
    ("avg_sqft",             "Avg Sqft"),
    ("avg_rent",             "Avg Rent"),
    ("rent_per_sqft",        "Rent / Sqft"),
    ("owner",                "Owner"),
    ("manager",              "Manager (person)"),
    ("management_company",   "Mgmt Company"),
    ("pm_software",          "PM Software"),
    ("asset_or_fee",         "Asset/Fee"),
]


def _load_property_card_overrides(folder: PropertyFolder | None) -> dict[str, Any]:
    """Read the per-property card overrides from disk. Returns empty dict
    when none exist."""
    if folder is None or not hasattr(folder, "path") or not folder.path.exists():
        return {}
    fp = folder.path / _PROPERTY_CARD_OVERRIDES_FILE
    if not fp.exists():
        return {}
    try:
        import json
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_property_card_overrides(
    folder: PropertyFolder,
    overrides: dict[str, Any],
) -> None:
    """Persist Property Card overrides to the folder. Filters out empty
    values so deleting a field means resorting to the auto-pulled value."""
    import json
    cleaned = {
        k: v for k, v in overrides.items()
        if v not in (None, "", "—")
    }
    fp = folder.path / _PROPERTY_CARD_OVERRIDES_FILE
    fp.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")


def _resolve_property_card_value(
    key: str,
    prop: dict[str, Any],
    sources: dict[str, Any] | None,
    overrides: dict[str, Any],
) -> tuple[Any, str]:
    """Resolve `key` from the best source available.

    Returns ``(raw_value, source_tag)`` where source_tag is one of
    {"manual", "rent_roll", "t12", "om", "db", ""}. An empty source_tag
    means we couldn't find a value anywhere.

    Priority: manual override → rent roll → T-12 → OM → DB (record).
    """
    if key in overrides and overrides[key] not in (None, "", "—"):
        return overrides[key], "manual"

    s = sources or {}

    # Computed fields — Brian 5/29 v2.0.22.
    # Rent / Sqft is always derivable when avg_rent and avg_sqft are both
    # known. Compute here so the analyst doesn't have to keep it in sync
    # by hand. Recurse for the inputs so each comes from its best source.
    if key == "rent_per_sqft":
        ar_raw, _ = _resolve_property_card_value("avg_rent", prop, sources, overrides)
        sq_raw, _ = _resolve_property_card_value("avg_sqft", prop, sources, overrides)
        try:
            ar = float(ar_raw) if ar_raw not in (None, "") else None
            sq = float(sq_raw) if sq_raw not in (None, "") else None
        except (TypeError, ValueError):
            ar = sq = None
        if ar and sq and sq > 0:
            return ar / sq, "computed"

    # Rent-roll-priority fields
    rr = s.get("rentRoll") if isinstance(s, dict) else None
    rr_summary = rr.get("summary") if isinstance(rr, dict) else None
    if isinstance(rr_summary, dict):
        if key == "units":
            v = rr_summary.get("totalUnits") or rr_summary.get("unitCount")
            if v: return v, "rent_roll"
        if key == "avg_rent":
            actual = rr_summary.get("totalActualRent")
            cnt = rr_summary.get("occupiedUnits") or rr_summary.get("totalUnits")
            if actual and cnt:
                try:
                    return float(actual) / float(cnt), "rent_roll"
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        if key == "avg_sqft":
            v = rr_summary.get("avgSqft") or rr_summary.get("averageSqft")
            if v: return v, "rent_roll"
        if key == "rent_per_sqft":
            v = rr_summary.get("rentPerSqft")
            if v: return v, "rent_roll"
        if key == "occupancy_pct":
            # Brian 5/29 v2.0.26 — `_fmt_pct` already multiplies by 100;
            # we have to return a FRACTION (0-1), not a percentage value.
            # The old code returned 92.10 → _fmt_pct(92.10) = "9210.0%".
            occ = rr_summary.get("occupancyPct") or rr_summary.get("occupiedPct")
            if occ:
                try:
                    val = float(occ)
                    # Source may already be 0-1 OR 0-100. Normalize to 0-1.
                    return (val if val <= 1.0 else val / 100.0), "rent_roll"
                except (TypeError, ValueError):
                    pass
            occ_u = rr_summary.get("occupiedUnits")
            tot_u = rr_summary.get("totalUnits")
            if occ_u and tot_u:
                try:
                    return float(occ_u) / float(tot_u), "rent_roll"
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

    # OM-priority fields
    om = s.get("om") if isinstance(s, dict) else None
    if isinstance(om, dict):
        om_keys = {
            "year_built": ("yearBuilt", "year_built"),
            "last_remodel": ("yearRenovated", "lastRenovated", "last_remodel"),
            "units": ("totalUnits", "units"),
            "asset_class": ("assetClass", "class"),
            "property_type": ("propertyType", "type"),
            "avg_sqft": ("avgSqft", "averageSqft"),
            "avg_rent": ("avgRent", "averageRent"),
        }
        if key in om_keys:
            for k in om_keys[key]:
                v = om.get(k)
                if isinstance(v, dict):
                    v = v.get("value")
                if v not in (None, ""):
                    return v, "om"

    # T-12 fallback for occupancy (rare — T-12s sometimes report it)
    if key == "occupancy_pct":
        t12_inc = s.get("t12_income") if isinstance(s, dict) else None
        if isinstance(t12_inc, dict):
            occ = t12_inc.get("occupancyPct")
            if occ:
                try:
                    val = float(occ)
                    # Same 0-1 vs 0-100 normalization as rent_roll branch.
                    return (val if val <= 1.0 else val / 100.0), "t12"
                except (TypeError, ValueError):
                    pass

    # DB fallback (property record)
    db_v = prop.get(key)
    # Same fraction normalization for DB-sourced occupancy.
    if key == "occupancy_pct" and db_v not in (None, ""):
        try:
            val = float(db_v)
            return (val if val <= 1.0 else val / 100.0), "db"
        except (TypeError, ValueError):
            pass
    if db_v not in (None, ""):
        return db_v, "db"
    return None, ""


def _format_property_card_value(key: str, raw: Any) -> str:
    """Display formatter — int / pct / money / decimal depending on field."""
    if raw is None or raw == "":
        return "—"
    int_keys = {"units", "year_built", "last_remodel", "avg_sqft"}
    pct_keys = {"occupancy_pct"}
    money_keys = {"avg_rent"}
    dec_keys = {"rent_per_sqft"}
    try:
        if key in int_keys:
            return _fmt_int(raw)
        if key in pct_keys:
            return _fmt_pct(raw)
        if key in money_keys:
            return _fmt_money(raw)
        if key in dec_keys:
            return _fmt_dec(raw)
    except (TypeError, ValueError):
        pass
    s = str(raw).strip()
    return s if s else "—"


def _render_property_card(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    """Property Card with per-row source colorization. See module docstring
    block above for source tag legend."""
    c = config.COLORS

    sources: dict[str, Any] | None = None
    if folder is not None and hasattr(folder, "path") and folder.path.exists():
        sources = load_sources(folder.path)

    overrides = _load_property_card_overrides(folder)

    src_colors = {
        "rent_roll": c.get("src_rr",   "#15803d"),
        "t12":       c.get("src_t12",  "#b45309"),
        "om":        c.get("src_etl",  "#7c3aed"),
        # Grey pre-flip (reference survey row), teal post-flip (8R backbone)
        # - resolved at render time from the SPINE_READ_SOURCE seam.
        "db":        config.spine_provenance_color(),
        "manual":    c.get("src_user", "#a37102"),
        "computed":  c.get("src_calc", "#1d4ed8"),
    }
    src_glyph = {
        "rent_roll": "RR",
        "t12":       "T12",
        "om":        "OM",
        "db":        "8R",
        "manual":    "Manual",
        "computed":  "Calc",
    }

    # Brian 5/29 v2.0.23 — badges now render on the LEFT of the label.
    # Old layout was [label ............ badge value]; new layout is
    # [badge label ............ value]. A fixed-width 50px slot keeps
    # the labels aligned even when a row has no badge (the slot stays
    # blank so the LABEL column starts at the same x-position on every
    # row).
    rows_html = ""
    for key, label in _PROPERTY_CARD_FIELDS:
        raw, src_tag = _resolve_property_card_value(key, prop, sources, overrides)
        display = _format_property_card_value(key, raw)
        if src_tag:
            tag_color = src_colors.get(src_tag, c.get("src_unknown", "#94a3b8"))
            badge_html = (
                f'<span style="display:inline-block;padding:1px 6px;'
                f'border:1px solid {tag_color};border-radius:3px;'
                f'color:{tag_color};font-size:9px;font-weight:700;'
                f'letter-spacing:0.4px;text-transform:uppercase;'
                f'line-height:1.3">{src_glyph[src_tag]}</span>'
            )
            value_color = tag_color
        else:
            badge_html = ""
            value_color = c["tx3"]

        rows_html += (
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:center;padding:6px 0;'
            f'border-bottom:1px solid {c["bdr"]};gap:10px">'
            f'<span style="display:flex;align-items:center;gap:10px;flex:1">'
            f'<span style="display:inline-flex;align-items:center;'
            f'justify-content:flex-start;min-width:50px;flex-shrink:0">'
            f'{badge_html}</span>'
            f'<span style="color:{c["tx3"]};font-size:11px;'
            f'text-transform:uppercase;letter-spacing:0.5px">{label}</span>'
            f'</span>'
            f'<span style="font-size:13px;color:{value_color};'
            f'font-variant-numeric:tabular-nums;text-align:right">'
            f'{display}</span>'
            f"</div>"
        )

    st.markdown(
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'border-radius:6px;padding:8px 14px">{rows_html}</div>',
        unsafe_allow_html=True,
    )

    # Legend
    legend_items = [
        ("RR",     "Rent Roll",     src_colors["rent_roll"]),
        ("T12",    "T-12",          src_colors["t12"]),
        ("OM",     "OM / Marketing", src_colors["om"]),
        ("8R",     "8R Backbone",   src_colors["db"]),
        ("Calc",   "Computed",      src_colors["computed"]),
        ("Manual", "Manual entry",  src_colors["manual"]),
    ]
    legend_html = ''.join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'margin-right:14px;font-size:10px;color:{c["tx3"]}">'
        f'<span style="display:inline-block;width:7px;height:7px;'
        f'border-radius:50%;background:{color}"></span>{glyph} = {label}'
        f'</span>'
        for glyph, label, color in legend_items
    )
    st.markdown(
        f'<div style="margin-top:8px;line-height:1.7">{legend_html}</div>',
        unsafe_allow_html=True,
    )


def _render_property_card_edit_form(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    """Edit form inside the popover. Lets the analyst override any field.
    Saved values become "Manual" entries and win over auto-pulled values.

    Brian 5/29 v2.0.22:
      - **Type** is now a dropdown of multifamily product types.
      - **PM Software** is now a dropdown of the top-7 systems + Other.
      - **Rent / Sqft** is removed (auto-computed from avg_rent ÷ avg_sqft).
      - **Market / Submarket** are removed (auto-pulled from city + Census /
        public data; not analyst-chooseable).
      - The implementation-detail caption (the one that mentioned the
        overrides filename) is gone — Brian isn't a technical user and
        doesn't need to see the path.
    """
    if folder is None:
        st.info(
            "Upload a document first to create a property folder, then "
            "open Edit to override any field."
        )
        return

    sources = load_sources(folder.path) if folder.path.exists() else None
    overrides = _load_property_card_overrides(folder)

    st.caption(
        "Leave a field blank to revert to the auto-pulled value. Saved "
        "overrides show with the **Manual** badge on the Property Card."
    )

    def _auto_placeholder(key: str) -> str:
        auto_raw, auto_src = _resolve_property_card_value(
            key, prop, sources, {}
        )
        if auto_raw in (None, "") or not auto_src:
            return "—"
        return f"auto: {_format_property_card_value(key, auto_raw)} ({auto_src})"

    def _select_index(options: list[str], current: Any) -> int:
        """Pick the selectbox index that matches a saved override or auto
        value (case-insensitive). 0 = '(leave blank — use auto)'."""
        if current in (None, ""):
            return 0
        s = str(current).strip().lower()
        for i, opt in enumerate(options, start=1):
            if opt.strip().lower() == s:
                return i
        return 0  # fall through to "leave blank" if no match

    new_overrides: dict[str, Any] = {}

    # Field labels for the editable set (preserves display order, drops the
    # non-editable fields per the docstring above).
    EDITABLE_DISPLAY: list[tuple[str, str]] = [
        (key, label) for key, label in _PROPERTY_CARD_FIELDS
        if key in _EDITABLE_FIELDS
    ]

    with st.form(key=f"prop_card_edit_{folder.folder_name}", clear_on_submit=False):
        cols = st.columns(2)
        for i, (key, label) in enumerate(EDITABLE_DISPLAY):
            current_override = overrides.get(key, "")

            with cols[i % 2]:
                # ---- Type dropdown ----
                if key == "property_type":
                    options = ["(leave blank — use auto)"] + _MULTIFAMILY_TYPES
                    idx = _select_index(_MULTIFAMILY_TYPES, current_override)
                    picked = st.selectbox(
                        label,
                        options=options,
                        index=idx,
                        help=_auto_placeholder(key),
                        key=f"pc_edit_{key}_{folder.folder_name}",
                    )
                    if picked != options[0]:
                        new_overrides[key] = picked

                # ---- PM Software dropdown ----
                elif key == "pm_software":
                    options = ["(leave blank — use auto)"] + _PM_SOFTWARE_OPTIONS
                    idx = _select_index(_PM_SOFTWARE_OPTIONS, current_override)
                    picked = st.selectbox(
                        label,
                        options=options,
                        index=idx,
                        help=_auto_placeholder(key),
                        key=f"pc_edit_{key}_{folder.folder_name}",
                    )
                    if picked != options[0]:
                        new_overrides[key] = picked

                # ---- All other editable fields: free text ----
                else:
                    val = st.text_input(
                        label,
                        value=str(current_override) if current_override != "" else "",
                        placeholder=_auto_placeholder(key),
                        key=f"pc_edit_{key}_{folder.folder_name}",
                    )
                    if val.strip():
                        if key in ("units", "year_built", "last_remodel", "avg_sqft"):
                            try:
                                new_overrides[key] = int(float(val.strip()))
                                continue
                            except ValueError:
                                pass
                        if key in ("occupancy_pct", "avg_rent"):
                            try:
                                new_overrides[key] = float(val.strip())
                                continue
                            except ValueError:
                                pass
                        new_overrides[key] = val.strip()

        col_save, col_clear, _ = st.columns([1, 1, 2])
        with col_save:
            saved = st.form_submit_button("💾 Save", type="primary")
        with col_clear:
            cleared = st.form_submit_button("Clear all")

    if saved:
        _save_property_card_overrides(folder, new_overrides)
        st.success(f"✓ Saved {len(new_overrides)} override(s)")
        st.rerun()
    if cleared:
        _save_property_card_overrides(folder, {})
        st.success("✓ All overrides cleared — reverting to auto-pull")
        st.rerun()


def _render_notes(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    """Notes textarea — auto-saves to notes.txt on change.

    If no property folder exists yet, the first save auto-creates one.
    """
    # Pick a stable key whether or not the folder exists yet
    folder_key = folder.folder_name if folder is not None else f"new_{prop.get('property_id', '')}"
    notes_key = f"notes_{folder_key}"

    existing = load_notes(folder.path) if folder is not None else ""
    # Brian 5/29 v2.0.24 — bulletproof notes recovery.
    # v2.0.21 partial fix wasn't enough; Brian still saw notes disappear.
    # Three failure modes we have to defend against:
    #   (1) session_state[notes_key] never set → must load from disk.
    #   (2) Widget got cleared/emptied by a Streamlit DOM glitch but disk
    #       still has the user's content → reload (was the v2.0.21 fix).
    #   (3) User opens the app in a 2nd tab, edits there, saves; first tab
    #       still shows the OLD content. The save in tab 2 changed disk.
    #       If the user hasn't typed anything in tab 1 since the last load,
    #       it's safe (and right) to reload from disk → cross-tab sync.
    #
    # `disk_marker_key` holds what we last loaded from disk for THIS folder.
    # When the widget value equals the disk_marker, the user hasn't typed
    # anything since the last load, so a disk refresh won't trample typing.
    disk_marker_key = f"notes_disk_marker_{folder_key}"
    cur = st.session_state.get(notes_key)
    last_disk = st.session_state.get(disk_marker_key)
    user_is_mid_typing = (
        cur is not None
        and cur != last_disk
        and (cur or "").strip()
    )
    should_rehydrate = (
        notes_key not in st.session_state           # first render
        or not (cur or "").strip()                  # widget empty + recovery
        or (not user_is_mid_typing and existing != last_disk)  # cross-tab
    )
    if should_rehydrate:
        st.session_state[notes_key] = existing
    st.session_state[disk_marker_key] = existing

    # Brian 5/29 v2.0.12: Notes defaults to roughly the same height as the
    # Property Card next to it (~17 rows × ~30px + chrome ≈ 600px). Gives
    # plenty of room to write without scrolling, and visually balances the
    # side-by-side layout on the Subject tab.
    new_value = st.text_area(
        "Notes",
        key=notes_key,
        height=600,
        placeholder="Free-form analyst notes…",
        label_visibility="collapsed",
    )

    if new_value != existing and new_value.strip():
        # Auto-create folder on first save when it doesn't exist
        target_folder = folder
        if target_folder is None:
            target_folder = ensure_property_folder(prop)
            st.success(f"📁 Created folder `{target_folder.folder_name}`")
        save_notes(target_folder.path, new_value)
        st.caption("✓ saved")
        if folder is None:
            st.rerun()  # refresh so the folder is now visible elsewhere


def _format_sale_date(v: Any) -> str:
    """Convert ISO date strings to MM/DD/YYYY for display."""
    if v is None or v == "":
        return "—"
    try:
        s = str(v).strip()
        # Match YYYY-MM-DD prefix; tolerate trailing time bits
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            yr, mo, dy = s[:4], s[5:7], s[8:10]
            return f"{int(mo)}/{int(dy)}/{yr}"
        return s
    except (ValueError, TypeError):
        return str(v)


def _normalize_llc(v: Any) -> str:
    """Norfolk deed records come in TitleCase ('Valore At Southern Park, Llc').
    Capitalize 'Llc' → 'LLC' so the LLC suffix is unambiguous."""
    if v is None:
        return "—"
    s = str(v).strip()
    if not s:
        return "—"
    # Replace standalone 'Llc' with 'LLC' (case-sensitive, word-boundary)
    import re
    return re.sub(r"\bLlc\b", "LLC", s)


def _parse_assessment_history(text: str) -> list[dict[str, Any]]:
    """Extract FY-by-FY assessed values from a free-text notes string.

    The convention used in our `sales.json` notes (manually populated for the
    handful of HR properties with deed lookups) looks like:

        "Assessed values: FY19 $6,463,000 | FY20 $6,463,000 | FY23 $10,664,900 |
        FY26 $11,538,800 (+78.5% since FY19)."

    This parser is forgiving: matches `FY{2-digit-or-4-digit} $N{,N}*`. Returns
    a list ordered by fiscal year ascending. Empty if no assessment data found.

    Tolerates:
      - 2-digit (FY19) AND 4-digit (FY2026) year tokens
      - Skipped years (Andover has FY24 then FY26, no FY25 — preserved as gap)
      - Embedded commentary between segments (parses each FY independently)
    """
    if not text:
        return []
    import re
    pattern = re.compile(r"FY(\d{2,4})\s*\$\s*([\d,]+)")
    rows: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        year_token = match.group(1)
        amount_str = match.group(2).replace(",", "")
        try:
            amount = int(amount_str)
        except ValueError:
            continue
        # Normalize 2-digit year to 4-digit (FY19 → 2019, FY27 → 2027)
        if len(year_token) == 2:
            yr = int(year_token)
            year_4 = 2000 + yr if yr < 50 else 1900 + yr
        else:
            year_4 = int(year_token)
        rows.append({"fiscal_year": year_4, "assessed_value": amount})

    # De-duplicate (notes sometimes repeat the same FY) and sort
    seen: dict[int, int] = {}
    for r in rows:
        seen[r["fiscal_year"]] = r["assessed_value"]
    return [
        {"fiscal_year": fy, "assessed_value": v}
        for fy, v in sorted(seen.items())
    ]


def _gather_assessment_history_from_sales(
    sales_data: Any,
) -> list[dict[str, Any]]:
    """Walk a sales.json payload (list OR dict shape) and extract every FY
    assessment we can find from the notes fields.

    Returns the merged FY-by-FY list across ALL records — sometimes the
    assessment history is on the most-recent sale, sometimes on an earlier
    sale, sometimes split across multiple notes.
    """
    if not sales_data:
        return []
    # Normalize to a list of records
    if isinstance(sales_data, dict):
        records = (
            sales_data.get("last_3_apartment_sales")
            or sales_data.get("sales")
            or []
        )
    elif isinstance(sales_data, list):
        records = sales_data
    else:
        return []

    merged: dict[int, int] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        notes = rec.get("notes") or ""
        for row in _parse_assessment_history(notes):
            # Latest record wins on collision (sorted by date, last sale = newest data)
            merged[row["fiscal_year"]] = row["assessed_value"]

    return [
        {"fiscal_year": fy, "assessed_value": v}
        for fy, v in sorted(merged.items())
    ]


def _render_assessment_history(folder: PropertyFolder | None) -> None:
    """Tax assessment history table.

    Phase 2 (2026-05-08): prefers the structured `sources.json -> assessmentHistory`
    block, falls back to parsing free-text from `sales.json` notes for any
    property that hasn't been migrated yet.

    Displays as a clean FY-by-FY table with YoY changes and visual cues for:
      - 🔥 Reassessment jumps > 30% (typical post-sale reassessment)
      - 📉 Year-over-year drops (rare — usually market correction)
      - Cumulative growth since first reading

    Phase 3 will auto-pull from city assessor open-data portals.
    """
    if folder is None:
        return

    # ---- Pass 1: prefer the structured block ----
    structured = load_assessment_history(folder.path)
    history: list[dict[str, Any]] = []
    source_label: str | None = None
    parcel_meta: str = ""
    if structured:
        history = list(structured.get("records") or [])
        source_label = structured.get("source") or "City Assessor"
        bits = []
        if structured.get("city"):
            bits.append(structured["city"])
        if structured.get("parcel_id"):
            bits.append(f"Parcel {structured['parcel_id']}")
        if structured.get("gpin"):
            bits.append(f"GPIN {structured['gpin']}")
        if structured.get("pull_date"):
            bits.append(f"pulled {structured['pull_date']}")
        parcel_meta = " · ".join(bits)

    # ---- Pass 2: fall back to legacy notes parsing ----
    if not history:
        sales = load_sales(folder.path)
        history = _gather_assessment_history_from_sales(sales)
        if history:
            source_label = "Embedded in sales.json notes (legacy)"

    if not history:
        return

    c = config.COLORS
    # Header is now provided by the parent section_card; we just emit
    # the source/parcel caption inline.
    caption_parts = [
        f"Source: {source_label}" if source_label else "City assessor records",
    ]
    if parcel_meta:
        caption_parts.append(parcel_meta)
    st.caption(
        " · ".join(caption_parts)
        + ". Gold flames flag reassessment jumps — typical at sale."
    )

    # Build the dataframe with YoY change column
    first = history[0]
    last = history[-1]
    rows = []
    prev_v = None
    for r in history:
        fy = r["fiscal_year"]
        v = r["assessed_value"]
        yoy = None if prev_v is None else (v - prev_v) / prev_v if prev_v else None
        # Flag interpretation
        if yoy is None:
            flag = "Baseline"
            yoy_str = "—"
        elif yoy >= 0.30:
            flag = f"🔥 Reassessment +{yoy*100:.1f}%"
            yoy_str = f"+{yoy*100:.1f}%"
        elif yoy >= 0.05:
            flag = f"+{yoy*100:.1f}%"
            yoy_str = f"+{yoy*100:.1f}%"
        elif yoy < -0.02:
            flag = f"📉 {yoy*100:+.1f}%"
            yoy_str = f"{yoy*100:+.1f}%"
        else:
            flag = f"{yoy*100:+.1f}%"
            yoy_str = f"{yoy*100:+.1f}%"
        rows.append({
            "FY": f"FY{fy}",
            "Assessed Value": f"${v:,}",
            "YoY": yoy_str,
            "Note": flag,
        })
        prev_v = v

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Cumulative growth callout
    if last["assessed_value"] > 0 and first["assessed_value"] > 0 and len(history) > 1:
        cum_pct = (last["assessed_value"] - first["assessed_value"]) / first["assessed_value"]
        n_years = last["fiscal_year"] - first["fiscal_year"]
        cagr = ((last["assessed_value"] / first["assessed_value"]) ** (1 / n_years) - 1) if n_years > 0 else 0
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {c["ac"]};'
            f'border-radius:4px;padding:10px 14px;margin-top:6px;color:{c["tx"]};'
            f'font-size:13px;line-height:1.5">'
            f'<b>Cumulative</b>: <span style="color:{c["ac2"]};font-weight:700">'
            f'+{cum_pct*100:.1f}%</span> since FY{first["fiscal_year"]} '
            f'({n_years} years · {cagr*100:.1f}% CAGR). Latest assessed value: '
            f'<b>${last["assessed_value"]:,}</b>.<br>'
            f'<span style="color:{c["tx2"]};font-size:12px">'
            f'💡 Reassessment risk on YOUR purchase: budget post-sale assessed ≈ '
            f'85% × your offer price. Most underwriters miss this — see the '
            f'Property tax reassessment toggle in Underwriting.</span></div>',
            unsafe_allow_html=True,
        )


def _render_sales(folder: PropertyFolder | None) -> None:
    if folder is None:
        return
    sales = load_sales(folder.path)
    if not sales:
        st.caption("No sale history available.")
        return

    # Newer auto-pulled shape: dict with `last_3_apartment_sales` list
    if isinstance(sales, dict):
        records = sales.get("last_3_apartment_sales") or sales.get("sales") or []
        meta = {k: v for k, v in sales.items() if k not in ("last_3_apartment_sales", "sales", "parcel_lookup")}
        if meta:
            with st.expander("Source metadata", expanded=False):
                st.json(meta, expanded=False)
    else:
        records = sales

    if not records:
        st.caption("No sale records.")
        return

    df = pd.DataFrame(records)
    # Drop overly-nested columns for cleaner display (parcels_conveyed etc.)
    drop = [col for col in df.columns if df[col].apply(lambda v: isinstance(v, (list, dict))).any()]
    if drop:
        df = df.drop(columns=drop)

    # Format known columns. Source records use legal terms `grantor`/`grantee`
    # (= seller / buyer respectively, per Black's Law). Rename to plain English
    # so reading the chain is unambiguous: each row shows BUYER receiving from
    # SELLER on the given DATE for the given PRICE.
    rename_map = {
        "date": "Date",
        "price": "Price",
        "grantor": "Seller",
        "grantee": "Buyer",
        "notes": "Notes",
    }
    if "date" in df.columns:
        df["date"] = df["date"].apply(_format_sale_date)
    for col in ("grantor", "grantee"):
        if col in df.columns:
            df[col] = df[col].apply(_normalize_llc)

    df = df.rename(columns=rename_map)
    # Reorder columns: Date, Price, Seller, Buyer, then any remaining (notes, etc.)
    preferred = [c for c in ("Date", "Price", "Seller", "Buyer") if c in df.columns]
    rest = [c for c in df.columns if c not in preferred]
    df = df[preferred + rest]

    df = _format_money_columns(df)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        "📜 Each row reads chronologically: **Buyer** received the property from **Seller** "
        "on **Date** for **Price**. The most-recent buyer is the current owner of record."
    )


def _render_rent_roll(folder: PropertyFolder | None) -> None:
    """Property Detail's rent roll display — uses the shared `ui.rent_roll`
    renderer with the unit-detail expander COLLAPSED (the rent roll is
    subordinate to property info on this tab). The Underwriting tab uses
    the same renderer with `expand_units=False` too — both tabs read
    consistently and apply the same Vacant/Notice row tinting."""
    from ui.rent_roll import render_rent_roll as _shared_rent_roll
    _shared_rent_roll(folder, section_title="Rent Roll Summary", expand_units=False)


def _render_photo_upload(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    """Single-image upload widget for the property's hero photo.

    Reuses the same counter-key trick as `_render_documents` — bump the
    uploader's key after each save so it resets to empty on rerun
    (otherwise `if uploaded:` stays True forever and we get a save loop).

    Auto-creates the property folder on first upload, just like notes /
    documents / dial saves.
    """
    folder_key = folder.folder_name if folder is not None else f"new_{prop.get('property_id', '')}"
    counter_key = f"photo_upload_counter_{folder_key}"
    counter = st.session_state.get(counter_key, 0)
    uploader_key = f"photo_upload_{folder_key}_{counter}"

    # Hidden label — the popover trigger already labels itself. Inside
    # the popover, the file picker is the only thing the user sees.
    uploaded = st.file_uploader(
        "Upload",
        label_visibility="collapsed",
        accept_multiple_files=False,
        key=uploader_key,
        type=["jpg", "jpeg", "png", "webp", "gif"],
    )
    if uploaded is not None:
        target_folder = folder
        if target_folder is None:
            target_folder = ensure_property_folder(prop)
            st.success(f"📁 Created folder `{target_folder.folder_name}`")
        try:
            saved_path = save_property_photo(
                target_folder.path,
                uploaded.getbuffer().tobytes(),
                uploaded.name,
            )
            st.success(f"✓ saved photo: `{saved_path.name}`")
        except (ValueError, OSError) as e:
            st.error(f"Could not save photo: {e}")
        # Bump counter so the next rerun renders a NEW (empty) uploader
        st.session_state[counter_key] = counter + 1
        st.rerun()



def _render_documents(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    # Brian 5/29 v2.0.15: file_uploader removed from the top of this section.
    # The Document Auto-Ingestion panel below has its own uploader and is the
    # canonical entry point now (AI extraction + per-field provenance). This
    # section is purely a *listing* of files already in the folder, plus the
    # re-parse action.
    folder_key = folder.folder_name if folder is not None else f"new_{prop.get('property_id', '')}"

    # Show parse feedback from the most recent re-parse (auto-ingest writes
    # feedback under its own key, not this one).
    msg_key = f"parse_msgs_{folder_key}"
    if msg_key in st.session_state:
        for level, text in st.session_state.pop(msg_key):
            getattr(st, level)(text)

    if folder is None:
        st.caption(
            "No documents yet — drop a file in the **Upload Property "
            "Materials** section below to create the folder."
        )
        return

    # List existing files (excluding the JSON/txt control files and the
    # property photo, which is shown in the header + has its own widget).
    control = {
        "deal.json", "sources.json", "sales.json", "notes.txt",
        "mystery_shops.json",
    }
    photo_names = {f"photo{ext}" for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")}
    skip = control | photo_names
    docs = sorted(
        p for p in folder.path.iterdir()
        if p.is_file() and p.name.lower() not in skip
    )
    if not docs:
        st.caption("No uploaded documents in this folder yet.")
        return

    # Brian 5/29 v2.0.25 — file rows FIRST (each filename clickable to
    # open in its native app). Re-parse button comes AFTER the list.
    #
    # Each doc row: [📄 filename (clickable button)] [size] [🗑️ Delete].
    # The filename button opens the file via os.startfile (Windows) /
    # subprocess open|xdg-open (Mac/Linux) — same pattern as Key Documents
    # in the V2 inspector.
    import os as _os
    import subprocess as _subprocess
    import sys as _sys

    def _open_doc_native(p):
        try:
            if _sys.platform == "win32":
                _os.startfile(str(p))  # type: ignore[attr-defined]
            elif _sys.platform == "darwin":
                _subprocess.Popen(["open", str(p)])
            else:
                _subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:
            st.warning(f"Could not open {p.name}: {exc}")

    for doc in docs:
        size_kb = doc.stat().st_size / 1024
        size_str = f"{size_kb:,.0f} KB" if size_kb < 1024 else f"{size_kb/1024:,.1f} MB"
        col_doc, col_size, col_del = st.columns(
            [8, 2, 1], vertical_alignment="center"
        )
        with col_doc:
            if st.button(
                f"📄 {doc.name}",
                key=f"open_doc_{folder_key}_{doc.name}",
                help=f"Open {doc.name} in its native application",
                use_container_width=True,
            ):
                _open_doc_native(doc)
        with col_size:
            st.caption(size_str)
        with col_del:
            with st.popover("🗑️", help=f"Delete {doc.name}", use_container_width=True):
                st.markdown(
                    f"**Delete `{doc.name}`?**  \n"
                    f"_{size_str}_ — this can't be undone."
                )
                if st.button(
                    "✗ Yes, delete",
                    type="primary",
                    key=f"del_doc_{folder_key}_{doc.name}",
                    use_container_width=True,
                ):
                    try:
                        doc.unlink()
                        st.success(f"Deleted `{doc.name}`")
                        st.rerun()
                    except OSError as e:
                        st.error(f"Could not delete: {e}")

    # Re-parse button — re-runs the parser over every file in the folder and
    # refreshes the rent roll / T-12 blocks in sources.json. Positioned at
    # the BOTTOM of the section per Brian 5/29 v2.0.25 (was at the top).
    st.markdown(
        '<div style="margin-top:14px"></div>',
        unsafe_allow_html=True,
    )
    if st.button(
        "↻ Re-parse documents into the workbench",
        key=f"reparse_{folder_key}",
        help=(
            "Re-run the parser over every file here and refresh the rent "
            "roll / T-12 data the workbench reads from sources.json."
        ),
        use_container_width=True,
    ):
        results: list[ParseResult] = []
        msgs: list[tuple[str, str]] = []
        for doc in docs:
            result = parse_uploaded_document(doc)
            results.append(result)
            level = "success" if result.blocks else (
                "warning" if result.kind == "error" else "info"
            )
            msgs.append((level, f"`{doc.name}` — {result.message}"))
        blocks = combine_blocks(results)
        if blocks:
            merge_sources(folder.path, blocks)
        st.session_state[f"parse_msgs_{folder_key}"] = msgs or [
            ("info", "No parseable documents found in this folder.")
        ]
        st.rerun()


def render_property_detail(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    """Top-level renderer for the Subject (Property Detail) tab.

    Section order locked by Brian 2026-05-29 evening:
      1. Header card (photo + address + Google Maps link + Open Folder)
      2. Sale History
      3. Property Card (renamed from "User Input Data") + Notes (side-by-side)
         — non-custom properties keep the "Property Data" label
      4. Documents (uploader; preamble copy removed)
      5. Document Auto-Ingestion (AI extraction → sources.json)

    Plus: Tax Assessment History renders conditionally (only when data exists)
    after Sale History — it's a natural extension of the historical data block.

    REMOVED per Brian 2026-05-29 (clutter — never uses):
      - "Rent Comp Calls" section
      - "Comp Call Printable Checklist" section
      - "Upload T-12s, rent rolls, OMs…" subtitle copy on Documents

    Rent roll moved to Performance & Market tab (Brian 2026-05-07 reorg —
    rent roll, comps, and market context all live together for pristine flow).
    """
    # 1. Header card (photo + address)
    _render_header(prop, folder)

    # 2. Sale History
    with section_card("Sale History"):
        _render_sales(folder)

    # 2b. Tax Assessment History (conditional — only when there's data)
    if folder is not None and (
        load_assessment_history(folder.path)
        or _gather_assessment_history_from_sales(load_sales(folder.path))
    ):
        with section_card(
            "Tax Assessment History",
            icon="🏛️",
            accent="ac",
        ):
            _render_assessment_history(folder)

    # 3. Property Card + Notes side-by-side.
    # Brian 5/29 v2.0.23: Edit moved from the top of the card to the
    # BOTTOM, labeled "Edit Property Card", styled as a text link via
    # the .v2-pc-edit-link CSS marker.
    col_card, col_notes = st.columns([3, 4])
    with col_card:
        with section_card("Property Card"):
            _render_property_card(prop, folder)
            # Bottom-of-card text link → opens the override form.
            st.markdown(
                '<div class="v2-pc-edit-link"></div>',
                unsafe_allow_html=True,
            )
            with st.popover(
                "Edit Property Card",
                use_container_width=False,
            ):
                _render_property_card_edit_form(prop, folder)
    with col_notes:
        with section_card("Notes"):
            _render_notes(prop, folder)

    # 4. Documents (no subtitle — Brian removed the upload preamble copy)
    with section_card("Documents", icon="📤"):
        _render_documents(prop, folder)

    # 5. Document Auto-Ingestion — drop T-12 / rent roll / OM, Claude extracts
    # fields into sources.json with provenance.
    from ui.document_ingest_panel import render_document_ingest_panel
    render_document_ingest_panel(prop, folder)
