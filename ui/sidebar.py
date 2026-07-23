"""Sidebar property selector + filters.

Defaults are tuned for Brian's daily workflow: Class C, Hampton Roads, 20-400
units. He can broaden any filter to find properties outside that target.

Selection is persisted via `st.session_state["selected_property_id"]`. Tabs
read that key to know which property to render.
"""

from __future__ import annotations

from typing import Any

import requests
import streamlit as st

import config
from data.db import (
    TARGET_STATES,
    city_counts_for_state,
    ensure_db_synced,
    list_distinct_states,
    list_management_companies,
    list_properties,
    upsert_property,
)
from data.property_io import add_custom_property, load_favorites
from ui.components import v2_strip_icon


@st.cache_data(ttl=86400, show_spinner=False)
def _lookup_zip(zip_code: str) -> dict[str, Any] | None:
    """Look up city / state / lat / lng for a US ZIP via zippopotam.us (free API).

    Cached for 24h so repeated ZIPs don't hit the API. Returns None on any
    error (offline, invalid ZIP, etc.) — caller falls back to manual entry.
    """
    if not zip_code or not zip_code.isdigit() or len(zip_code) != 5:
        return None
    try:
        r = requests.get(
            f"https://api.zippopotam.us/us/{zip_code}",
            timeout=5,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        place = data["places"][0]
        return {
            "city": place["place name"],
            "state": place["state abbreviation"],
            "latitude": float(place["latitude"]),
            "longitude": float(place["longitude"]),
        }
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


# Eight Rock home-base cities (Hampton Roads MSA). Kept as a convenience
# preset, but the search is no longer LIMITED to these — Brian 5/30 expanded
# the footprint to VA·NC·SC·GA·TN and the filter is now data-driven from the
# DB's actual states/cities.
HAMPTON_ROADS_CITIES: tuple[str, ...] = (
    "Norfolk",
    "Virginia Beach",
    "Chesapeake",
    "Hampton",
    "Newport News",
    "Portsmouth",
    "Suffolk",
)

# State dropdown presets
STATE_PRESET_TARGET = "All target states (VA·NC·SC·GA·TN)"
STATE_PRESET_ALL = "All states"
# City dropdown presets
CITY_PRESET_ALL = "All cities"
CITY_PRESET_HR = "All Hampton Roads"

# Unit-range presets — keys are dropdown labels, values are (min, max) tuples
UNITS_PRESETS: dict[str, tuple[int, int]] = {
    "20–400 (Class C target)": (20, 400),
    "20–100 (small)":          (20, 100),
    "100–200":                 (100, 200),
    "200–400":                 (200, 400),
    "400+":                    (400, 1_000_000),
    "All sizes":               (0, 1_000_000),
}

# Legacy alias retained so any old references still resolve.
CITY_PRESET_VA = "All Virginia"


def _ensure_db() -> None:
    """First-time DB sync. Idempotent; no-op after the first session run."""
    if st.session_state.get("_db_synced"):
        return
    with st.spinner("Loading ALN data…"):
        ensure_db_synced()
    st.session_state["_db_synced"] = True


@st.dialog("Add custom property", width="large")
def _show_add_property_dialog() -> None:
    """Modal dialog for adding a property not in ALN (off-market deal,
    new construction, etc.).

    Refactored 2026-05-08: was previously an expander INSIDE the sidebar,
    where dark text rendered on the dark sidebar bg and the form ate
    sidebar real estate. Now it's a proper modal that opens in the main
    content area when the analyst clicks the `+` button below the
    Management Company filter.

    Uses individual widgets (no st.form) so ZIP entry can auto-populate
    city / state / lat / lng via the zippopotam.us free API. The user can
    override any auto-filled value before clicking the final submit button.

    Stores the entry to `Properties/_custom_props.json` (durable) AND inserts
    into the SQLite query layer (so it appears in the sidebar list right away).
    """
    st.caption(
        "Type a ZIP and city / state / coordinates auto-fill. Override "
        "any field manually. Required: name, address, city, units, class, lat/lng."
    )

    name = st.text_input("Property name *", key="cp_name")

    col_addr, col_zip = st.columns([3, 1])
    with col_addr:
        address = st.text_input("Address *", key="cp_address")
    with col_zip:
        zip_code = st.text_input(
            "ZIP", key="cp_zip", max_chars=5,
            help="Type a 5-digit ZIP to auto-fill city/state/coords.",
        )

    # ZIP auto-fill: when a fresh 5-digit ZIP is entered, look it up and
    # write defaults into session_state so the city / state / lat / lng
    # widgets pick them up on this rerun.
    if zip_code and len(zip_code) == 5 and zip_code.isdigit():
        last_zip = st.session_state.get("_cp_last_zip_lookup")
        if zip_code != last_zip:
            info = _lookup_zip(zip_code)
            if info:
                st.session_state["_cp_last_zip_lookup"] = zip_code
                st.session_state["cp_city"] = info["city"]
                st.session_state["cp_state"] = info["state"]
                st.session_state["cp_lat"] = info["latitude"]
                st.session_state["cp_lng"] = info["longitude"]
                st.rerun()

    col_city, col_state, col_class = st.columns([3, 1, 1])
    with col_city:
        city = st.text_input(
            "City *", key="cp_city",
            placeholder="auto-fills from ZIP",
        )
    with col_state:
        state = st.text_input(
            "State", key="cp_state", value=st.session_state.get("cp_state", "VA"),
            max_chars=2,
        )
    with col_class:
        asset_class = st.selectbox(
            "Class *", options=["A", "B", "C", "D"],
            index=2, key="cp_class",
        )

    col_units, col_year = st.columns(2)
    with col_units:
        units = st.number_input(
            "Units *", min_value=1, max_value=10_000, value=100, step=1,
            key="cp_units",
        )
    with col_year:
        year_built = st.number_input(
            "Year built", min_value=1900, max_value=2050, value=1985, step=1,
            key="cp_year_built",
        )

    col_lat, col_lng = st.columns(2)
    with col_lat:
        latitude = st.number_input(
            "Latitude *", min_value=-90.0, max_value=90.0,
            value=float(st.session_state.get("cp_lat", 36.85)),
            format="%.6f", step=0.0001, key="cp_lat",
            help="Auto-fills from ZIP. Norfolk ≈ 36.85, Hampton ≈ 37.03.",
        )
    with col_lng:
        longitude = st.number_input(
            "Longitude *", min_value=-180.0, max_value=180.0,
            value=float(st.session_state.get("cp_lng", -76.29)),
            format="%.6f", step=0.0001, key="cp_lng",
            help="Hampton Roads is roughly -76.0 to -76.5.",
        )

    # Optional underwriting-relevant fields
    col_rent, col_sqft = st.columns(2)
    with col_rent:
        avg_rent = st.number_input(
            "Avg rent ($/mo)", min_value=0, max_value=20_000, value=0, step=25,
            key="cp_avg_rent",
        )
    with col_sqft:
        avg_sqft = st.number_input(
            "Avg sqft", min_value=0, max_value=10_000, value=0, step=10,
            key="cp_avg_sqft",
        )
    occupancy_pct = st.slider("Occupancy %", 0, 100, 90, key="cp_occ")

    col_owner, col_mgr = st.columns(2)
    with col_owner:
        owner = st.text_input("Owner", key="cp_owner")
    with col_mgr:
        manager = st.text_input("Manager / broker", key="cp_manager")

    submitted = st.button("➕ Create property", type="primary", use_container_width=True)
    if submitted:
        # Validate required fields
        missing = []
        if not name.strip(): missing.append("name")
        if not address.strip(): missing.append("address")
        if not city.strip(): missing.append("city")
        if not units: missing.append("units")
        if not asset_class: missing.append("class")
        if missing:
            st.error(f"Required fields missing: {', '.join(missing)}")
            return

        prop_dict = {
            "name": name.strip(),
            "address": address.strip(),
            "city": city.strip(),
            "state": state.strip().upper() or "VA",
            "zip": zip_code.strip() or None,
            "units": int(units),
            "year_built": int(year_built) if year_built else None,
            "occupancy_pct": occupancy_pct / 100.0,
            "avg_sqft": float(avg_sqft) if avg_sqft else None,
            "avg_rent": float(avg_rent) if avg_rent else None,
            "rent_per_sqft": (
                float(avg_rent) / float(avg_sqft)
                if avg_rent and avg_sqft else None
            ),
            "asset_class": asset_class,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "owner": owner.strip() or None,
            "manager": manager.strip() or None,
            "status": "Custom",
            "asset_or_fee": "Asset",
        }

        prop_id = add_custom_property(prop_dict)
        prop_dict["property_id"] = prop_id
        upsert_property(prop_dict)

        st.session_state["selected_property_id"] = prop_id

        # Clear the form fields for next add + the dialog-open flag
        for k in (
            "cp_name", "cp_address", "cp_zip", "cp_city", "cp_state",
            "cp_owner", "cp_manager", "_cp_last_zip_lookup",
            "_show_add_property_dialog",
        ):
            st.session_state.pop(k, None)

        st.success(f"✓ Created `{name}` and selected it.")
        st.rerun()


def maybe_show_add_property_dialog() -> None:
    """Open the Add-Custom-Property modal if the sidebar button set the flag.

    MUST be called from `app.py` AFTER `render_sidebar()` returns — i.e.
    OUTSIDE any `with st.sidebar:` context. `@st.dialog` decorators don't
    render reliably when invoked from inside a sidebar context manager
    (Streamlit 1.57 behavior), so the sidebar's button just sets a flag
    and this wrapper opens the modal from the main script flow.
    """
    if st.session_state.get("_show_add_property_dialog"):
        _show_add_property_dialog()


def _list_filtered_properties(
    *,
    search: str | None,
    asset_class: str | None,
    state_choice: str,
    city_choice: str,
    units_preset: str,
    management_company: str | None,
) -> list[dict]:
    """Apply UI filter selections to the DB query (data-driven State→City)."""
    units_min, units_max = UNITS_PRESETS[units_preset]

    # Resolve the STATE selection.
    state: str | None = None
    cities: list[str] | None = None
    if state_choice == STATE_PRESET_TARGET:
        # All target states — pass each as an IN-list via `cities`? No — state
        # is the filter. We post-filter to target states below since SQL takes
        # one state at a time; cheaper to filter the (≤500) result set.
        state = None
    elif state_choice == STATE_PRESET_ALL:
        state = None
    elif len(state_choice) == 2:
        state = state_choice

    # Resolve the CITY selection.
    single_city: str | None = None
    if city_choice == CITY_PRESET_HR:
        cities = list(HAMPTON_ROADS_CITIES)
    elif city_choice and city_choice != CITY_PRESET_ALL:
        single_city = city_choice

    properties = list_properties(
        search=search,
        asset_class=asset_class,
        state=state,
        city=single_city,
        cities=cities,
        units_min=units_min,
        units_max=units_max,
        management_company=management_company,
        limit=500,
    )

    # Post-filter for the multi-state "target states" preset.
    if state_choice == STATE_PRESET_TARGET:
        targets = {s for s, _ in TARGET_STATES}
        properties = [p for p in properties if p.get("state") in targets]

    return properties


def _render_storage_status_chip() -> None:
    """Render a tiny chip showing whether the workbench is reading from
    local disk or Microsoft Graph (OneDrive via cloud). Updates live based
    on the ``ER_STORAGE_BACKEND`` env var.

    Three states:
      - local-disk : Brian's desktop, normal dev flow → gray-on-grey chip
      - graph-onedrive : Azure App Service prod → gold chip with last-pull tooltip
      - error : storage layer failed to initialize → red chip with hint
    """
    from core.storage import get_storage

    dc = config.DARK_COLORS
    try:
        storage = get_storage()
        label = storage.backend_label
    except Exception as e:
        label = f"error: {e}"

    if label == "graph-onedrive":
        bg = dc["ac"]
        fg = "#0f1117"
        icon = "☁️"
        text = "OneDrive via Graph"
    elif label == "local-disk":
        bg = dc["bg4"]
        fg = dc["tx2"]
        icon = "💾"
        text = "Local disk"
    else:
        bg = "#7f1d1d"
        fg = "#fff"
        icon = "⚠️"
        text = label

    st.sidebar.markdown(
        f'<div style="margin-top:8px;padding:4px 10px;background:{bg};'
        f'color:{fg};font-size:10px;font-weight:600;border-radius:8px;'
        f'text-align:center;letter-spacing:0.4px">'
        f'{icon}  {text}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_sidebar() -> tuple[str, str | None]:
    """Render the sidebar — module switcher at top + property selector below.

    Returns:
        (active_module, selected_property_id) tuple. The module is the
        chosen module slug (e.g. "deal_analysis" or "crm"). The
        property_id is None when CRM module is active (it doesn't depend
        on a specific property).

    Modules (enterprise-software-style left-nav, per Brian 2026-05-08 ask):
      - **deal_analysis** — Subject / Performance & Market / Underwriting /
        Returns & Waterfall / Exec Summary. The "underwrite a specific
        property" workflow.
      - **crm** — Inventory & Alerts / Pipeline & Sourcing. The "find new
        properties to chase + manage broker outreach" workflow. No
        property selection needed.
    """
    _ensure_db()

    c = config.COLORS

    # Initialize active module in session state
    if "active_module" not in st.session_state:
        st.session_state["active_module"] = "deal_analysis"

    with st.sidebar:
        # ---- Module switcher (top, always visible) ----
        active_module = _render_module_switcher()
        _render_storage_status_chip()
        st.markdown('<div style="margin-top:8px"></div>', unsafe_allow_html=True)

        # ---- Portfolio module: no property selector — return early ----
        if active_module == "portfolio":
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:3px solid {c["ac"]};border-radius:6px;padding:10px 14px">'
                f'<div style="color:{c["tx2"]};font-size:12px;line-height:1.5">'
                f'<b style="color:{c["ac2"]}">Portfolio</b><br>'
                f'Aggregated view across every property in the book — '
                f'concentration, rate-shock sensitivity, LP capital deployed.<br><br>'
                f'<span style="color:{c["tx3"]};font-size:11px">'
                f'Switch to <b>Deal Analysis</b> to underwrite a specific '
                f'property.</span></div></div>',
                unsafe_allow_html=True,
            )
            return active_module, st.session_state.get("selected_property_id")

        # ---- Help module: no property selector — return early ----
        if active_module == "help":
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:3px solid {c["ac"]};border-radius:6px;padding:10px 14px">'
                f'<div style="color:{c["tx2"]};font-size:12px;line-height:1.5">'
                f'<b style="color:{c["ac2"]}">Help</b><br>'
                f'Plain-English guide to the four headline workbench '
                f'features: Market Calibration, Bidirectional DD Verdict '
                f'Tightening, AI Acquisition Checklist Co-Pilot, and '
                f'Forced-Seller Radar.<br><br>'
                f'<span style="color:{c["tx3"]};font-size:11px">'
                f'Click any <b>ⓘ</b> icon next to a feature heading anywhere '
                f'in the app to jump directly to the matching section.</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
            return active_module, st.session_state.get("selected_property_id")

        # ---- CRM module: no property selector — return early ----
        if active_module == "crm":
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:3px solid {c["ac"]};border-radius:6px;padding:10px 14px">'
                f'<div style="color:{c["tx2"]};font-size:12px;line-height:1.5">'
                f'<b style="color:{c["ac2"]}">CRM & Sourcing</b><br>'
                f'Browse 3,370 HR multifamily parcels, track broker '
                f'relationships, generate direct-mail target lists, and '
                f'flag refi-pressure candidates.<br><br>'
                f'<span style="color:{c["tx3"]};font-size:11px">'
                f'Switch to <b>Deal Analysis</b> to underwrite a specific '
                f'property.</span></div></div>',
                unsafe_allow_html=True,
            )
            return active_module, st.session_state.get("selected_property_id")

        st.markdown(v2_strip_icon("### 🏢 Properties"))

        # (Add custom property button moved below Management Company filter
        # — per Brian 2026-05-08; was an in-sidebar expander before, where
        # dark text was unreadable on the dark sidebar bg.)

        search = st.text_input(
            "Search",
            key="filter_search",
            placeholder="Name, city, address…",
            label_visibility="collapsed",
        )

        # Class + State on one row.
        col_class, col_state = st.columns([1, 2])
        with col_class:
            class_choice = st.selectbox(
                "Class",
                options=["All", "A", "B", "C", "D"],
                index=3,  # default C
                key="filter_class",
            )
        with col_state:
            # Data-driven state list — target states first, then any others
            # present in the loaded ALN data.
            state_codes = list_distinct_states(target_first=True)
            state_label = {code: name for code, name in TARGET_STATES}
            state_options = [STATE_PRESET_TARGET] + [
                f"{state_label.get(c, c)} ({c})" for c in state_codes
            ] + [STATE_PRESET_ALL]
            state_pick = st.selectbox(
                "State",
                state_options,
                index=0,  # default: all target states
                key="filter_state",
            )
            # Resolve the picked label back to a state code (or preset string)
            if state_pick in (STATE_PRESET_TARGET, STATE_PRESET_ALL):
                state_choice = state_pick
            else:
                # "Virginia (VA)" -> "VA"
                state_choice = state_pick.rsplit("(", 1)[-1].rstrip(")")

        # City cascade — populated from the DB for the selected state.
        if state_choice not in (STATE_PRESET_TARGET, STATE_PRESET_ALL):
            city_rows = city_counts_for_state(state_choice)
            city_opts = [CITY_PRESET_ALL]
            if state_choice == "VA":
                city_opts.append(CITY_PRESET_HR)
            city_opts += [f"{name} ({n})" for name, n in city_rows]
        else:
            # Multi-state selection — offer the HR preset (home base) + the
            # top cities across the selection, by count.
            city_opts = [CITY_PRESET_ALL, CITY_PRESET_HR]
        city_pick = st.selectbox(
            "City",
            city_opts,
            index=0,  # default: all cities in the state(s)
            key="filter_city",
        )
        if city_pick in (CITY_PRESET_ALL, CITY_PRESET_HR):
            city_choice = city_pick
        else:
            city_choice = city_pick.rsplit(" (", 1)[0]  # strip "(N)" count

        col_size, col_fav = st.columns([2, 1])
        with col_size:
            units_preset = st.selectbox(
                "Size",
                options=list(UNITS_PRESETS.keys()),
                index=0,
                key="filter_units",
            )
        with col_fav:
            favorites_only = st.checkbox(
                "⭐ Favs only",
                key="filter_favorites_only",
                help="Show only favorited properties.",
            )

        # Management company filter — distinct values from the DB, sorted by
        # how many properties each manages (most-managed first).
        # min_count=2 hides the long tail of one-property managers.
        # The "(N props)" suffix makes the dropdown self-explanatory: it's
        # the count of properties that company manages in this dataset.
        mgmt_options_raw = list_management_companies(min_count=2)
        mgmt_choices = ["All managers"] + [
            f"{name}  ({n} props)" for name, n in mgmt_options_raw
        ]
        mgmt_choice = st.selectbox(
            "Management company",
            options=mgmt_choices,
            index=0,
            key="filter_mgmt",
            help=(
                "Filter properties by management company. "
                "The number in parentheses is how many properties that "
                "company manages in your loaded ALN dataset — sorted "
                "by count descending."
            ),
        )
        # Strip the count suffix to get the bare company name for SQL
        mgmt_filter: str | None = None
        if mgmt_choice != "All managers":
            mgmt_filter = mgmt_choice.rsplit("  (", 1)[0]

        # ---- "+ Add custom property" button (opens modal in main pane) ----
        # Sits below Management Company per Brian 2026-05-08. Clicking sets
        # a session-state flag — the dialog is then opened from OUTSIDE the
        # `with st.sidebar:` context (see end of `render_sidebar`) because
        # `@st.dialog` invoked from inside a sidebar context manager doesn't
        # render reliably (Streamlit 1.57 known behavior).
        if st.button(
            "➕ Add custom property",
            key="add_custom_property_btn",
            use_container_width=True,
            help="Create a property record that's not in ALN — opens an "
                 "input form in the main pane.",
        ):
            st.session_state["_show_add_property_dialog"] = True
            st.rerun()

        # Apply filters and fetch.
        # When the user types in Search, treat it as a "find this specific
        # property" intent and bypass the categorical filters (class, city,
        # units, management). Otherwise typing an address gets hidden by the
        # default Class=C / city=Hampton Roads / units presets. The user can
        # still combine Search with the ⭐ Favs-only filter — that's
        # intentional scope, not a categorical filter.
        # See Brian's request 2026-05-27: "If I enter an address, show the
        # property in the results list."
        asset_class = None if class_choice == "All" else class_choice
        if search:
            properties = _list_filtered_properties(
                search=search,
                asset_class=None,
                state_choice=STATE_PRESET_ALL,  # search every state
                city_choice=CITY_PRESET_ALL,    # no city restriction
                units_preset="All sizes",       # no units restriction
                management_company=None,
            )
            search_bypassed_filters = True
        else:
            properties = _list_filtered_properties(
                search=None,
                asset_class=asset_class,
                state_choice=state_choice,
                city_choice=city_choice,
                units_preset=units_preset,
                management_company=mgmt_filter,
            )
            search_bypassed_filters = False

        # Apply favorites filter (post-DB-query, since favorites live in JSON)
        favs = load_favorites()
        if favorites_only:
            properties = [
                p for p in properties
                if str(p.get("property_id") or "") in favs
                or str(p.get("aln_id") or "") in favs
            ]

        st.caption(f"**{len(properties)}** propert{'y' if len(properties) == 1 else 'ies'}")
        if search_bypassed_filters:
            st.caption(
                "🔎 _Searching across all VA — Class / City / Size / Mgmt "
                "filters bypassed while Search is active._"
            )

        if not properties:
            st.info("No properties match these filters. Try broadening the search.")
            return active_module, st.session_state.get("selected_property_id")

        current_id = st.session_state.get("selected_property_id")

        # Cap displayed list to keep render time reasonable. Sidebar will scroll.
        DISPLAY_CAP = 200
        shown = properties[:DISPLAY_CAP]
        if len(properties) > DISPLAY_CAP:
            st.caption(
                f"Showing first {DISPLAY_CAP}. Narrow filters to see the rest."
            )

        # Inject CSS once per render to style the list-of-buttons as compact
        # left-aligned property rows. Sidebar uses DARK theme — pull from
        # DARK_COLORS so light text reads on dark button bg.
        dc = config.DARK_COLORS
        st.markdown(
            f"""
<style>
section[data-testid="stSidebar"] [data-testid="stButton"] > button {{
  text-align: left !important;
  justify-content: flex-start !important;
  padding: 6px 10px !important;
  border-radius: 4px !important;
  font-weight: 400 !important;
  font-size: 12px !important;
  line-height: 1.35 !important;
  white-space: normal !important;
  height: auto !important;
  min-height: 0 !important;
  border: 1px solid {dc['bdr']} !important;
  background: {dc['bg3']} !important;
  margin-bottom: 3px !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button p {{
  color: {dc['tx']} !important;
  font-size: 12px !important;
  line-height: 1.35 !important;
  margin: 0 !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {{
  border-color: {dc['ac']} !important;
  background: {dc['bg4']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {{
  background: {dc['blbg']} !important;
  border-left: 3px solid {dc['bl']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] p {{
  color: {dc['ac2']} !important;
  font-weight: 500 !important;
}}
</style>
            """,
            unsafe_allow_html=True,
        )

        # Render one button per property. Click sets selected_property_id.
        # Address shown on its own line so address-text searches are visually
        # verifiable — users can see WHICH property at that address matched.
        new_id = current_id
        for p in shown:
            pid = p["property_id"]
            aln_id = str(p.get("aln_id") or "")
            is_sel = (pid == current_id)
            is_fav = (str(pid) in favs) or (aln_id and aln_id in favs)
            name = p.get("name") or "—"
            address = (p.get("address") or "").strip()
            city = p.get("city") or "?"
            units = p.get("units") or "?"
            cls = p.get("asset_class") or "—"
            year = p.get("year_built") or ""
            year_str = f" · {year}" if year else ""
            star = "⭐ " if is_fav else ""
            address_line = f"{address}\n" if address else ""
            label = (
                f"{star}{name}\n"
                f"{address_line}"
                f"{city} · {units}u · Class {cls}{year_str}"
            )

            if st.button(
                label,
                key=f"prop_btn_{pid}",
                use_container_width=True,
                type="primary" if is_sel else "secondary",
            ):
                new_id = pid

        # If selection changed, persist + rerun so the rest of the page updates
        if new_id != current_id:
            st.session_state["selected_property_id"] = new_id
            st.rerun()

        return active_module, new_id


# ---------------------------------------------------------------------------
# Module switcher
# ---------------------------------------------------------------------------

def _render_module_switcher() -> str:
    """Render the module switcher at the top of the sidebar (Deal Analysis
    vs CRM & Sourcing). Returns the active module slug.

    Implemented as two big clickable buttons styled as nav cards — kept
    custom-CSS-styled rather than a radio button so the UX feels like
    enterprise SaaS module nav (think Yardi, RealPage, MRI).
    """
    c = config.COLORS
    active = st.session_state.get("active_module", "deal_analysis")

    modules = [
        ("deal_analysis", "🏢 Deal Analysis",
         "Underwrite a specific property"),
        ("crm",           "🎯 CRM & Sourcing",
         "Pipeline · brokers · refi candidates"),
        ("portfolio",     "📊 Portfolio",
         "All deals · concentration · rate shock"),
        ("help",          "❓ Help",
         "Plain-English guide to key features"),
    ]

    # Sidebar uses DARK theme — all colors come from DARK_COLORS so
    # the buttons are dark cards on dark sidebar bg with light text.
    # (Earlier bug: using light COLORS made button labels white-on-white
    # because the sidebar markdown override forced text light.)
    dc = config.DARK_COLORS

    st.markdown(
        f"""
<style>
.er-module-nav-label {{
  color: {dc['tx3']};
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.7px;
  font-weight: 600;
  margin-bottom: 4px;
}}
/* Module-switcher button styling. Buttons live inside stButton inside the
   sidebar — the inner stMarkdownContainer holds the label text. We force
   light text on it so labels are readable on dark button bg. */
section[data-testid="stSidebar"] [data-testid="stButton"] > button {{
  background: {dc['bg3']} !important;
  border: 1px solid {dc['bdr']} !important;
  color: {dc['tx']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button p {{
  color: {dc['tx']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {{
  border-color: {dc['ac']} !important;
  background: {dc['bg4']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {{
  background: {dc['blbg']} !important;
  border-left: 3px solid {dc['ac']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] p {{
  color: {dc['ac2']} !important;
  font-weight: 600 !important;
}}
</style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div class="er-module-nav-label">Workspace</div>',
        unsafe_allow_html=True,
    )

    selected = active
    for slug, label, sub in modules:
        is_active = (slug == active)
        # Use Streamlit's button + style via JS-injected class. Since we
        # can't directly add a class to st.button, we approximate by using
        # the type=primary/secondary distinction and color via CSS for the
        # primary state.
        if st.button(
            f"{label}\n{sub}",
            key=f"module_{slug}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            selected = slug

    if selected != active:
        st.session_state["active_module"] = selected
        st.rerun()

    return selected
