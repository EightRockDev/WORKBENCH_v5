"""Eight Rock Capital Partners — underwriting conventions.

Single source of truth for every constant in the workbench. If a number
appears in `core/` or `app.py`, it should reference a name in this file.

Decisions ratified 2026-05-06; see memory file `feedback_underwriting_conventions.md`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Version  (v5.0 scheme, format: V5.PHASE.FEATURE.PATCH.BUILD)
#   - the leading 5 marks the Workbench v5.0 product line (fixed)
#   - PHASE   : build-sequence milestone (0 = P0/P0.5 pilot, 1 = Walk, ...)
#   - FEATURE : notable capability added within the phase
#   - PATCH   : fixes/refinements
#   - BUILD   : increments on every change
# *** BUMP THIS ON EVERY CHANGE and add a CHANGELOG.md entry (owner directive). ***
# ---------------------------------------------------------------------------

WORKBENCH_VERSION = "V5.38.3.0.0"


# ---------------------------------------------------------------------------
# Brand palette
# ---------------------------------------------------------------------------
#
# Per Brian 2026-05-08: Yardi-Matrix-inspired UX with light content area for
# readability, while keeping Eight Rock brand colors (gold accent + dark
# chrome). The CHROME (top bar + sidebar) is dark per `DARK_COLORS` below;
# the CONTENT pane (tabs, cards, tables) uses light values in `COLORS`.
#
# All custom HTML components (in ui/comps.py, ui/value_add.py, etc.) read
# `config.COLORS` directly, so flipping these values flips the whole content
# area to light mode without per-component refactoring.

COLORS = {  # CONTENT-PANE LIGHT THEME (Yardi-style)
    "bg":     "#f1f4f9",  # main content bg (slightly stronger grey)
    "bg2":    "#ffffff",  # cards / panels (clean white)
    "bg3":    "#f5f7fb",  # subtle inset / table headers
    "bg4":    "#e6ebf2",  # hover / secondary
    "bdr":    "#c5cdd9",  # borders — stronger than #d8dee8 for clear card edges
    "bdr2":   "#9ba8bd",  # emphasized borders
    "tx":     "#0f1117",  # primary text (near-black)
    "tx2":    "#3a4555",  # secondary — darkened for more contrast on white
    "tx3":    "#5a6478",  # tertiary — darkened
    "gn":     "#15803d",  # GO green (darker for legibility on white)
    "gnbg":   "#dcfce7",
    "gnbrd":  "#86efac",
    "rd":     "#b91c1c",  # NO-GO red (darker for legibility)
    "rdbg":   "#fee2e2",
    "rdbrd":  "#fca5a5",
    "yw":     "#b45309",  # WATCH amber (darker)
    "bl":     "#1d4ed8",  # link / selection blue (darker)
    "blbg":   "#dbeafe",
    # Eight Rock brand gold — `ac` works on white; `ac2` is dark gold for
    # text emphasis on light bg (was light gold on dark bg in old theme).
    "ac":     "#C8900A",  # Eight Rock gold (primary accent)
    "ac2":    "#a37102",  # darker gold for emphasis on light surfaces
    "ac3":    "#7a5400",  # darkest gold (hover/active)
    "src_rr":   "#15803d",  # 🟢 Rent Roll
    "src_t12":  "#b45309",  # 🟠 T-12
    "src_ref":  "#6b7588",  # ⚪ reference record (legacy survey row, pre-flip)
    "src_8r":   "#14b8a6",  # 🟦 8R self-sourced backbone (teal)
    "src_etl":  "#7c3aed",  # 🟣 Public ETL
    "src_user": "#a37102",  # 🥇 User input
    "src_calc": "#1d4ed8",  # 🔵 Computed
    "src_unknown": "#94a3b8",
}

# Reserved for the top bar + sidebar chrome only. Kept dark so the gold
# Eight Rock logo reads cleanly and the workspace nav has visual weight.
DARK_COLORS = {
    "bg":     "#0f1117",
    "bg2":    "#161b27",
    "bg3":    "#1e2536",
    "bg4":    "#252d3d",
    "bdr":    "#3a4560",
    "bdr2":   "#4d5a78",
    "tx":     "#f5f7fb",
    "tx2":    "#bdc5d6",
    "tx3":    "#94a0b3",
    "ac":     "#D4A017",
    "ac2":    "#F7D060",
    "ac3":    "#A67C00",
    "blbg":   "#0f1f3a",
    "bl":     "#3b82f6",
    "gn":     "#22c55e",
    "rd":     "#ef4444",
    "yw":     "#f59e0b",
}

FONT_FAMILY = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"


# ---------------------------------------------------------------------------
# Reusable inline icons (HTML/SVG)
# ---------------------------------------------------------------------------

# Microsoft Excel-style icon. Mimics the recognizable Microsoft 365 Excel
# logo — bold green rounded square with a stylized white "X" composed of
# two crossing strokes (not a literal letter), plus a darker green sidebar
# accent so it reads as "spreadsheet app" not "generic green chip" at small
# sizes. Avoids licensing concerns of using Microsoft's actual brand asset.
# Renders cleanly in `st.markdown(unsafe_allow_html=True)` contexts.
EXCEL_ICON_HTML = (
    '<span style="display:inline-block;vertical-align:-4px;margin-right:6px">'
    '<svg width="20" height="20" viewBox="0 0 32 32" '
    'xmlns="http://www.w3.org/2000/svg">'
    # Darker green sidebar (like Excel's accent strip)
    '<rect x="0" y="0" width="9" height="32" rx="2" fill="#0E6B38"/>'
    # Main green body
    '<rect x="7" y="0" width="25" height="32" rx="2" fill="#107C41"/>'
    # White "X" — two crossing strokes (left → right and right → left)
    '<path d="M13 9 L19 16 L13 23 H17 L21 17.5 L25 23 H29 L23 16 L29 9 H25 L21 14.5 L17 9 Z" '
    'fill="white"/>'
    '</svg></span>'
)

# ---------------------------------------------------------------------------
# GO / WATCH / NO-GO bars (Class C, Hampton Roads)
# ---------------------------------------------------------------------------

GO_CAP = 0.075
WATCH_CAP = 0.070
NOGO_CAP = 0.0685

GO_DSCR = 1.30
WATCH_DSCR = 1.10
NORFOLK_DSCR_FLOOR = 1.25  # Norfolk overlay (tighter than market)

GO_COC = 0.06
WATCH_COC = 0.04


# ---------------------------------------------------------------------------
# Return targets (LP-facing)
# ---------------------------------------------------------------------------

LP_IRR_TARGET = 0.15      # primary GO threshold over 5-yr hold
PROJECT_IRR_TARGET = 0.18  # supporting indicator
LP_EQUITY_MULTIPLE_TARGET = 1.8


# ---------------------------------------------------------------------------
# Capital structure
# ---------------------------------------------------------------------------

LP_PREF = 0.08              # cumulative, non-compounded, on unreturned LP capital
LP_RESIDUAL_SPLIT = 0.70    # tier 3 LP share
GP_RESIDUAL_SPLIT = 0.30    # tier 3 GP promote
GP_COINVEST = 0.0           # LPs fund 100% of equity


# ---------------------------------------------------------------------------
# Asset Management Fee
# ---------------------------------------------------------------------------

AM_FEE_PCT = 0.04        # default 4% of GPR
AM_FEE_PCT_MIN = 0.0
AM_FEE_PCT_MAX = 0.05    # slider ceiling
AM_FEE_EXIT_YEAR = 0.0   # waived in the sale year


# ---------------------------------------------------------------------------
# Operating assumptions
# ---------------------------------------------------------------------------

VACANCY_DEFAULT = 0.07           # Eight Rock baseline (Brian adjusts manually per deal)
VACANCY_NORFOLK_DOWNSIDE = 0.10  # required stress test for Norfolk acquisitions
VACANCY_FLOOR = 0.02
VACANCY_CEILING = 0.15

EXPENSE_GROWTH_DEFAULT = 0.03
RENT_GROWTH_DEFAULT = 0.03

# Class-based expense ratios (used when sources don't have actuals)
EXPENSE_RATIOS = {"A": 0.40, "B": 0.42, "C": 0.45, "D": 0.48}


# ---------------------------------------------------------------------------
# Debt
# ---------------------------------------------------------------------------

AMORT_MONTHS = 300         # 25 years, locked
AMORT_YEARS = 25
IO_YEARS_MIN = 0
IO_YEARS_MAX = 10
IO_YEARS_DEFAULT = 0

INTEREST_RATE_DEFAULT = 0.06
INTEREST_RATE_MIN = 0.03
INTEREST_RATE_MAX = 0.12

DOWN_PAYMENT_DEFAULT = 0.30
DOWN_PAYMENT_MIN = 0.10
DOWN_PAYMENT_MAX = 0.50


# ---------------------------------------------------------------------------
# Hold + exit
# ---------------------------------------------------------------------------

HOLD_PERIOD_DEFAULT = 5
HOLD_PERIOD_MIN = 3
HOLD_PERIOD_MAX = 10

EXIT_CAP_DEFAULT = 0.075
EXIT_CAP_MIN = 0.04
EXIT_CAP_MAX = 0.12


# ---------------------------------------------------------------------------
# LP Equity Raise (replaces the legacy 2.5% soft-cost convention)
# ---------------------------------------------------------------------------

# Default LP raise = down-payment dollars. Slider lets Brian raise more for
# closing costs, capex reserves, working capital. Saved to deal.json as
# `raise_amount`. LP IRR is computed on the full raise (not just down payment),
# so raising extra dilutes returns until the extra capital is deployed.
EQUITY_RAISE_MIN_PCT_OF_DP = 1.0   # cannot raise less than down payment
EQUITY_RAISE_MAX_PCT_OF_DP = 1.5   # ~150% of down payment ceiling


# ---------------------------------------------------------------------------
# Comps
# ---------------------------------------------------------------------------

EARTH_RADIUS_MILES = 3958.8

COMPS_BUCKET1_RADIUS_MILES = 3.0
COMPS_BUCKET1_REQUIRE_SAME_CLASS = True
COMPS_BUCKET1_MAX = 8

COMPS_BUCKET2_RADIUS_MILES = 5.0
COMPS_BUCKET2_MAX = 4

COMPS_TOTAL_MAX = COMPS_BUCKET1_MAX + COMPS_BUCKET2_MAX  # 12


# ---------------------------------------------------------------------------
# Phase 0 cutover (spec 7.3, P0-3)
# ---------------------------------------------------------------------------
# Which property spine the read layer (data/db.py) serves:
#   "legacy" - the licensed vendor table `properties` (P0-2 dual-run default)
#   "8r"     - the self-sourced backbone `properties_8r`, adapted to the
#              legacy row shape, legacy ids resolved via property_crosswalk
# Flip to "8r" ONLY after the P0-2 gates hold (comp overlap >= 90%,
# rent delta <= 5%). Env override ER_SPINE_READ_SOURCE wins for testing.
import os as _os
SPINE_READ_SOURCE = _os.environ.get("ER_SPINE_READ_SOURCE", "legacy")


def spine_provenance_color() -> str:
    """Badge color for a value sourced from the property-records table.

    What that table IS depends on the read seam: pre-flip it is the licensed
    reference survey (grey `src_ref`); post-flip it is the self-sourced 8R
    backbone (teal `src_8r`). One duplicate-key slip in COLORS once made the
    reference row render in backbone teal — the two provenances must never
    share a color, because the whole point of the badge is telling them
    apart. Reads SPINE_READ_SOURCE at call time so a flip (or a test
    monkeypatch) takes effect without a restart.
    """
    key = "src_8r" if SPINE_READ_SOURCE == "8r" else "src_ref"
    return COLORS[key]


# ---------------------------------------------------------------------------
# Sensitivity matrix (per SUMMARY-FORMAT.md)
# ---------------------------------------------------------------------------

SENSITIVITY_VACANCIES = (0.05, 0.07, 0.10)
SENSITIVITY_RENT_GROWTHS = (0.02, 0.035, 0.05)
SENSITIVITY_EXPENSE_GROWTHS = {"conservative": 0.025, "aggressive": 0.045}

# Any LP IRR below this in the sensitivity grid is flagged as a downside red flag
SENSITIVITY_LP_IRR_FLAG = 0.12


# ---------------------------------------------------------------------------
# City-level PPU ceilings (Class C, 20–400 units; per SUMMARY-FORMAT.md)
# Per-unit price thresholds. Above GO ceiling → evaluate against WATCH.
# Above WATCH ceiling → NO-GO unless documented value-add thesis.
# ---------------------------------------------------------------------------

CITY_PPU_CEILINGS = {
    "Norfolk":        {"go": 132_000, "watch": 142_000},
    "Virginia Beach": {"go": 141_000, "watch": 151_000},
    "Chesapeake":     {"go": 146_000, "watch": 156_000},
    "Hampton":        {"go": 135_000, "watch": 145_000},
    "Newport News":   {"go": 130_000, "watch": 139_000},
    "Portsmouth":     {"go": 121_000, "watch": 130_000},
    "Suffolk":        {"go": 135_000, "watch": 145_000},
}


# ---------------------------------------------------------------------------
# Misc UI
# ---------------------------------------------------------------------------

PROPERTY_LIST_PAGE_SIZE = 60

# Vacancy source badge colors (UI hints — keys consumed by Streamlit)
VACANCY_SOURCE_COLORS = {
    "record": "#FFC107",  # yellow chip — derived from record occupancy
    "user": "#1976D2",  # blue chip — Brian has overridden
}
