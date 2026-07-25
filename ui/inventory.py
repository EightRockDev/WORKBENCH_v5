"""HR Multifamily Inventory + alerts tab.

Surfaces the comprehensive property inventory pulled by the
`va_assessors` ETL puller. Three sections:

  1. **🏠 New / Recently Reassessed** — properties whose latest-FY value
     jumped by ≥ a configurable threshold. Big jumps almost always
     correspond to ownership changes (= name change OR new construction
     coming online). Direct answer to Brian's "track new properties + name
     changes" ask.

  2. **🔍 Browse Inventory** — every parcel in the ETL with filters by
     city, class, asking-rent range, vintage, owner pattern. Sortable
     dataframe.

  3. **📊 Inventory Summary** — counts + averages by class × city.

This tab is read-only — it doesn't write to the workbench DB. The data
comes from `hampton_roads.db` populated by the assessor ETL.
"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

import config
from core.market_data import (
    get_multifamily_inventory,
    get_recent_assessment_jumps,
    is_etl_available,
)
from data.db import list_properties, list_distinct_cities
from data.property_io import (
    delete_saved_search,
    load_saved_searches,
    save_search,
)
from ui.components import section_card, v2_strip_icon
from ui.etl_notice import render_etl_missing_notice


# ---------------------------------------------------------------------------
# Address normalization for ALN ↔ Assessor cross-reference
# ---------------------------------------------------------------------------
# Assessor data ("3000 S CAPE HENRY AVE") and ALN data ("3000 S. Cape Henry
# Avenue") describe the same parcel with different formatting. Normalize
# both sides to a canonical form so address-keyed joins actually match.

# Common street-suffix and directional abbreviations. Expanded BIDIRECTIONALLY
# — both "Ave" → "avenue" and "Av" → "avenue" become the same token, so the
# join works regardless of which spelling each source uses.
_ADDRESS_ABBREVIATIONS = {
    "ave": "avenue", "avn": "avenue", "av": "avenue",
    "st": "street", "str": "street",
    "rd": "road",
    "blvd": "boulevard", "blv": "boulevard",
    "dr": "drive", "drv": "drive",
    "ln": "lane",
    "ct": "court",
    "cir": "circle",
    "pl": "place",
    "pkwy": "parkway", "pky": "parkway",
    "ter": "terrace",
    "trl": "trail",
    "hwy": "highway",
    "sq": "square",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest",
    "apt": "apartment",
    "ste": "suite",
}


def _normalize_address(addr: str | None) -> str:
    """Canonicalize an address string for cross-source matching.

    Lowercases, strips punctuation, expands common abbreviations. Returns
    "" for None / empty so dict keys don't accidentally match.
    """
    if not addr:
        return ""
    s = str(addr).lower()
    # Replace punctuation with spaces (keeps alphanumerics + whitespace)
    s = re.sub(r"[^\w\s]", " ", s)
    # Expand abbreviations token-by-token
    tokens = [_ADDRESS_ABBREVIATIONS.get(t, t) for t in s.split() if t]
    return " ".join(tokens)


# Pertinent ALN fields surfaced in the cross-referenced inventory tables.
# Keys mirror the ALN row's column names; values are display labels. Order
# here = display order in the dataframe.
_ALN_DISPLAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("name",                "ALN Property"),
    ("asset_class",         "ALN Class"),
    ("units",               "ALN Units"),
    ("year_built",          "ALN Built"),
    ("management_company",  "ALN Mgmt Co"),
    ("owner",               "ALN Owner"),
    ("manager",             "ALN On-site Mgr"),
    ("occupancy_pct",       "ALN Occ"),
    ("avg_rent",            "ALN Avg Rent"),
    ("submarket",           "ALN Submarket"),
)


@st.cache_data(ttl=600, show_spinner=False)
def _build_aln_address_index() -> dict[tuple[str, str], dict[str, str | float | int | None]]:
    """Build a lookup of (city_lower, normalized_address) → full ALN row.

    The value is a dict containing every field listed in `_ALN_DISPLAY_FIELDS`
    plus the property_id (for potential drill-through), so each cross-ref'd
    inventory row can surface the property name, A/B/C class, mgmt co,
    owner, on-site manager, occupancy, avg rent, and submarket without
    re-querying SQLite.

    Cached for 10 minutes. The ALN property list is ~2,500 rows so this
    builds in well under 100ms; the cache just avoids redoing the work
    every time the user tweaks a filter.
    """
    index: dict[tuple[str, str], dict[str, str | float | int | None]] = {}
    aln_props = list_properties(limit=10_000)
    for p in aln_props:
        addr_norm = _normalize_address(p.get("address"))
        city = (p.get("city") or "").strip().lower()
        name = p.get("name") or ""
        if not addr_norm or not name:
            continue
        record = {col: p.get(col) for col, _label in _ALN_DISPLAY_FIELDS}
        record["property_id"] = p.get("property_id")
        # Don't overwrite — first one wins on (rare) duplicate normalized addresses
        index.setdefault((city, addr_norm), record)
        # Also write a no-city fallback key so we can match across slight
        # city-name discrepancies (e.g., assessor lists "Va Beach", ALN
        # lists "Virginia Beach").
        index.setdefault(("", addr_norm), record)
    return index


def _lookup_aln_record(
    address: str | None,
    city: str | None,
    index: dict[tuple[str, str], dict[str, str | float | int | None]],
) -> dict[str, str | float | int | None]:
    """Return the full ALN record for an assessor (address, city). Empty dict
    when no match — caller renders '—' for each missing field."""
    addr_norm = _normalize_address(address)
    if not addr_norm:
        return {}
    city_lc = (city or "").strip().lower()
    # Try city-specific match first (more accurate)
    rec = index.get((city_lc, addr_norm))
    if rec:
        return rec
    # Fall back to no-city match
    return index.get(("", addr_norm), {}) or {}


def _format_aln_field(col: str, value: str | float | int | None) -> str:
    """Render an ALN field for display. Type-aware so dollars/percents/ints
    all read correctly in the dataframe."""
    if value is None or value == "":
        return "—"
    if col in ("units", "year_built"):
        try:
            iv = int(value)
            return str(iv) if iv else "—"
        except (TypeError, ValueError):
            return str(value)
    if col == "occupancy_pct":
        try:
            f = float(value)
            return f"{f*100:.0f}%" if f <= 1.0 else f"{f:.0f}%"
        except (TypeError, ValueError):
            return str(value)
    if col == "avg_rent":
        try:
            f = float(value)
            return f"${f:,.0f}" if f else "—"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _resolve_aln_records(df: pd.DataFrame) -> list[dict[str, str | float | int | None]]:
    """For each row in `df`, look up the ALN record by (address, city). Returns
    a parallel list — empty dict for rows without a match. Use this when you
    need to FILTER on raw ALN values (numeric occupancy, units, etc.)
    BEFORE the dataframe is formatted for display.
    """
    aln_index = _build_aln_address_index()
    addresses = df["address"].fillna("") if "address" in df.columns else [""] * len(df)
    cities = df["city"].fillna("") if "city" in df.columns else [""] * len(df)
    return [
        _lookup_aln_record(addr, city, aln_index)
        for addr, city in zip(addresses, cities)
    ]


def _add_aln_columns(
    display: pd.DataFrame,
    df: pd.DataFrame,
    aln_records: list[dict[str, str | float | int | None]] | None = None,
) -> tuple[pd.DataFrame, list[bool]]:
    """Insert the ALN cross-reference columns into a display dataframe.

    Pass `aln_records` if you've already resolved them (e.g. via
    `_resolve_aln_records`) so we don't re-query. Returns the new
    dataframe AND a parallel list of bool match flags so callers can
    compute match-rate summaries.
    """
    if aln_records is None:
        aln_records = _resolve_aln_records(df)

    matched: list[bool] = [bool(r) for r in aln_records]
    columns: dict[str, list[str]] = {label: [] for _col, label in _ALN_DISPLAY_FIELDS}
    for rec in aln_records:
        for col, label in _ALN_DISPLAY_FIELDS:
            columns[label].append(_format_aln_field(col, rec.get(col)) if rec else "—")

    # Insert ALN columns immediately after "Address" so the cross-reference
    # reads naturally left-to-right.
    out = display.copy()
    insert_at = (
        list(out.columns).index("Address") + 1
        if "Address" in out.columns
        else len(out.columns)
    )
    for i, (_col, label) in enumerate(_ALN_DISPLAY_FIELDS):
        out.insert(insert_at + i, label, columns[label])
    return out, matched


# Class-code shortcuts. Norfolk uses these prefixes; other cities will
# reuse them where possible (VA standard residential class codes).
EIGHT_ROCK_CLASSES = {
    "401 Apartment 5–11 Designed":   "401",
    "402 Apartment 5–11 Converted":  "402",
    "403 Apartment 12–48 Designed":  "403",
    "404 Apartment 12–48 Converted": "404",
    "405 Apartment 49+ Low Rise ⭐": "405",
    "406 Apartment 49+ Mid Rise":    "406",
    "407 Apartment 49+ High Rise":   "407",
}


def _format_money_col(s: pd.Series) -> pd.Series:
    return s.apply(
        lambda v: f"${float(v):,.0f}"
        if pd.notna(v) and v != 0 else "—"
    )


def _render_alerts_section() -> None:
    """🏠 New / Recently Reassessed — properties that just had a big
    assessment jump. The flagging logic uses the latest two FYs in the
    `va_assessment_history` table (so this auto-updates on each weekly /
    monthly ETL refresh).
    """
    c = config.COLORS
    with section_card(
        "New / Recently Reassessed Properties",
        icon="🏠",
        subtitle=(
            "Properties whose latest fiscal-year assessed value jumped sharply "
            "vs. the prior FY. Big jumps almost always = ownership change "
            "(name change candidate) OR new construction coming online. "
            "Threshold defaults to ≥ 20%."
        ),
    ):
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            city_options = ["All HR Cities", "Norfolk", "Virginia Beach",
                            "Chesapeake", "Portsmouth", "Hampton",
                            "Newport News", "Suffolk"]
            city_choice = st.selectbox(
                "Filter by city",
                options=city_options,
                index=0,
                key="inv_alerts_city",
            )
        with col2:
            threshold_pct = st.slider(
                "Jump threshold",
                min_value=10, max_value=100, value=20, step=5,
                key="inv_alerts_threshold",
                help="Show properties whose latest FY assessed value rose by at "
                     "least this percentage vs. the prior FY.",
            )
        with col3:
            limit = st.number_input(
                "Max results",
                min_value=10, max_value=500, value=50, step=10,
                key="inv_alerts_limit",
            )

        city_filter = None if city_choice == "All HR Cities" else city_choice
        df = get_recent_assessment_jumps(
            city=city_filter,
            min_pct=threshold_pct / 100.0,
            limit=int(limit),
        )

        if df.empty:
            st.info(
                "No properties match. Either the ETL hasn't been pulled yet, or "
                "no parcels jumped by ≥ this threshold. Try lowering the slider."
            )
            return

        # Format display columns. Assessor `owner`/`year_built`/`class` are
        # prefixed "Asr " so they don't collide with the ALN-side columns
        # injected by `_add_aln_columns` below.
        display = pd.DataFrame({
            "City": df["city"],
            "Address": df["address"].fillna(""),
            "Asr Owner": df["owner"].fillna("—"),
            "Asr Class": df["class_description"].fillna("—"),
            "Asr Built": df["year_built"].apply(
                lambda v: int(v) if pd.notna(v) and v else "—"
            ),
            "Prior FY": df["prior_fy"].apply(lambda v: f"FY{int(v)}"),
            "Prior $": _format_money_col(df["prior_value"]),
            "Latest FY": df["latest_fy"].apply(lambda v: f"FY{int(v)}"),
            "Latest $": _format_money_col(df["latest_value"]),
            "Jump": df["jump_pct"].apply(lambda v: f"+{v*100:.1f}%"),
            "Parcel": df["parcel_id"].fillna(""),
            "GPIN": df["gpin"].fillna(""),
        })

        # Enrich with ALN cross-reference (property name, A/B/C class, mgmt
        # co, owner, on-site manager, occupancy, avg rent, submarket).
        # Reassessment jumps are most-actionable when paired with the ALN
        # marketing context — a 30% jump on a managed Class C property
        # owned by a known operator is a different signal than the same
        # jump on an unmanaged parcel.
        display, matched = _add_aln_columns(display, df)
        n_total = len(display)
        n_matched = sum(1 for m in matched if m)
        match_pct = (n_matched / n_total * 100) if n_total else 0

        st.markdown(
            f'<div style="color:{c["tx2"]};font-size:13px;margin-bottom:6px">'
            f'<b>{n_total} properties</b> with ≥ {threshold_pct}% '
            f'reassessment jump · sorted largest jump first · '
            f'<span style="color:{c["src_aln"]}">'
            f'{n_matched:,} matched to ALN ({match_pct:.0f}%)</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(display, use_container_width=True, hide_index=True, height=400)


# Every widget key in the Browse-Inventory filter panel. Listed here so
# saved searches (load / save / reset) can iterate the full set without
# missing any filter when new ones are added.
_BROWSE_FILTER_KEYS: tuple[str, ...] = (
    # Location
    "inv_browse_city",
    "inv_browse_address",
    "inv_browse_submarket",
    # Assessor
    "inv_browse_class",
    "inv_browse_owner",
    "inv_browse_yr_min",
    "inv_browse_yr_max",
    "inv_browse_acre_min",
    "inv_browse_acre_max",
    "inv_browse_land_min",
    "inv_browse_land_max",
    "inv_browse_bldg_min",
    "inv_browse_bldg_max",
    "inv_browse_total_min",
    "inv_browse_total_max",
    "inv_browse_sale_min",
    "inv_browse_sale_max",
    "inv_browse_sale_since",
    # ALN cross-reference
    "inv_browse_aln_match",
    "inv_browse_aln_property",
    "inv_browse_aln_class",
    "inv_browse_aln_units_min",
    "inv_browse_aln_units_max",
    "inv_browse_aln_mgmt",
    "inv_browse_aln_owner",
    "inv_browse_aln_onsite",
    "inv_browse_aln_occ_min",
    "inv_browse_aln_rent_min",
    "inv_browse_aln_rent_max",
    "inv_browse_aln_yr_min",
)

_BROWSE_SECTION_ID = "inventory_browse"


def _apply_saved_search_if_pending(saved: dict[str, dict]) -> None:
    """Detect a saved-search dropdown change and write the saved values into
    session_state BEFORE the filter widgets are constructed. Streamlit's
    session_state-vs-widget rules require the writes to land first; otherwise
    the widgets ignore the saved values for one render."""
    selected = st.session_state.get("inv_saved_search_choice")
    if not selected or selected == "(none)":
        return
    if selected not in saved:
        return
    last_loaded = st.session_state.get("_inv_last_loaded_search")
    if last_loaded == selected:
        return
    # Apply the saved widget state, then mark this search as the current one.
    for k, v in saved[selected].items():
        if k in _BROWSE_FILTER_KEYS:
            st.session_state[k] = v
    st.session_state["_inv_last_loaded_search"] = selected
    st.rerun()


def _render_saved_search_bar() -> str:
    """Saved-search dropdown + Save/Delete controls. Returns the currently
    selected search name (or "(none)")."""
    c = config.COLORS
    saved = load_saved_searches(_BROWSE_SECTION_ID)

    # ---- Apply pending dropdown selection ----
    # Save / Delete / Reset handlers set `_inv_pending_select` instead of
    # writing directly to `inv_saved_search_choice` (Streamlit forbids
    # writing to a widget's key AFTER that widget has been instantiated
    # this run; the handlers run later, so they queue the change here for
    # the next render to apply BEFORE the widget renders again).
    pending = st.session_state.pop("_inv_pending_select", None)
    if pending is not None:
        st.session_state["inv_saved_search_choice"] = pending

    # Apply pending load BEFORE rendering filter widgets below.
    _apply_saved_search_if_pending(saved)

    options = ["(none)"] + sorted(saved.keys())
    col_load, col_save_name, col_save_btn, col_del = st.columns([3, 3, 1, 1])
    with col_load:
        selected = st.selectbox(
            "💾 Saved searches",
            options=options,
            index=0,
            key="inv_saved_search_choice",
            help="Load a previously-saved combination of filters. Save the "
                 "current filters by typing a name on the right and "
                 "clicking 💾.",
        )
    with col_save_name:
        st.text_input(
            "Save current filters as…",
            key="inv_save_name",
            placeholder="e.g. Norfolk Class C 100+ units",
            label_visibility="visible",
        )
    with col_save_btn:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        save_clicked = st.button("💾", key="inv_save_btn", help="Save current filters")
    with col_del:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        del_disabled = (selected == "(none)")
        del_clicked = st.button(
            "🗑️", key="inv_del_btn",
            disabled=del_disabled,
            help="Delete the currently-loaded saved search",
        )

    if save_clicked:
        name = (st.session_state.get("inv_save_name") or "").strip()
        if not name:
            st.warning("Type a name first, then click 💾 to save.")
        else:
            state = {
                k: st.session_state.get(k)
                for k in _BROWSE_FILTER_KEYS
                if k in st.session_state
            }
            save_search(_BROWSE_SECTION_ID, name, state)
            # `inv_save_name` and `inv_saved_search_choice` are widget keys
            # that have already rendered this run, so we can't __setitem__
            # them directly. Pop to reset, queue the new dropdown selection
            # via the `_inv_pending_select` sentinel for the next render.
            st.session_state.pop("inv_save_name", None)
            st.session_state["_inv_last_loaded_search"] = name
            st.session_state["_inv_pending_select"] = name
            st.success(f"✓ Saved '{name}'")
            st.rerun()
    if del_clicked and not del_disabled:
        if delete_saved_search(_BROWSE_SECTION_ID, selected):
            st.session_state.pop("_inv_last_loaded_search", None)
            # Same Streamlit constraint as above — queue the dropdown reset
            # via sentinel rather than writing the widget key directly.
            st.session_state["_inv_pending_select"] = "(none)"
            st.success(f"✓ Deleted '{selected}'")
            st.rerun()

    return selected


def _render_browse_section() -> None:
    """🔍 Browse — every multifamily parcel in the ETL inventory.

    Layout:
      1. Saved-search bar (load / save / delete named filter presets)
      2. Filter panel (expander) — every column in the table is filterable
      3. Reset button
      4. Result count + ALN match-rate
      5. Dataframe
    """
    c = config.COLORS
    with section_card(
        "Browse Multifamily Inventory",
        icon="🔍",
        subtitle=(
            "Every multifamily property in the ALN library across the target "
            "states (VA·NC·SC·GA·TN). Assessor enrichment shows where city "
            "open-data feeds are wired in. Filter below; save useful combos "
            "as a named search for one-click recall."
        ),
    ):
        # ---- Saved search controls ----
        _render_saved_search_bar()

        # ---- Filter panel ----
        with st.expander(v2_strip_icon("⚙️ Filters"), expanded=True):
            # ---- Location ----
            st.markdown(
                f'<div style="color:{c["tx2"]};font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.7px;font-weight:700;'
                f'margin-bottom:4px">📍 Location</div>',
                unsafe_allow_html=True,
            )
            l1, l2, l3 = st.columns(3)
            with l1:
                # Data-driven city list across the full multi-state ALN
                # library (Brian 5/30 expansion). "All cities" = no filter.
                city_options = ["All cities"] + list_distinct_cities()
                st.selectbox(
                    "City", options=city_options, index=0,
                    key="inv_browse_city",
                )
            with l2:
                st.text_input(
                    "Address contains",
                    key="inv_browse_address",
                    placeholder="Cape Henry, Virginia Beach Blvd...",
                )
            with l3:
                st.text_input(
                    "ALN Submarket contains",
                    key="inv_browse_submarket",
                    placeholder="Norfolk Central, Hampton North...",
                )

            # ---- Assessor data ----
            st.markdown(
                f'<div style="color:{c["tx2"]};font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.7px;font-weight:700;'
                f'margin-top:14px;margin-bottom:4px">'
                f'🏛️ Assessor data</div>',
                unsafe_allow_html=True,
            )
            a1, a2, a3 = st.columns(3)
            with a1:
                class_options = (
                    ["All multifamily classes (401–407)"]
                    + list(EIGHT_ROCK_CLASSES.keys())
                )
                st.selectbox(
                    "Class code",
                    options=class_options, index=0,
                    key="inv_browse_class",
                )
            with a2:
                st.text_input(
                    "Asr Owner contains",
                    key="inv_browse_owner",
                    placeholder="LLC, Trust, Realty...",
                )
            with a3:
                st.slider(
                    "Year built (range)",
                    min_value=1900, max_value=2026, value=(1900, 2026),
                    step=1, key="inv_browse_yr_min",
                    help="Min and max year-built filter (assessor field).",
                )

            a4, a5, a6 = st.columns(3)
            with a4:
                st.number_input(
                    "Acreage min", min_value=0.0, max_value=100.0,
                    value=0.0, step=0.1, key="inv_browse_acre_min",
                )
                st.number_input(
                    "Acreage max", min_value=0.0, max_value=100.0,
                    value=100.0, step=0.1, key="inv_browse_acre_max",
                )
            with a5:
                st.number_input(
                    "Total assessed $ min",
                    min_value=0, max_value=200_000_000, value=0,
                    step=100_000, key="inv_browse_total_min",
                )
                st.number_input(
                    "Total assessed $ max",
                    min_value=0, max_value=200_000_000, value=200_000_000,
                    step=100_000, key="inv_browse_total_max",
                )
            with a6:
                st.number_input(
                    "Land $ min",
                    min_value=0, max_value=200_000_000, value=0,
                    step=50_000, key="inv_browse_land_min",
                )
                st.number_input(
                    "Building $ min",
                    min_value=0, max_value=200_000_000, value=0,
                    step=50_000, key="inv_browse_bldg_min",
                )
            # Spread the second row of dollar maxes so it doesn't pile up:
            # land_max and bldg_max get their own row with a sale-since row.
            a7, a8, a9 = st.columns(3)
            with a7:
                st.number_input(
                    "Land $ max",
                    min_value=0, max_value=200_000_000, value=200_000_000,
                    step=50_000, key="inv_browse_land_max",
                )
            with a8:
                st.number_input(
                    "Building $ max",
                    min_value=0, max_value=200_000_000, value=200_000_000,
                    step=50_000, key="inv_browse_bldg_max",
                )
            with a9:
                st.text_input(
                    "Last sale on/after (YYYY-MM-DD)",
                    key="inv_browse_sale_since",
                    placeholder="e.g. 2020-01-01",
                    help="Empty = no date filter.",
                )

            a10, a11, _a12 = st.columns(3)
            with a10:
                st.number_input(
                    "Sale $ min",
                    min_value=0, max_value=200_000_000, value=0,
                    step=100_000, key="inv_browse_sale_min",
                )
            with a11:
                st.number_input(
                    "Sale $ max",
                    min_value=0, max_value=200_000_000, value=200_000_000,
                    step=100_000, key="inv_browse_sale_max",
                )

            # ---- ALN cross-reference ----
            st.markdown(
                f'<div style="color:{c["tx2"]};font-size:11px;'
                f'text-transform:uppercase;letter-spacing:0.7px;font-weight:700;'
                f'margin-top:14px;margin-bottom:4px">'
                f'🥇 ALN cross-reference</div>',
                unsafe_allow_html=True,
            )
            n1, n2, n3 = st.columns(3)
            with n1:
                st.radio(
                    "ALN match status",
                    options=["All", "Matched only", "Unmatched only"],
                    horizontal=True, key="inv_browse_aln_match",
                )
            with n2:
                st.multiselect(
                    "ALN Class (A/B/C/D)",
                    options=["A", "B", "C", "D"],
                    key="inv_browse_aln_class",
                )
            with n3:
                st.text_input(
                    "ALN Property name contains",
                    key="inv_browse_aln_property",
                    placeholder="Crossroads, Andover...",
                )

            n4, n5, n6 = st.columns(3)
            with n4:
                st.text_input(
                    "ALN Mgmt Co contains",
                    key="inv_browse_aln_mgmt",
                    placeholder="Drucker, Lawson...",
                )
            with n5:
                st.text_input(
                    "ALN Owner contains",
                    key="inv_browse_aln_owner",
                    placeholder="Operating-entity name...",
                )
            with n6:
                st.text_input(
                    "ALN On-site Mgr contains",
                    key="inv_browse_aln_onsite",
                    placeholder="Person on-site / phone log...",
                )

            n7, n8, n9 = st.columns(3)
            with n7:
                st.slider(
                    "ALN Units (range)",
                    min_value=0, max_value=600, value=(0, 600),
                    step=5, key="inv_browse_aln_units_min",
                )
            with n8:
                st.slider(
                    "ALN Year built (min)",
                    min_value=1900, max_value=2026, value=1900,
                    step=1, key="inv_browse_aln_yr_min",
                )
            with n9:
                st.slider(
                    "ALN Occupancy (min %)",
                    min_value=0, max_value=100, value=0,
                    step=1, key="inv_browse_aln_occ_min",
                )

            n10, n11, _n12 = st.columns(3)
            with n10:
                st.number_input(
                    "ALN Avg Rent $ min",
                    min_value=0, max_value=10_000, value=0, step=25,
                    key="inv_browse_aln_rent_min",
                )
            with n11:
                st.number_input(
                    "ALN Avg Rent $ max",
                    min_value=0, max_value=10_000, value=10_000, step=25,
                    key="inv_browse_aln_rent_max",
                )

            # ---- Reset ----
            st.markdown('<div style="margin-top:10px"></div>', unsafe_allow_html=True)
            if st.button(
                "↺ Reset filters",
                key="inv_browse_reset",
                help="Clear every filter back to its default.",
            ):
                # Pop all filter widget keys (allowed — pop is removal,
                # not __setitem__), then queue the dropdown reset via the
                # `_inv_pending_select` sentinel so the saved-search bar
                # picks it up at the top of the next render BEFORE the
                # dropdown widget instantiates again.
                for k in _BROWSE_FILTER_KEYS:
                    st.session_state.pop(k, None)
                st.session_state.pop("_inv_last_loaded_search", None)
                st.session_state["_inv_pending_select"] = "(none)"
                st.rerun()

        # ---- Resolve the SQL-side filters (city + class) ----
        city_choice = st.session_state.get("inv_browse_city", "All cities")
        class_choice = st.session_state.get(
            "inv_browse_class", "All multifamily classes (401–407)"
        )
        city_filter = None if city_choice in ("All cities", "All HR Cities") else city_choice
        class_filter = (
            EIGHT_ROCK_CLASSES.get(class_choice)
            if class_choice in EIGHT_ROCK_CLASSES else None
        )

        df = get_multifamily_inventory(
            city=city_filter, class_filter=class_filter, limit=10_000,
        )

        if df.empty:
            render_etl_missing_notice("the assessor inventory")
            return

        # ---- Apply assessor-side filters ----
        df = _apply_assessor_filters(df)

        # ---- Resolve ALN records (raw, before formatting) ----
        aln_records = _resolve_aln_records(df)

        # ---- Apply ALN-side filters (operates on raw records + df) ----
        df, aln_records = _apply_aln_filters(df, aln_records)

        if df.empty:
            st.info("No properties match these filters. Try broadening or resetting.")
            return

        # ---- Build display ----
        display = pd.DataFrame({
            "City": df["city"].values,
            "Address": df["address"].fillna("").values,
            "Asr Owner": df["owner"].fillna("—").values,
            "Asr Class": df["class_description"].fillna("—").values,
            "Asr Built": df["year_built"].apply(
                lambda v: int(v) if pd.notna(v) and v else "—"
            ).values,
            "Acreage": df["acreage"].apply(
                lambda v: f"{float(v):.2f}" if pd.notna(v) and v else "—"
            ).values,
            "Land $": _format_money_col(df.get("land_value", pd.Series([None] * len(df)))).values,
            "Building $": _format_money_col(df.get("improvement_value", pd.Series([None] * len(df)))).values,
            "Total $": _format_money_col(df["assessed_value"]).values,
            "Last Sale": df.get("last_sale_date", pd.Series([None] * len(df))).apply(
                lambda v: str(v)[:10] if pd.notna(v) else "—"
            ).values,
            "Sale $": _format_money_col(df.get("last_sale_price", pd.Series([None] * len(df)))).values,
            "Parcel": df["parcel_id"].fillna("").values,
            "GPIN": df["gpin"].fillna("").values,
        })
        display, matched = _add_aln_columns(display, df, aln_records=aln_records)

        n = len(display)
        total_value = df["assessed_value"].fillna(0).astype(float).sum()
        n_matched = sum(1 for m in matched if m)
        match_pct = (n_matched / n * 100) if n else 0
        st.markdown(
            f'<div style="color:{c["tx2"]};font-size:13px;margin-bottom:6px">'
            f'<b>{n:,} properties</b> shown · combined assessed value '
            f'<b>${total_value:,.0f}</b> · '
            f'<span style="color:{c["src_aln"]}">'
            f'{n_matched:,} matched to ALN ({match_pct:.0f}%)</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(display, use_container_width=True, hide_index=True, height=500)


def _apply_assessor_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Apply every assessor-side filter from session_state to `df`. Returns
    a filtered copy. Each filter checks whether its widget value differs
    from the no-op default before applying — so absent filters are cheap.
    """
    address_q = (st.session_state.get("inv_browse_address") or "").strip()
    if address_q:
        df = df[df["address"].astype(str).str.contains(address_q, case=False, na=False)]

    owner_q = (st.session_state.get("inv_browse_owner") or "").strip()
    if owner_q:
        df = df[df["owner"].astype(str).str.contains(owner_q, case=False, na=False)]

    yr_range = st.session_state.get("inv_browse_yr_min")
    if isinstance(yr_range, tuple) and yr_range != (1900, 2026):
        ymin, ymax = yr_range
        years = df["year_built"].fillna(0).astype(int)
        df = df[(years >= ymin) & (years <= ymax)]

    acre_min = float(st.session_state.get("inv_browse_acre_min") or 0.0)
    acre_max = float(st.session_state.get("inv_browse_acre_max") or 100.0)
    if acre_min > 0.0 or acre_max < 100.0:
        ac = df["acreage"].fillna(0).astype(float)
        df = df[(ac >= acre_min) & (ac <= acre_max)]

    total_min = int(st.session_state.get("inv_browse_total_min") or 0)
    total_max = int(st.session_state.get("inv_browse_total_max") or 200_000_000)
    if total_min > 0 or total_max < 200_000_000:
        tv = df["assessed_value"].fillna(0).astype(float)
        df = df[(tv >= total_min) & (tv <= total_max)]

    land_min = int(st.session_state.get("inv_browse_land_min") or 0)
    land_max = int(st.session_state.get("inv_browse_land_max") or 200_000_000)
    if "land_value" in df.columns and (land_min > 0 or land_max < 200_000_000):
        lv = df["land_value"].fillna(0).astype(float)
        df = df[(lv >= land_min) & (lv <= land_max)]

    bldg_min = int(st.session_state.get("inv_browse_bldg_min") or 0)
    bldg_max = int(st.session_state.get("inv_browse_bldg_max") or 200_000_000)
    if "improvement_value" in df.columns and (bldg_min > 0 or bldg_max < 200_000_000):
        bv = df["improvement_value"].fillna(0).astype(float)
        df = df[(bv >= bldg_min) & (bv <= bldg_max)]

    sale_min = int(st.session_state.get("inv_browse_sale_min") or 0)
    sale_max = int(st.session_state.get("inv_browse_sale_max") or 200_000_000)
    if "last_sale_price" in df.columns and (sale_min > 0 or sale_max < 200_000_000):
        sv = df["last_sale_price"].fillna(0).astype(float)
        df = df[(sv >= sale_min) & (sv <= sale_max)]

    sale_since = (st.session_state.get("inv_browse_sale_since") or "").strip()
    if sale_since and "last_sale_date" in df.columns:
        # Lexicographic compare on YYYY-MM-DD strings is fine — ETL stores
        # dates in ISO format. Skip the row if its date is empty / older.
        df = df[df["last_sale_date"].fillna("").astype(str).str[:10] >= sale_since]

    return df


def _apply_aln_filters(
    df: pd.DataFrame,
    aln_records: list[dict],
) -> tuple[pd.DataFrame, list[dict]]:
    """Apply ALN-side filters using the raw resolved records. Returns the
    filtered df and the parallel-trimmed aln_records list."""
    match_choice = st.session_state.get("inv_browse_aln_match", "All")
    if match_choice == "Matched only":
        keep = [bool(r) for r in aln_records]
    elif match_choice == "Unmatched only":
        keep = [not bool(r) for r in aln_records]
    else:
        keep = [True] * len(aln_records)

    # Property contains
    prop_q = (st.session_state.get("inv_browse_aln_property") or "").strip().lower()
    if prop_q:
        for i, r in enumerate(aln_records):
            if not r:
                keep[i] = False
                continue
            if prop_q not in str(r.get("name") or "").lower():
                keep[i] = False

    # Class multiselect
    class_sel = st.session_state.get("inv_browse_aln_class") or []
    if class_sel:
        for i, r in enumerate(aln_records):
            if not r or str(r.get("asset_class") or "") not in class_sel:
                keep[i] = False

    # Mgmt Co
    mgmt_q = (st.session_state.get("inv_browse_aln_mgmt") or "").strip().lower()
    if mgmt_q:
        for i, r in enumerate(aln_records):
            if not r or mgmt_q not in str(r.get("management_company") or "").lower():
                keep[i] = False

    # ALN Owner
    owner_q = (st.session_state.get("inv_browse_aln_owner") or "").strip().lower()
    if owner_q:
        for i, r in enumerate(aln_records):
            if not r or owner_q not in str(r.get("owner") or "").lower():
                keep[i] = False

    # On-site Mgr
    onsite_q = (st.session_state.get("inv_browse_aln_onsite") or "").strip().lower()
    if onsite_q:
        for i, r in enumerate(aln_records):
            if not r or onsite_q not in str(r.get("manager") or "").lower():
                keep[i] = False

    # Units range
    units_range = st.session_state.get("inv_browse_aln_units_min")
    if isinstance(units_range, tuple) and units_range != (0, 600):
        umin, umax = units_range
        for i, r in enumerate(aln_records):
            if not r:
                keep[i] = False
                continue
            try:
                u = int(r.get("units") or 0)
            except (TypeError, ValueError):
                u = 0
            if not (umin <= u <= umax):
                keep[i] = False

    # ALN Year built min
    aln_yr_min = int(st.session_state.get("inv_browse_aln_yr_min") or 1900)
    if aln_yr_min > 1900:
        for i, r in enumerate(aln_records):
            if not r:
                keep[i] = False
                continue
            try:
                yr = int(r.get("year_built") or 0)
            except (TypeError, ValueError):
                yr = 0
            if yr < aln_yr_min:
                keep[i] = False

    # Occupancy min
    occ_min = int(st.session_state.get("inv_browse_aln_occ_min") or 0)
    if occ_min > 0:
        for i, r in enumerate(aln_records):
            if not r:
                keep[i] = False
                continue
            try:
                occ = float(r.get("occupancy_pct") or 0.0)
                if occ <= 1.0:
                    occ = occ * 100.0
            except (TypeError, ValueError):
                occ = 0.0
            if occ < occ_min:
                keep[i] = False

    # Rent range
    rent_min = int(st.session_state.get("inv_browse_aln_rent_min") or 0)
    rent_max = int(st.session_state.get("inv_browse_aln_rent_max") or 10_000)
    if rent_min > 0 or rent_max < 10_000:
        for i, r in enumerate(aln_records):
            if not r:
                keep[i] = False
                continue
            try:
                rt = float(r.get("avg_rent") or 0.0)
            except (TypeError, ValueError):
                rt = 0.0
            if not (rent_min <= rt <= rent_max):
                keep[i] = False

    # Submarket contains
    sub_q = (st.session_state.get("inv_browse_submarket") or "").strip().lower()
    if sub_q:
        for i, r in enumerate(aln_records):
            if not r or sub_q not in str(r.get("submarket") or "").lower():
                keep[i] = False

    if all(keep):
        return df, aln_records

    keep_arr = pd.Series(keep, index=df.index)
    new_df = df[keep_arr].reset_index(drop=True)
    new_records = [r for r, k in zip(aln_records, keep) if k]
    return new_df, new_records


def _render_summary_section() -> None:
    """Inventory Summary — counts × class × city."""
    c = config.COLORS
    df = get_multifamily_inventory(limit=10_000)
    if df.empty:
        return

    with section_card(
        "Inventory Summary by Class",
        icon=config.EXCEL_ICON_HTML,
        accent="ac",
    ):
        # Group by class_description
        summary = df.groupby("class_description").agg(
            count=("parcel_id", "count"),
            avg_assessed=("assessed_value", "mean"),
            median_assessed=("assessed_value", "median"),
            total_assessed=("assessed_value", "sum"),
            avg_year_built=("year_built", "mean"),
        ).reset_index()
        summary = summary.sort_values("class_description")
        summary["avg_assessed"] = summary["avg_assessed"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        )
        summary["median_assessed"] = summary["median_assessed"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        )
        summary["total_assessed"] = summary["total_assessed"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
        )
        summary["avg_year_built"] = summary["avg_year_built"].apply(
            lambda v: int(v) if pd.notna(v) else "—"
        )
        summary.columns = ["Class", "Count", "Avg $", "Median $", "Total $", "Avg Built"]
        st.dataframe(summary, use_container_width=True, hide_index=True)

    with section_card(
        "Inventory Summary by City",
        icon=config.EXCEL_ICON_HTML,
        accent="ac",
    ):
        by_city = df.groupby("city").agg(
            count=("parcel_id", "count"),
            avg_assessed=("assessed_value", "mean"),
            total_assessed=("assessed_value", "sum"),
        ).reset_index()
        by_city = by_city.sort_values("count", ascending=False)
        by_city["avg_assessed"] = by_city["avg_assessed"].apply(
            lambda v: f"${v:,.0f}"
        )
        by_city["total_assessed"] = by_city["total_assessed"].apply(
            lambda v: f"${v:,.0f}"
        )
        by_city.columns = ["City", "Count", "Avg $", "Total $"]
        st.dataframe(by_city, use_container_width=True, hide_index=True)

        # Eight Rock sweet-spot callout
        sweet = df[df["class_description"].fillna("").str.startswith("405")]
        if not sweet.empty:
            st.markdown(
                f'<div style="background:{c["bg3"]};border-left:3px solid {c["ac"]};'
                f'border-radius:4px;padding:10px 14px;margin-top:10px;color:{c["tx"]};'
                f'font-size:13px;line-height:1.5">'
                f'⭐ <b>Eight Rock sweet spot</b>: <b>{len(sweet):,} Class 405 '
                f'(49+ Low Rise) properties</b> across HR · combined assessed '
                f'value <b>${sweet["assessed_value"].sum():,.0f}</b>. '
                f'Browse them above with the Class filter set to "405 Apartment '
                f'49+ Low Rise ⭐".</div>',
                unsafe_allow_html=True,
            )


def render_inventory(prop: dict | None = None) -> None:
    """Top-level renderer for the Inventory & Alerts tab.

    Read-only view of the HR multifamily ETL data. Doesn't depend on
    `prop` — works the same regardless of which property is selected
    in the sidebar.
    """
    if not is_etl_available():
        render_etl_missing_notice("the multifamily inventory and alerts")
        return

    _render_alerts_section()
    _render_browse_section()
    _render_summary_section()
