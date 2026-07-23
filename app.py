"""Streamlit entrypoint — Eight Rock Workbench.

Layout: 5 tabs (Underwriting / Waterfall / Comps / Property Detail / Exec Summary)
with a sticky sidebar carrying property filters and selection. Tab-internal
rendering lives in `ui/` modules; this file only orchestrates state and routing.

Run:
    uv run python -m streamlit run app.py

    (Use `python -m streamlit` rather than bare `streamlit` — some Windows
    Application Control policies block the streamlit.exe shim with
    `os error 4551`; routing through python.exe bypasses the block.)
"""

from __future__ import annotations

import base64
from pathlib import Path
from textwrap import dedent

import streamlit as st

# ---------------------------------------------------------------------------
# Load .env at app startup so ANTHROPIC_API_KEY (and any other secrets) are
# in os.environ BEFORE any tab module imports. Doing this here — at the
# top-level of app.py — avoids the race where the artifact panel checked
# for the key before load_dotenv had run.
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import config
from data.db import get_property
from data.property_io import (
    discover_property_folders,
    find_folder_for_property,
)
from ui.comps import render_comps
from ui.due_diligence import render_due_diligence
from ui.acquisition_checklist import render_acquisition_checklist
from ui.exec_summary import render_exec_summary
from ui.help_page import render_help
from ui.ic_memo_validator import render_ic_memo_validator
from ui.inventory import render_inventory
from ui.owner_portal import render_owner_portal
from ui.pipeline import render_pipeline
from ui.portfolio import render_portfolio
from ui.property_detail import render_property_detail
from ui.sidebar import maybe_show_add_property_dialog, render_sidebar
from ui.underwriting import render_underwriting
from ui.waterfall_view import render_waterfall
# V2 "Quiet Operator" theme — activated by ER_THEME=v2 env var. When
# inactive, these imports are dead weight (no side effects, no extra cost).
# See ui/v2_theme_05292026.py.
from ui.v2_theme_05292026 import (
    is_v2 as _is_v2,
    inject_v2_theme as _inject_v2_theme,
    render_v2_active_banner as _v2_active_banner,
    render_v2_topbar as _v2_topbar,
    render_v2_property_header as _v2_header,
    render_v2_stats_bar as _v2_stats,
    render_v2_verdict_band as _v2_verdict,
    render_v2_inspector as _v2_inspector,
    render_v2_cmdk_palette as _v2_cmdk,
    apply_query_param_to_state as _apply_qp,
    render_v1_switch_button as _v1_switch_btn,
    gather_metrics as _v2_metrics,
    render_v2_inventory_landing as _v2_landing,
    record_property_view as _v2_record_view,
)

# Path to the dark-background SVG variant. We embed it as a base64 data URI
# inside an <img> tag rather than inlining the SVG markup — <img> is much
# better behaved in Streamlit's CSS-scoped context, and browsers reliably
# honor `<img height="..." width="...">` so the logo never overflows.
# Logo file naming gotcha (Brian 2026-05-08):
#   `approved-eight-rock-logo-light-...svg` and `logo-transparent-...svg`
#   both use DARK silver gradients (#111111 → #2A2A2A) — they're for use
#   ON LIGHT backgrounds, NOT for displaying as a "light-colored logo".
#   On our dark top bar (#161b27) those would render essentially invisible.
#
#   `eight-rock-capital-partners-logo-...svg` uses the bright-gold + white-
#   silver gradient (#F7D060 / #FFFFFF / #C0C0C0) which reads cleanly
#   against the dark chrome — that's the one we use for the top bar.
_LOGO_PATH = (
    Path(__file__).resolve().parent.parent
    / "Logos"
    / "eight-rock-capital-partners-logo-05062026.svg"
)


def _load_logo_data_uri() -> str:
    """Return the logo as a `data:image/svg+xml;base64,...` URI for <img src=...>.

    Routes through ``core.storage`` so the logo loads from local disk on
    Brian's desktop and from OneDrive via Graph in cloud mode. Returns ''
    if the file is missing — top bar falls back to a text wordmark.
    """
    try:
        from core.storage import get_storage
        from data.property_io import _rel
        storage = get_storage()
        key = _rel(_LOGO_PATH)
        if not storage.is_file(key):
            return ""
        svg_bytes = storage.read_bytes(key)
    except Exception:
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode("ascii")


def _inject_branding() -> None:
    """Hide Streamlit's default chrome and render the Eight Rock top bar.

    Layout mirrors Workbench.html line 157, with the real SVG logo replacing
    the legacy CSS-text wordmark:
      [SVG logo]  |  [Virginia Property Workbench]  ...  [version]

    Theme (per Brian 2026-05-08): Yardi-Matrix-inspired — dark chrome (top
    bar + sidebar with light Eight Rock logo + gold accents) + light content
    pane (white cards on cool-grey bg with dark text) for readability.
    """
    c = config.COLORS         # light theme for content
    dc = config.DARK_COLORS    # dark theme for chrome
    logo_uri = _load_logo_data_uri()
    if logo_uri:
        logo_markup = f'<img class="er-logo-img" src="{logo_uri}" alt="Eight Rock">'
    else:
        logo_markup = '<span class="er-logo-fallback">Eight Rock</span>'

    # IMPORTANT: keep this markdown content unindented so st.markdown does
    # NOT interpret it as a code block (which happens at >=4 leading spaces).
    # `dedent` + `lstrip` guarantees the first non-blank line starts at col 0.
    block = dedent(
        f"""
<style>
/* === YARDI-INSPIRED LIGHT CONTENT + DARK CHROME =====================
   Per Brian 2026-05-08: light content for Yardi-Matrix readability
   (white cards on cool-grey bg, dark text), DARK chrome (top bar +
   sidebar) so the gold Eight Rock logo + workspace nav have weight.
   ==================================================================== */

/* Hide Streamlit's default chrome */
header[data-testid="stHeader"] {{
  background: transparent !important;
}}
header[data-testid="stHeader"] [data-testid="stToolbar"] {{ display: none !important; }}
.stDeployButton {{ display: none !important; }}
#MainMenu {{ visibility: hidden !important; }}
footer {{ visibility: hidden !important; }}

/* === SIDEBAR (DARK CHROME) ============================================ */
section[data-testid="stSidebar"] {{
  display: flex !important;
  visibility: visible !important;
  transform: translateX(0) !important;
  margin-left: 0 !important;
  min-width: 300px !important;
  max-width: 600px !important;
  resize: horizontal;
  overflow: auto;
  background: {dc['bg2']} !important;
  border-right: 1px solid {dc['bdr']} !important;
}}
section[data-testid="stSidebar"] > div {{
  display: flex !important;
  visibility: visible !important;
  background: {dc['bg2']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] span:not([style*="color"]),
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] strong,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h4 {{
  color: {dc['tx']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
section[data-testid="stSidebar"] .stCaption {{
  color: {dc['tx2']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {{
  color: {dc['tx']} !important;
  font-size: 12px !important;
}}
/* Force sidebar widget internals to dark-theme look (the global content
   styles assume light theme). Inputs inside the sidebar should be dark
   cards with light text, not white inputs. */
section[data-testid="stSidebar"] [data-testid="stTextInput"] input,
section[data-testid="stSidebar"] [data-testid="stTextArea"] textarea,
section[data-testid="stSidebar"] [data-testid="stNumberInputContainer"] input,
section[data-testid="stSidebar"] [data-baseweb="select"] > div {{
  background: {dc['bg3']} !important;
  border-color: {dc['bdr']} !important;
  color: {dc['tx']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stExpander"] {{
  background: {dc['bg3']} !important;
  border: 1px solid {dc['bdr']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary * {{
  color: {dc['tx']} !important;
}}
/* Selectbox dropdown menu items (when open) */
section[data-testid="stSidebar"] [data-baseweb="select"] li {{
  color: {dc['tx']} !important;
}}
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
button[kind="header"] {{
  display: none !important;
}}

/* === BASE TYPOGRAPHY (LIGHT CONTENT) ================================= */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
  font-family: {config.FONT_FAMILY};
  font-size: 16px;
  color: {c['tx']};
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}}

/* === FORCE LIGHT BG ON EVERY POSSIBLE STREAMLIT WRAPPER ============== */
/* If Streamlit's cached `base = "dark"` config is still in effect, it
   sets dark backgrounds on multiple wrapper elements that fight our
   light theme. Override every known wrapper aggressively so the content
   pane is always light regardless of the cached theme. */
.stApp,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMain"] > div,
[data-testid="stMain"] section,
.main,
.main > .block-container,
.main > div {{
  background: {c['bg']} !important;
  color: {c['tx']} !important;
}}

/* The body/html — last-line-of-defense override */
html, body {{
  background: {c['bg']} !important;
}}

/* Defend Material Symbols icon font (was breaking on aggressive selectors) */
.material-icons, .material-symbols-outlined, .material-symbols-rounded,
[data-testid="stIconMaterial"], span[data-testid="stIconMaterial"] {{
  font-family: 'Material Symbols Outlined', 'Material Symbols Rounded',
               'Material Icons' !important;
  font-weight: normal !important;
  font-style: normal !important;
  font-size: 1.25em !important;
  letter-spacing: normal !important;
  text-transform: none !important;
  display: inline-block !important;
  white-space: nowrap !important;
  word-wrap: normal !important;
  direction: ltr !important;
  -webkit-font-feature-settings: 'liga' !important;
  -webkit-font-smoothing: antialiased !important;
  font-feature-settings: 'liga' !important;
}}

/* All page text — dark on light */
.block-container p,
.block-container li,
.block-container span:not([style*="color"]),
.block-container label,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span:not([style*="color"]) {{
  color: {c['tx']};
  font-size: 15px;
  line-height: 1.55;
}}

.block-container strong,
.block-container b,
[data-testid="stMarkdownContainer"] strong,
[data-testid="stMarkdownContainer"] b {{
  color: {c['tx']};
  font-weight: 700;
}}

.block-container {{
  padding-top: 0.25rem;
  padding-bottom: 1rem;
  max-width: 1320px;
}}

/* Section headings — dark on light with stronger visual hierarchy.
   h5 (the most-used) gets a gold left-border + larger font so each
   section reads like a labeled card section. */
.block-container h1 {{
  font-size: 32px !important;
  color: {c['tx']} !important;
  font-weight: 700;
  letter-spacing: -0.01em;
}}
.block-container h2 {{
  font-size: 26px !important;
  color: {c['tx']} !important;
  font-weight: 700;
  border-bottom: 2px solid {c['bdr2']};
  padding-bottom: 8px;
  margin-top: 24px;
  margin-bottom: 16px;
}}
.block-container h3 {{
  font-size: 22px !important;
  color: {c['tx']} !important;
  font-weight: 700;
  margin-top: 22px;
  margin-bottom: 12px;
}}
.block-container h4 {{
  font-size: 18px !important;
  color: {c['tx']} !important;
  font-weight: 700;
  margin-top: 18px;
}}
.block-container h5 {{
  font-size: 17px !important;
  color: {c['tx']} !important;
  font-weight: 700;
  letter-spacing: 0.1px;
  margin-top: 22px;
  margin-bottom: 10px;
  padding-left: 10px;
  border-left: 4px solid {c['ac']};
  line-height: 1.4;
}}
.block-container h6 {{
  font-size: 13px !important;
  color: {c['ac3']} !important;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.9px;
  margin-top: 16px;
}}

/* Captions */
[data-testid="stCaptionContainer"],
.stCaption,
small {{
  color: {c['tx2']} !important;
  font-size: 13px !important;
  line-height: 1.5;
}}

/* Metric tiles — Yardi-clean: dark text on white-ish, gold accent for emphasis */
[data-testid="stMetric"] {{
  background: {c['bg2']} !important;
  border: 1px solid {c['bdr']} !important;
  border-radius: 6px !important;
  padding: 10px 14px !important;
}}
[data-testid="stMetricValue"] {{
  font-size: clamp(18px, calc(0.7vw + 14px), 28px) !important;
  font-weight: 700 !important;
  color: {c['tx']} !important;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  line-height: 1.15 !important;
  white-space: nowrap;
}}
[data-testid="stMetricValue"] > div {{
  font-variant-numeric: tabular-nums;
}}
[data-testid="stMetricLabel"] {{
  font-size: 11px !important;
  color: {c['tx2']} !important;
  font-weight: 600 !important;
  text-transform: uppercase;
  letter-spacing: 0.7px;
}}
[data-testid="stMetricDelta"] {{
  font-size: 12px !important;
  font-weight: 600 !important;
}}

/* Tables — dark text on white rows, light-grey header */
[data-testid="stTable"] table,
[data-testid="stDataFrame"] table {{
  font-size: 14px !important;
  color: {c['tx']} !important;
  background: {c['bg2']} !important;
}}
[data-testid="stDataFrame"] td,
[data-testid="stTable"] td {{
  font-variant-numeric: tabular-nums;
  color: {c['tx']} !important;
  background: {c['bg2']} !important;
  border-bottom: 1px solid {c['bdr']} !important;
}}
[data-testid="stDataFrame"] th,
[data-testid="stTable"] th {{
  color: {c['tx']} !important;
  font-weight: 700 !important;
  background: {c['bg3']} !important;
  border-bottom: 2px solid {c['bdr2']} !important;
}}

/* Form widgets — readable on white */
[data-testid="stWidgetLabel"],
.stSlider [data-testid="stWidgetLabel"],
.stNumberInput [data-testid="stWidgetLabel"] {{
  font-size: 14px !important;
  color: {c['tx']} !important;
  font-weight: 600 !important;
}}
[data-testid="stNumberInputContainer"] input,
.stTextInput input,
.stTextArea textarea {{
  font-size: 15px !important;
  color: {c['tx']} !important;
  font-variant-numeric: tabular-nums;
  background: {c['bg2']} !important;
  border: 1px solid {c['bdr']} !important;
}}
[data-testid="stNumberInputContainer"] input:focus,
.stTextInput input:focus,
.stTextArea textarea:focus {{
  border-color: {c['ac']} !important;
  box-shadow: 0 0 0 2px rgba(200, 144, 10, 0.15) !important;
}}

/* Selectbox + multiselect dropdowns */
[data-baseweb="select"] > div {{
  background: {c['bg2']} !important;
  border-color: {c['bdr']} !important;
  color: {c['tx']} !important;
}}

/* Sliders — gold thumb, dark text */
[data-baseweb="slider"] [role="slider"] + div,
[data-testid="stTickBar"] {{
  font-size: 13px !important;
  color: {c['tx']} !important;
  font-weight: 600;
}}

/* === TABS (Yardi-style underlined pills, BOLD) ====================== */
.stTabs [data-baseweb="tab-list"] {{
  border-bottom: 2px solid {c['bdr2']} !important;
  gap: 4px !important;
  margin-bottom: 24px !important;
  background: {c['bg2']} !important;
  padding: 0 8px !important;
  border-radius: 6px 6px 0 0 !important;
}}
.stTabs [data-baseweb="tab"] {{
  font-size: 15px !important;
  font-weight: 600 !important;
  padding: 14px 20px !important;
  color: {c['tx2']} !important;
  background: transparent !important;
  border: none !important;
  border-bottom: 3px solid transparent !important;
  margin-bottom: -2px !important;
  letter-spacing: 0.2px !important;
}}
.stTabs [data-baseweb="tab"]:hover {{
  color: {c['tx']} !important;
  background: {c['bg3']} !important;
}}
.stTabs [data-baseweb="tab"][aria-selected="true"] {{
  color: {c['ac3']} !important;
  border-bottom: 3px solid {c['ac']} !important;
  background: transparent !important;
  font-weight: 700 !important;
}}
.stTabs [data-baseweb="tab"][aria-selected="true"] p {{
  color: {c['ac3']} !important;
  font-weight: 700 !important;
}}

/* Expanders — clean white card */
[data-testid="stExpander"] {{
  background: {c['bg2']} !important;
  border: 1px solid {c['bdr']} !important;
  border-radius: 6px !important;
}}
[data-testid="stExpander"] summary {{
  color: {c['tx']} !important;
  font-weight: 600 !important;
}}

/* Buttons (content area only — sidebar buttons styled separately) */
.block-container [data-testid="stButton"] > button {{
  border: 1px solid {c['bdr']} !important;
  background: {c['bg2']} !important;
  color: {c['tx']} !important;
  font-weight: 500 !important;
}}
.block-container [data-testid="stButton"] > button:hover {{
  border-color: {c['ac']} !important;
  background: {c['bg4']} !important;
}}
.block-container [data-testid="stButton"] > button[kind="primary"] {{
  background: {c['ac']} !important;
  border-color: {c['ac']} !important;
  color: #ffffff !important;
  font-weight: 600 !important;
}}
.block-container [data-testid="stButton"] > button[kind="primary"]:hover {{
  background: {c['ac3']} !important;
}}

/* === DARK TOP BAR (forced full-width via container resets) ============ */
/* Override Streamlit's default block-container padding/margins so the
   topbar can extend edge-to-edge of the content pane. Without these the
   topbar gets clipped to .block-container's max-width and looks tiny. */
[data-testid="stMain"] > .main > .block-container {{
  padding-top: 0 !important;
}}
[data-testid="stMain"] .block-container > div:first-child [data-testid="stMarkdownContainer"] {{
  width: 100% !important;
}}

.er-topbar {{
  display: flex;
  align-items: center;
  gap: 20px;
  height: 88px;
  margin: -1rem -2rem 24px -2rem;
  padding: 0 28px;
  background: {dc['bg2']};
  border-bottom: 3px solid {dc['ac']};
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  position: relative;
  z-index: 10;
}}
.er-logo-img {{
  height: 64px;
  width: auto;
  display: block;
  flex-shrink: 0;
}}
.er-logo-fallback {{
  font-size: 22px;
  font-weight: 700;
  color: {dc['ac2']};
  letter-spacing: 0.2px;
}}
.er-sep {{
  width: 1px;
  height: 44px;
  background: {dc['bdr']};
}}
.er-title {{
  font-size: 26px;
  color: #ffffff;
  flex: 1;
  font-weight: 700;
  letter-spacing: 0.3px;
  line-height: 1.1;
}}
/* Version pill — lighter foreground (`tx2` not `tx3`) so it reads against
   the dark chrome. Brian flagged the prior `#94a0b3` was unreadable. */
.er-version {{
  font-size: 12px;
  color: {dc['tx2']};
  font-weight: 600;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.5px;
  background: rgba(255,255,255,0.10);
  border: 1px solid rgba(255,255,255,0.12);
  padding: 5px 12px;
  border-radius: 6px;
}}
</style>

<div class="er-topbar">
  {logo_markup}
  <div class="er-sep"></div>
  <div class="er-title">Virginia Property Workbench</div>
  <span class="er-version">{config.WORKBENCH_VERSION}</span>
</div>
"""
    ).lstrip()
    st.markdown(block, unsafe_allow_html=True)


def main() -> None:
    st.set_page_config(
        page_title="Eight Rock · Virginia Property Workbench",
        page_icon="🏢",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_branding()

    # ?prop=<id> query param → session state. Runs in BOTH V1 and V2 so the
    # "Switch to V1" / "Switch to V2.0" pills carry the current property
    # selection seamlessly between the two versions.
    _apply_qp()

    # V2 theme overlay — runs only when ER_THEME=v2. Layers Inter +
    # JetBrains Mono fonts and V2 design tokens on top of V1's CSS so
    # the cascade wins via `!important` selectors.
    if _is_v2():
        _inject_v2_theme()
        # Inject the ⌘K palette DOM + JS keyboard listener (Ctrl+K + 1-9).
        # Brian (5/29 evening): removed the gold "V2.0 ACTIVE" banner per the
        # screenshot — V2 chrome itself is now the visual contract.
        _v2_cmdk()

    active_module, selected_property_id = render_sidebar()

    # V1 mode: floating "Try V2.0" pill in the top-right corner.
    # V2 mode: handled inside render_v2_topbar (no-op here).
    _v1_switch_btn(selected_property_id)

    # The sidebar's "+ Add custom property" button sets a session-state flag
    # rather than calling `@st.dialog` directly — dialogs invoked from
    # inside `with st.sidebar:` don't render reliably. We open it here, in
    # the main script flow, so the modal pops cleanly over the content area.
    maybe_show_add_property_dialog()

    # Module-driven tab structure (per Brian 2026-05-08):
    #
    # **Deal Analysis** module — the per-property workflow:
    #   1. Subject              — what is this property
    #   2. Performance & Market — rent roll + comps + market context
    #   3. Underwriting         — dials + KPIs + Refi/Exit + sensitivity
    #   4. Returns & Waterfall  — investor economics
    #   5. Exec Summary         — DOCX export
    #
    # **CRM & Sourcing** module — pipeline tooling, independent of any
    #   single property:
    #   1. Inventory & Alerts   — browse 3,370 HR multifamily parcels
    #   2. Pipeline & Sourcing  — broker CRM + direct-mail + refi alerts
    #
    # The sidebar's module switcher returns `active_module`; we dispatch
    # below based on it.

    if active_module == "crm":
        # ---- CRM & Sourcing module ----
        if _is_v2():
            _v2_topbar(None)  # V2 chrome above CRM module
        tab_inv, tab_pipe = st.tabs([
            "🏠 Inventory & Alerts",
            "🎯 Pipeline & Sourcing",
        ])
        with tab_inv:
            render_inventory(prop=None)
        with tab_pipe:
            render_pipeline(prop=None)
        return

    if active_module == "portfolio":
        # ---- Portfolio Risk Dashboard ----
        if _is_v2():
            _v2_topbar(None)
        render_portfolio()
        return

    if active_module == "help":
        if _is_v2():
            _v2_topbar(None)
        # ---- Help — plain-English guide to the 4 headline features.
        # Clicking any ⓘ next to a section_card heading routes here with
        # ``st.session_state["help_anchor"]`` set; the help page scrolls
        # the matching section into view on load.
        render_help()
        return

    # ---- Deal Analysis module (default) ----
    # V2-aware flow: when ER_THEME=v2, render the V2 hero (eyebrow + name +
    # chips + stat bar + verdict band) ABOVE the tabs, and a sticky right
    # inspector (calibration · diligence · macro · people · documents)
    # alongside them. V1 path is unchanged — tabs render full-width as before.

    # Load prop + folder up front so V2 can use them in the hero. This is
    # purely a reordering — `get_property` and `discover_property_folders`
    # are the same calls V1 made. No logic changes.
    prop = None
    folder = None
    if selected_property_id is not None:
        prop = get_property(selected_property_id)
        if prop is None:
            st.error(f"Property `{selected_property_id}` not found in workbench.db.")
            return
        folders = discover_property_folders()
        folder = find_folder_for_property(prop, folders)

    if _is_v2():
        # V2: render the topbar at full width regardless of property selection
        _v2_topbar(prop)

        if prop is None:
            # Brian 5/29 v2.0.29 — proper inventory landing page (was just
            # "Pick a property…" text). Hero + rotating quote + search +
            # recently-viewed grid + full inventory grid. Cards link to
            # `?prop=<id>` so click navigation works without a Streamlit
            # callback.
            _v2_landing()
            return

        # Record this view so the landing page can sort it to the top
        # next time the user clicks back to the home view.
        _v2_record_view(prop.get("property_id"))

        # Property hero (full width)
        _v2_header(prop)

        # Gather metrics for stat bar / verdict / inspector
        metrics = _v2_metrics(prop, folder)

        # Two-column workspace: main + sticky inspector
        main_col, inspector_col = st.columns([3, 1], gap="large")

        with inspector_col:
            _v2_inspector(prop, metrics)

        with main_col:
            _v2_stats(prop, metrics)
            # Verdict band removed entirely from V2 per Brian 5/29 v2.0.10
            # (the earlier JS-toggled "hide on Subject" approach didn't fire
            # reliably). The Calibration inspector block on the right rail
            # already shows the gate-by-gate pass/fail breakdown.

            # Brian 5/29 v2.0.27 — tab restructure:
            #   - IC Memo tab DELETED → its content moves to bottom of Summary
            #   - Acquisition tab DELETED → its content moves to top of Diligence
            #   - Tab order is now: Subject · Underwriting · Returns ·
            #     Market · Summary · Diligence · Owner Portal (7 tabs total)
            tab_subject, tab_uw, tab_wf, tab_perf, tab_summary, tab_dd, tab_owner = st.tabs([
                "Subject",
                "Underwriting",
                "Returns",
                "Market",
                "Summary",
                "Diligence",
                "Investors",
            ])
    else:
        # V1 path — full descriptive labels with emojis, same reorder /
        # tab removals for parity. Acquisition + IC Memo content lives
        # inside the Diligence + Summary tabs respectively.
        tab_subject, tab_uw, tab_wf, tab_perf, tab_summary, tab_dd, tab_owner = st.tabs([
            "🏢 Subject",
            "📊 Underwriting",
            "💰 Returns & Waterfall",
            "📍 Performance & Market",
            "📄 Exec Summary",
            "📋 Due Diligence",
            "💼 Investors",
        ])

        if selected_property_id is None:
            for tab in (tab_subject, tab_perf, tab_uw, tab_dd, tab_wf, tab_owner, tab_summary, tab_ic, tab_acq):
                with tab:
                    st.info("Pick a property from the sidebar to begin.")
            return

    # ---- Render each tab's content (same renderers for V1 and V2) ----
    # Brian 5/29 v2.0.27 — IC Memo + Acquisition no longer have their own
    # tabs. IC Memo Validator now renders at the BOTTOM of Summary; the
    # Acquisition Checklist renders at the TOP of Diligence.
    with tab_subject:
        render_property_detail(prop, folder)
    with tab_perf:
        render_comps(prop, folder)
    with tab_uw:
        render_underwriting(prop, folder)
    with tab_dd:
        # Acquisition Checklist sits at the TOP of Diligence now.
        render_acquisition_checklist(prop, folder)
        render_due_diligence(prop, folder)
    with tab_wf:
        render_waterfall(prop, folder)
    with tab_owner:
        render_owner_portal(prop, folder)
    with tab_summary:
        render_exec_summary(prop, folder)
        # IC Memo Validator sits at the BOTTOM of Summary now.
        render_ic_memo_validator(prop, folder)


if __name__ == "__main__":
    main()
