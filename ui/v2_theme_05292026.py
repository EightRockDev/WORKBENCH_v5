"""V2 theme — "Quiet Operator" restyle of the V1 Streamlit Workbench.

Activated by environment variable ER_THEME=v2. When inactive, V1 renders
exactly as before (these functions are not called).

Architecture (per Brian 2026-05-29):
  - Single app, both V1 and V2 share the same logic + data layers
  - V2 mode adds CSS overlay (light theme, Inter + JetBrains Mono, V2 gold)
  - V2 mode replaces the property header with hero + chips + stat bar +
    verdict band + right inspector
  - All V1 tabs continue to render normally; V2 just changes the chrome
  - Zero changes to core/, data/, or any test

Public surface:
  - is_v2() -> bool                         — env-var detection
  - inject_v2_theme()                       — call once at app start
  - render_v2_topbar(prop=None)             — top breadcrumb + search + status
  - render_v2_property_header(prop)         — eyebrow + name + chips
  - render_v2_stats_bar(prop)               — 4-card stat bar (asking/cap/IRR/DSCR)
  - render_v2_verdict_band(prop)            — GO/WATCH/NOGO + rationale
  - render_v2_inspector(prop)               — calibration / DD / macro / people / docs
  - get_v2_version_label() -> str           — "V2.0 · Quiet Operator"

Helper modules used: core.calibration, core.verdict, data.property_io.
None of those are modified.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

import config

# ---------------------------------------------------------------------------
# V2 design tokens — locked per reference_ui_direction.md (5/27/2026)
# ---------------------------------------------------------------------------

V2 = {
    "bg":          "#FAFAF7",
    "bg_soft":     "#F4F1E8",
    "card":        "#FFFFFF",
    "ink":         "#0A1628",
    "ink_2":       "#4B5563",
    "ink_3":       "#6B7280",
    "ink_4":       "#9CA3AF",
    "ink_5":       "#D1D5DB",
    "gold":        "#B89738",
    "gold_bright": "#D4B14B",
    "gold_soft":   "#F5EFE0",
    "gold_deep":   "#8C7028",
    "line":        "#E7E5DD",
    "line_faint":  "#F2F0E8",
    "pos":         "#047857",
    "pos_soft":    "#ECFDF5",
    "warn":        "#B45309",
    "warn_soft":   "#FFFBEB",
    "neg":         "#B91C1C",
    "neg_soft":    "#FEF2F2",
    "blue":        "#1D4ED8",
    "blue_soft":   "#EFF6FF",
}


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def is_v2() -> bool:
    """Single source of truth for the theme gate. V2 is the DEFAULT
    (owner 2026-07-31); ER_THEME=v1 restores the legacy layout. Unset
    OR empty both mean v2 - only an explicit other value disables it."""
    return (os.environ.get("ER_THEME") or "v2").lower() == "v2"


def _eight_rock_logo_data_uri() -> str:
    """Read the Eight Rock logo SVG once, return as a data URI for inline
    use in HTML <img> tags. Empty string if the file isn't found.

    Per Brian 5/29 v2.0.25 — logo placed in the upper-right of the V2
    topbar."""
    if not hasattr(_eight_rock_logo_data_uri, "_cached"):
        import base64
        # Walk up from this file: ui/ -> python_workbench/ -> repo root
        repo_root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            repo_root / "Logos" / "approved-eight-rock-logo-light-05062026.svg",
            repo_root / "Logos" / "logo-transparent-05062026.svg",
            repo_root / "Logos" / "eight-rock-capital-partners-logo-05062026.svg",
        ]
        data = ""
        for fp in candidates:
            if fp.exists():
                try:
                    raw = fp.read_bytes()
                    b64 = base64.b64encode(raw).decode("ascii")
                    data = f"data:image/svg+xml;base64,{b64}"
                    break
                except OSError:
                    continue
        _eight_rock_logo_data_uri._cached = data  # type: ignore[attr-defined]
    return _eight_rock_logo_data_uri._cached  # type: ignore[attr-defined]


def _et_clock_now() -> str:
    """Current time in Eastern Time, 12-hour format with AM/PM + 'ET'
    suffix. Per Brian 5/29 v2.0.24 — military time was hard to read.

    Uses the IANA zone 'America/New_York' which auto-handles EST/EDT
    transitions; we just label 'ET' so the display stays consistent
    year-round.
    """
    try:
        from zoneinfo import ZoneInfo
        now_et = dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback if zoneinfo unavailable: format system local with ET
        # label anyway (better than 24-hour military).
        now_et = dt.datetime.now()
    fmt = "%#I:%M %p" if os.name == "nt" else "%-I:%M %p"
    return f"{now_et.strftime(fmt)} ET"


# ─────────────────────────────────────────────────────────────────────────
# V2 BUILD VERSION — *** BUMP THIS ON EVERY V2 CHANGE *** (Brian 5/29 EOD)
# ─────────────────────────────────────────────────────────────────────────
# Patch number increments per substantive V2 change. The history below is
# the changelog — append a new line each time you bump.
#
#   v2.0.1  5/29  Initial V2 deployment (theme overlay, env-var switch)
#   v2.0.2  5/29  ⌘K command palette + 1-9 tab shortcuts
#   v2.0.3  5/29  V1↔V2 switch buttons + cross-version sync wiring
#   v2.0.4  5/29  Purchase Price rename + deal.json wiring
#   v2.0.5  5/29  Full underwriting metrics (IRR, DSCR, EM, CoC) via V1 pipeline
#   v2.0.6  5/29  Screenshot match (sidebar hide, Find anything, eyebrow,
#                 Build IC packet button, calibration labels)
#   v2.0.7  5/29  Subject tab reorder (Sale History → Property Card → Documents →
#                 Auto-Ingestion); Notes side-by-side w/ Property Card; removed
#                 Rent Comp Calls + Comp Call Printable + upload preamble copy;
#                 verdict band hidden on Subject tab; V2 version display added
#   v2.0.8  5/29  Removed "IC-TRACK · " token from the Crossroads eyebrow
#                 (now reads "LIVE DEAL · UPDATED HH:MM XM")
#   v2.0.9  5/29  Performance & Market tab rework:
#                 1. Rent Roll → 2. Comparables (combined B1+B2, B1 in
#                 8R gold) → 3. Rent Listing URLs → 4. Data Sources &
#                 Last Refresh. Removed Map + LIHTC entirely. Latest
#                 scrape results compressed into small inline squares.
#                 "Refresh All" + per-source refresh buttons added, each
#                 wired to `python hampton_roads_etl.py --only <src>` via
#                 subprocess. Subject vs Market preserved in collapsed
#                 expander below the fold.
#  v2.0.10  5/29  Removed verdict band ENTIRELY from V2 (was supposed to
#                 hide on Subject tab via JS but kept showing through).
#                 Calibration inspector block on the right covers the
#                 verdict info anyway. Plus listings panel: more apartment
#                 marketing sites + "Property Marketing Sites" heading.
#  v2.0.11  5/29  Latest Scrape Result squares now CLICKABLE anchors that
#                 open the saved listing_url in a new tab. Hover tilts up
#                 + gold border. Non-link squares (no URL captured)
#                 render as plain divs so cursor doesn't lie.
#                 17 marketing sources confirmed in URL dropdown:
#                 4 scrapeable (rentcafe, zillow, apartments_com,
#                 property_site) + 13 display-only quick-jump links
#                 (apartmentlist, apartmentguide, rent_com, trulia,
#                 hotpads, zumper, realtor_com, forrent, padmapper,
#                 costar, loopnet, craigslist, facebook_marketplace).
#  v2.0.12  5/29  Notes textarea on Subject tab grown 200px → 600px to
#                 visually balance with the Property Card next to it.
#  v2.0.13  5/29  V2 tab labels shortened (emojis dropped, multi-word
#                 trimmed) so all 9 tabs fit in the narrower main column
#                 without overflow: Subject / Market / Underwriting /
#                 Diligence / Returns / Owner Portal / Summary / IC Memo
#                 / Acquisition. V2 tab padding tightened 9px→7px and
#                 font 13px→12px. Pill row gets thin scrollbar fallback
#                 if it still overflows on a narrow viewport.
#  v2.0.14  5/29  Underwriting tab: Market Calibration panel moved from
#                 the TOP of Deal Dials to the BOTTOM of the tab as its
#                 own section_card. Active workflow (dials → metrics →
#                 stress test → cash flow → verdict) now lives above
#                 the fold, with calibration thresholds available as a
#                 reference lookup at the end.
#  v2.0.15  5/29  (1) Subject/Documents: removed the duplicate file
#                 uploader from the top of the Documents section. The
#                 Document Auto-Ingestion panel below is the canonical
#                 upload entry point now (AI extraction + per-field
#                 provenance). (2) Performance & Market: refresh button
#                 for each ETL source moved INSIDE its card (under the
#                 timestamp) — was a separate right-hand column. CSS
#                 :has() targets the container, so the Streamlit button
#                 renders natively inside the bordered card. (3) V2
#                 right-rail Key Documents: each file row is now a
#                 clickable Streamlit button that opens the file in its
#                 native application via os.startfile (Windows) /
#                 subprocess open|xdg-open (mac/linux).
#  v2.0.16  5/29  Icons stripped from ALL section headings in V2 (V1
#                 keeps its emojis). Two chokepoints: (a) section_card
#                 helper drops its `icon=` argument in V2 mode — covers
#                 29 of 29 `section_card(...)` call sites including
#                 Brian's explicit example "🤖 Document Auto-Ingestion".
#                 (b) new `v2_strip_icon()` helper in ui/components.py
#                 strips leading emoji from titles that aren't routed
#                 through section_card — applied to 11 sites: 5
#                 st.expander labels (Filters, Add investor, Add broker,
#                 Log comp call, Data Source Color Key, More market
#                 context) and 4 markdown headers (Properties sidebar,
#                 Comp Call Checklist, 3 value-add subheaders).
#  v2.0.17  5/29  Subject header card consolidated: Favorited + Open
#                 Folder buttons moved INSIDE the white card (right
#                 column) — were in an outer column outside the card.
#                 Chunky ⬆ Upload popover button under the photo
#                 replaced with a small "↗ Photo Upload" text link
#                 styled to match the ↗ Google Maps link. CSS scoped
#                 via .v2-photo-upload-mark marker so it only restyles
#                 this one popover. Performance & Market: ETL refresh
#                 button moved from the bottom of each source card to
#                 the top-right, directly under the timestamp. Each
#                 source is now a 2-column row (info / timestamp+
#                 refresh).
#  v2.0.18  5/29  Section moves across tabs:
#                 (1) Performance & Market: Rent Roll removed (already
#                     renders on Underwriting tab — duplicate).
#                 (2) Underwriting: Year-1 KPIs no longer renders here
#                     (still computes metrics via render=False for the
#                     sensitivity / verdict / refi-exit sections that
#                     consume them).
#                 (3) Returns tab top order is now: Year-1 KPIs →
#                     Investor Returns → exit-cap model → seller floor
#                     → Monte Carlo → Year-by-Year Waterfall. Returns
#                     story now leads the tab; risk lenses moved down
#                     between the returns and the detailed schedules.
#                 _render_metrics gained a `render: bool = True` param
#                 to support compute-only callers.
#  v2.0.19  5/29  Two adds:
#                 (a) NEW: Value-Add CAPEX (Short Hold) calculator on
#                     the Underwriting tab. Per-year unit-renovation
#                     ramp × cost/unit × monthly rent bump → total
#                     CAPEX, cumulative rent ↑ ramp table, value
#                     created at exit (rent ↑ ÷ exit cap), and $ value
#                     per $1 of CAPEX. Persisted to
#                     `value_add_capex.json` per property. Includes a
#                     formula-sanity-check caption (Brian's hunch was
#                     ×exit_cap; correct is ÷exit_cap).
#                 (b) Property Card refactor on Subject tab. Each row
#                     now resolves from the best source (rent roll →
#                     T-12 → OM → DB → manual override) with a colored
#                     provenance badge (RR / T12 / OM / 8R / Manual).
#                     New "✏️ Edit" popover next to the card heading
#                     lets Brian override any field — saved values get
#                     the Manual badge and win over auto-pulled values.
#                     "Status" row removed. Overrides persist to
#                     `property_card_overrides.json` per property.
#
#  v2.0.20  5/29  Four asks:
#                 (a) Central SECTION_HELP dict in ui/components.py +
#                     section_card chokepoint render a small ⓘ popover
#                     next to every heading that has a help entry.
#                     Hover = short tooltip. Click = inline detailed
#                     popover with formula / definition. ~30 sections
#                     covered out of the gate (Subject, P&M, UW,
#                     Returns, Diligence, Owner Portal, Exec Summary).
#                     CSS scoped via .v2-section-help-mark marker.
#                 (b) 5-Year Cash Flow moved from Underwriting tab to
#                     the TOP of the Summary tab (above the renamed
#                     Executive Summary card and above the Artifact
#                     Engine).
#                 (c) Summary tab "Preview" section renamed to
#                     "{property name} — Executive Summary"; the
#                     duplicate ### markdown heading below it removed.
#                 (d) CAPEX caption: dropped the "(NOT × exit cap — that
#                     would shrink the number instead of capitalizing
#                     it)" parenthetical per Brian.
#
#  v2.0.21  5/29  Hotfix pair:
#                 (a) Notes "lost data" fix on Subject tab. The old
#                     re-hydrate guard (`if notes_key not in
#                     st.session_state`) only loaded from disk on FIRST
#                     render of a folder. If session_state[notes_key]
#                     was initialized to "" during a brief render with
#                     folder=None (or a transient load failure), the
#                     textarea stayed empty even though notes.txt on
#                     disk had real content. Brian saw "where did all
#                     of my data go?" — the data was always on disk
#                     (2,602 bytes for Crossroads), just not re-loaded.
#                     New guard: also re-hydrate when widget is
#                     empty/whitespace AND disk has real content.
#                     Doesn't trample in-flight typing (which is
#                     non-whitespace).
#                 (b) Removed the example URL placeholder
#                     "https://www.andoverapts.com/floorplans" from the
#                     Add-URL text input on the Performance & Market →
#                     Rent Listing URLs panel. Field renders empty.
#
#  v2.0.22  5/29  Property Card Edit dialog upgrades:
#                 (a) Type → dropdown of 13 multifamily product types
#                     (Garden-Style, Townhomes, Walk-Up, Low/Mid/High-
#                     Rise, Cottages, Mixed-Use, Student, Senior 55+,
#                     LIHTC, SFR/BTR, MHC).
#                 (b) PM Software → dropdown of top-7 PMS in multifamily
#                     (AppFolio, Yardi, RealPage, Entrata, ResMan,
#                     Buildium, Rent Manager) + Other.
#                 (c) Rent / Sqft removed from Edit form — auto-
#                     computed from avg_rent ÷ avg_sqft and rendered
#                     with a 🔵 Calc badge on the card.
#                 (d) Market + Submarket removed from Edit form — Brian
#                     doesn't want to manually choose these; they
#                     auto-pull from the DB (Census + Norfolk Air
#                     wiring deferred).
#                 (e) "Saved to property_card_overrides.json" caption
#                     removed — implementation detail Brian doesn't
#                     need to see.
#
#  v2.0.23  5/29  Four asks:
#                 (a) Property Card: source badges (RR / 8R / T12 /
#                     OM / Calc / Manual) moved to the LEFT of the
#                     label text. Fixed 50px badge slot keeps labels
#                     aligned even when a row has no badge.
#                 (b) Section-help ⓘ popovers were rendering oddly
#                     (Streamlit chevron icon visible inside a too-
#                     small round button — looked like a teardrop).
#                     Hidden the chevron SVG + tightened sizing to a
#                     proper 28×28 round button with centered glyph.
#                 (c) "✏️ Edit" popover at the top of the Property Card
#                     replaced with "Edit Property Card" text link at
#                     the BOTTOM of the card. Styled via the new
#                     .v2-pc-edit-link marker CSS — transparent button
#                     with gold underline, no border.
#                 (d) Underwriting tab reordered:
#                     • Value-Add CAPEX moved UP directly under Deal
#                       Dials (was below Value-Add Levers).
#                     • Refi / Exit Stress Test moved DOWN below
#                       Sensitivity (was directly after Year-1 metrics).
#                     New narrative: dials → CAPEX → rent roll → rent
#                     gap → levers → cost-seg → amortization →
#                     sensitivity → exit stress → verdict.
#
#  v2.0.24  5/29  Three fixes:
#                 (a) Section-help ⓘ + Edit Property Card link CSS
#                     were INERT — the marker selector
#                     `.v2-section-help-mark ~ div [data-testid=...]`
#                     never matched in Streamlit's real DOM because the
#                     marker is nested inside stMarkdown and has no
#                     sibling div in its parent column. Result: the
#                     popovers rendered with default chunky styling +
#                     visible chevron, which is what Brian's two
#                     screenshots showed. Real fix: use `:has()` on the
#                     column ancestor that contains the marker.
#                 (b) Notes "lost data" bulletproofing (round 3). Old
#                     v2.0.21 fix recovered when widget was empty AND
#                     disk had content — but didn't sync across browser
#                     tabs/sessions when disk changed externally. New
#                     guard tracks the last-loaded-disk content per
#                     folder; reloads from disk whenever the widget
#                     value matches the last-loaded value (meaning the
#                     user hasn't typed anything since). Combined with
#                     the empty-recovery branch, this defends against
#                     ALL three failure modes.
#                 (c) Topbar "Live · 15:28" and Calibration "FRED ·
#                     15:28" → 12-hour Eastern Time per Brian (military
#                     was hard to read). New helper `_et_clock_now()`
#                     uses zoneinfo.America/New_York so EST/EDT
#                     transitions are auto-handled; label stays "ET"
#                     year-round.
#
#  v2.0.25  5/29  Five fixes — the big "what just broke" recovery pass:
#                 (a) Page-background regression from v2.0.24 — caused by
#                     :has() selectors so broad they matched outer
#                     stVerticalBlocks and styled EVERY popover button on
#                     the page (not just the section-help ones). REPLACED
#                     the popover-based help entirely with a native HTML
#                     <details>/<summary> element. Self-contained CSS
#                     under `.v2-section-help-*` classes — can't bleed
#                     into anything. Hover = browser tooltip with short
#                     summary. Click = inline panel with full details.
#                 (b) Eight Rock logo embedded as data:image/svg+xml in
#                     the V2 topbar's upper-right corner (28px tall).
#                     Logo file at ../Logos/approved-...-05062026.svg,
#                     cached after first read.
#                 (c) Tab order: Diligence moved to RIGHT OF Summary —
#                     now reads as a post-Summary deep-dive rather than
#                     an upfront gate. New order:
#                     Subject · Market · Underwriting · Returns · Owner
#                     Portal · Summary · Diligence · IC Memo · Acquisition.
#                     Same reorder applied to V1 path for parity.
#                 (d) Documents section: file rows render FIRST (each
#                     filename is now a clickable Streamlit button that
#                     opens the file in its native app), then a size
#                     column, then 🗑️ delete. Re-parse button moved to
#                     the BOTTOM (was at the top).
#                 (e) Property Card label clarified: "Manager" → "Manager
#                     (person)" so it's obvious record/seller-supplied "Gates
#                     Hudson"-style values belong in Mgmt Company, not
#                     Manager.
#
#  v2.0.26  5/29  Five fixes batched:
#                 (a) Occupancy showed "9210.0%" because the resolver
#                     returned a percentage (92.10) but `_fmt_pct`
#                     multiplies by 100 internally. Normalized
#                     `occupancy_pct` to return a 0-1 fraction from all
#                     three branches (rent_roll / t12 / db). Now renders
#                     as 92.1% correctly.
#                 (b) Section-help <details> elements now share a `name`
#                     attribute (`v2-section-help`), making them an
#                     exclusive accordion group — opening one auto-
#                     closes the others. Supported in Chrome 120+,
#                     Safari 17.2+, Firefox 128+.
#                 (c) Eight Rock logo moved from the V2 topbar (where
#                     v2.0.25 placed it) to the upper-right of the
#                     property hero block, per Brian's fresh screenshot
#                     pointing at that spot. Two-column hero grid: left
#                     = eyebrow/name/chips, right = logo (220px max).
#                     Falls back to single-column at <900px viewports.
#                 (d) Tab visual restyle to match Brian's reference
#                     screenshot — every tab gets a numbered counter
#                     badge (CSS `counter-increment`/`counter()`), the
#                     active tab uses Eight Rock gold (#B89738) pill
#                     with white text. Streamlit's red underline
#                     suppressed. Owner Portal moved to FAR RIGHT —
#                     new tab order is Subject · Market · Underwriting
#                     · Returns · Summary · Diligence · IC Memo ·
#                     Acquisition · Owner Portal.
#                 (e) Breadcrumb "Pipeline" / "Active Deals" links are
#                     now real anchors that navigate to `?home=1`. The
#                     query-param handler treats that flag as an
#                     explicit reset → clears `selected_property_id` so
#                     the inventory view re-renders. Hover underline in
#                     Eight Rock gold.
#
#  v2.0.27  5/29  Tab restructure + Diligence-tab inspector:
#                 (a) Tabs collapsed from 9 → 7. IC Memo tab DELETED;
#                     its renderer now runs at the BOTTOM of Summary.
#                     Acquisition tab DELETED; its renderer now runs at
#                     the TOP of Diligence. New tab order: Subject ·
#                     Underwriting · Returns · Market · Summary ·
#                     Diligence · Owner Portal.
#                 (b) Tab-watcher JS extended from "just Subject" to
#                     every tab — body class is now v2-on-<slug> where
#                     slug ∈ {subject, underwriting, returns, market,
#                     summary, diligence, owner-portal}.
#                 (c) Diligence inspector gated to body.v2-on-diligence
#                     → ONLY visible on the Diligence tab. Block also
#                     gained per-category breakdown rows (Title &
#                     closing, Financial, Physical, Environmental, etc.)
#                     plus graceful "—" rendering when the property is
#                     still UNSCORED.
#                 (d) gather_metrics now reads the actual DDState dataclass
#                     attributes (overall_risk_score, items, dealbreakers,
#                     category_scores) instead of the stale dict-style
#                     access that always returned None.
#
#  v2.0.28  5/29  Value-Add CAPEX year count is now DYNAMIC to the deal's
#                 hold period (was hardcoded 5). When the Hold period
#                 slider on Deal Dials is at 7, the CAPEX section shows
#                 7 input boxes (Yr 1 .. Yr 7) AND the ramp table walks
#                 through 7 years. Defaults to 5 if hp is missing.
#                 Clamped to [1, 15]. Saved plans pad/truncate to match.
#                 (Brian flagged this twice — sorry for the slip.)
#
#  v2.0.29  5/29  V2 INVENTORY LANDING PAGE shipped — fixes the
#                 "breadcrumb click → empty 'Pick a property' screen"
#                 issue. Replaces the bare st.info call when no
#                 property is selected.
#                 Three landing-page sections:
#                   (1) Hero — "Workbench V2." + property count phrase
#                       + ROTATING REAL-ESTATE QUOTE (12 quotes from
#                       Carnegie, Kiyosaki, Roosevelt, Rockefeller,
#                       Twain, Buffett, Drucker, FDR, Glickman, etc.).
#                       Date-deterministic — same quote all day, rotates
#                       at midnight.
#                   (2) Search box at the top per Brian's markup —
#                       filters by name / address / city.
#                   (3) "Recently viewed" grid above the full inventory.
#                       Recent views tracked in `_recent_views.json`
#                       next to the Properties folder; capped at 8
#                       entries; deduped; persists across sessions and
#                       across V1↔V2.
#                 Property cards are responsive (auto-fill at 320px+).
#                 Click navigates to `?prop=<id>` — no Streamlit
#                 handler needed.
#                 Recording wired in app.py so every property page view
#                 pushes the id to the front of the recent-views list.
#
#  v2.0.30  5/29  Year-1 KPIs sanity-flag callouts (Expense ratio, Negative
#                 leverage, DSCR < stress floor, Debt Yield < 6%) moved from
#                 between Row 3 and NOI Trend to the BOTTOM of the Year-1
#                 KPIs section. Reads as data first → warnings last.
#                 Computation stays in place so `er` is still returned in
#                 the metrics dict.
#
#  v2.0.31  5/29  Landing-page search FIX. v2.0.29 was calling
#                 `list_properties(limit=500)` and then filtering in
#                 Python — but the DB has 2,530 properties, so anything
#                 past row 500 was silently dropped. Crossroads
#                 Townhomes happened to fall past the cap, which is
#                 why Brian's "Crossroads" search returned "No
#                 properties match." Real fix:
#                   (a) Pass user's query straight to the DB via the
#                       `search=` kwarg — DB does the matching across
#                       name + address + city + market + owner +
#                       manager (whatever its FTS index covers).
#                   (b) Recently-viewed properties are now explicitly
#                       fetched by ID via get_property() so they always
#                       appear in the Recently viewed section, even if
#                       their row sits past the no-search cap.
#                   (c) Hero count phrase now shows the TRUE inventory
#                       size (was showing the cap, "500 properties to
#                       walk through.").
#
#  v2.0.32  5/29  Topbar + Find-anything fixes:
#                 (a) Breadcrumb collapsed from "Pipeline / Active
#                     Deals / {prop}" to "Search / {prop}". The
#                     "Search" link is a real anchor with class
#                     .v2-nav-search-trigger that opens the ⌘K palette.
#                 (b) Find anything… bar reliably opens the palette on
#                     click. The previous inline onclick attribute was
#                     intercepted/stripped by Streamlit's React layer
#                     in some scenarios; replaced with a delegated
#                     capture-phase listener on document that fires
#                     whenever any .v2-nav-search-trigger element is
#                     clicked. Survives Streamlit reruns.
#                 (c) Cmd+K / Ctrl+K keyboard shortcut already worked
#                     via the existing capture-phase keydown listener;
#                     confirmed wired + functional.
#
#  v2.0.33  5/29  Hide technical chrome from the user surface:
#                 (a) Key Documents inspector now filters out ALL
#                     app-internal state files. Brian saw "JSO ·
#                     acquisition-checklist.json · 5/29" appear as a
#                     "key document" — that's app plumbing, not
#                     something he uploaded. Filter blocks: all .json
#                     extensions in the property folder, plus the
#                     known control-file names (deal/sources/sales/
#                     mystery_shops/value_add_capex/property_card_
#                     overrides/acquisition-checklist/due_diligence/
#                     dd_state/owner_portal/investors/events/term_sheet
#                     /_recent_views/_favorites). Underscore-prefixed
#                     names also hidden by convention.
#                 (b) Inspector "Macro Context" block REMOVED ("Remove
#                     this from all screens" per Brian's screenshot).
#                     The debt-yield + city/submarket details surface
#                     on Underwriting → Market Calibration instead.
#                 (c) User-visible captions that mentioned internal
#                     storage paths cleaned up: "saved to notes.txt"
#                     → "auto-saved as you type"; "saved per-property
#                     to value_add_capex.json" → "saves automatically
#                     for each property". (Implementation detail
#                     leaks per the new feedback_no_technical_details
#                     memory.)
#
#  v2.0.34  5/29  Two visual fixes:
#                 (a) Tab restyle to match Brian's new reference — full
#                     pill (border-radius: 999px), DARK NAVY active tab
#                     with white text + dark badge (was Eight Rock gold
#                     in v2.0.26). Inactive tabs unchanged. Bottom
#                     border-line removed; tabs float as pills.
#                 (b) Section-help ⓘ popover panel now opens RIGHTWARD
#                     (`left: 0`) from the trigger instead of leftward
#                     (`right: 0`). The trigger sits at the LEFT of a
#                     section title, so opening rightward keeps the
#                     panel inside the card. Width clamped to
#                     `min(380px, viewport - 32px)` so it never bleeds
#                     off the right edge either. Fixes Brian's
#                     screenshot of the Comparables help getting
#                     clipped off the left side of the page.
#
#                 NOTE for Brian: the v2.0.32 topbar fixes (Search /
#                 {prop} breadcrumb, click-anywhere on Find Anything,
#                 Cmd+K) are already in source. The version pill in
#                 his screenshot still says v2.0.31 — that means the
#                 V2 server (port 8502) hasn't been restarted since
#                 the v2.0.32 patch landed. Restart V2 (stop + start
#                 the launcher) to pick up everything.
#
#  v2.0.35  5/29  Tab styling tuned closer to Brian's reference shot:
#                 (a) Inactive tab text now full INK color (was the
#                     gray ink_3).
#                 (b) Number badges are warm-cream `#F5F0E4` boxes with
#                     no border (was light-gray + thin border).
#                 (c) Tab pills slightly more substantial — 10×18
#                     padding, 14px font, 6px gap between tabs.
#                 (d) Active badge translucency bumped from 12% to 16%
#                     so the "1" reads cleanly on the dark pill.
#
#  v2.0.36  5/29  Search palette FIX (the real fix) + tab contrast bump:
#                 (a) Root cause: the v2.0.32 delegated click listener
#                     DID fire, but `open()` was calling
#                     `classList.add('show')` on a STALE overlay
#                     reference. Streamlit reruns rebuild the topbar +
#                     palette DOM; the IIFE's cached `overlay`/`input`
#                     /`list`/`empty`/`sectionLabel` references then
#                     pointed at detached nodes. Add to detached node
#                     = no visible change.
#                 Fix: do NOT cache element references. Look every
#                 element up fresh inside each function via tiny
#                 getOverlay() / getInput() / etc. helpers that hit
#                 `document.getElementById` each call. Also moved the
#                 input + overlay-backdrop click listeners off the
#                 cached refs onto delegated document listeners so
#                 they survive Streamlit reruns. open() also retries
#                 once after 50ms if the palette DOM hasn't rendered
#                 yet (race with first paint).
#                 (b) Active tab text contrast: the dark-navy pill +
#                     white text combo should be high-contrast, but
#                     Streamlit was injecting a lighter color on the
#                     inner <p>/<div>/<span>. Cascade
#                     `color: #FFFFFF !important` + `font-weight: 700`
#                     onto every descendant; bump active badge
#                     translucency 0.16 → 0.20.
#
#  v2.0.37  5/29  Three asks:
#                 (a) Seller Floor Reverse-Engineering now AUTO-FILLS
#                     from the property's Sale History (the same data
#                     the Subject tab shows) before falling back to
#                     the assessor ETL row. Reads sales.json via the
#                     existing load_sales helper, picks the latest
#                     real (>$100K) sale by year. New caption tells
#                     the user where the auto-fill came from.
#                 (b) Investors panel: Email column + Email field in
#                     the Add form so IR-update sends can target each
#                     investor directly. Email also stored per-investor
#                     in the ledger.
#                 (c) Investors panel: "✏️ Edit / delete an investor"
#                     expander lets Brian update any investor's name /
#                     kind / commitment / email / notes, OR delete
#                     them (two-step confirm via checkbox; also drops
#                     all events that reference the investor and
#                     re-rolls totals).
#                 Backed by new helpers in core/lp_gp_ledger.py:
#                 update_investor() + remove_investor(); Investor
#                 dataclass gained an `email` field with
#                 to_dict/from_dict roundtrip.
#                 (d) Cleaned up the last user-visible technical leak
#                     ("layout not recognized — populate sources.json
#                     manually") + other JSON path mentions in toasts,
#                     SECTION_HELP entries, and inspector panel copy
#                     per the no-technical-details memory.
#
#  v2.0.38  5/29  Calibration inspector readability — the wrap fix.
#                 Brian's screenshot showed two awkward wraps in the
#                 narrow right-rail column:
#                   (1) "FRED · 5:18 PM ET" → "ET" dropped to a 2nd
#                       line. Fix: `white-space: nowrap` on the pip.
#                   (2) "$ / unit vs submkt" row label + value were
#                       breaking across lines. Fixes:
#                       - Renamed label to "$/Unit vs submkt"
#                         (shorter, capitalized U for clarity).
#                       - Right-rail comparison trimmed from
#                         "vs $/u ceiling" to "vs ceiling".
#                       - Row CSS now `gap: 10px`, `white-space:
#                         nowrap` on the value side, `text-overflow:
#                         ellipsis` on the label so anything that's
#                         still too long truncates cleanly instead of
#                         wrapping to a 2nd line.
#                 Applies to every inspector row (Calibration,
#                 Diligence, etc.) — they share the .v2-ins-row class.
#
#  v2.1.0   5/30  *** MAJOR: multi-state expansion + product rename. ***
#                 (1) RENAME: the product is now "QUARRIE" (was
#                     "Workbench"). Topbar wordmark + landing hero
#                     updated. Tagline "Where Eight Rock breaks ground."
#                     Backend module names left unchanged (no churn).
#                 (2) DATA: ingested the full multi-state licensed export library
#                     (VA·NC·SC·GA·TN + national mgmt portfolios) —
#                     2,530 → 13,657 properties, deduped by provider API-Id
#                     UUID. New columns: asset_type (Brian's tag rule),
#                     property_segment (Conventional/Affordable/Senior/
#                     Student/Military), market_description, owner_fax,
#                     area_supervisor, corp_mgmt_id, last_sold_*,
#                     assessed_value_per_unit, source_file. Live deals
#                     (Crossroads etc.) preserved via the custom-prop
#                     merge.
#                 (3) SEARCH: the sidebar + inventory geography filters
#                     are now DATA-DRIVEN State→City cascades (were
#                     hardcoded to 7 Hampton Roads cities). Default =
#                     "All target states (VA·NC·SC·GA·TN)"; HR remains a
#                     one-click preset. New db.py helpers:
#                     list_distinct_states(target_first), city_counts_
#                     for_state(), + state/cities filters on
#                     list_properties()/count_properties().
#  v2.1.1   5/31  *** SEARCH ROOT-CAUSE FIX (failed 4x before). ***
#                 Streamlit does NOT execute <script> injected via
#                 st.markdown — so every prior JS command-palette never
#                 ran (click + ⌘K listeners were never attached). Fixed
#                 two ways that DO work:
#                 (1) "Search" breadcrumb + "Find anything…" bar are now
#                     plain <a href="?home=1"> links → real browser
#                     navigation to the full searchable inventory
#                     landing (native Streamlit search box). Zero JS.
#                 (2) ⌘K/Ctrl+K via st.components.v1.html (a 0-height
#                     iframe — the only way Streamlit runs your JS). Its
#                     same-origin script attaches a keydown listener to
#                     window.parent.document and navigates to ?home=1.
#                     Keys 1-9 click the parent's Streamlit tabs.
#                 render_v2_cmdk_palette() rewritten from the dead
#                 240-line overlay+script to the compact iframe bridge.
#
#  v2.1.2   5/31  *** SEARCH IS NOW A REAL IN-PLACE FIELD (5th ask). ***
#                 The "Find anything…" bar is a native st.text_input you
#                 click into and type — matching properties drop down
#                 right under the bar (no navigating to another page).
#                 Built by rebuilding the topbar as a Streamlit columns
#                 row (brand | search input | status) so the search is a
#                 first-class widget, not faked HTML. Results = HTML
#                 anchor cards → ?prop=<id> opens that property. ⌘K now
#                 FOCUSES the field (was: navigate to ?home=1). Opening a
#                 property clears the box so the dropdown dismisses.
#
# Internal theme-feature ratchet ONLY (test_v2_exhaustive asserts each
# feature phase against it). NEVER shown to the owner: the topbar pill
# is the truth-teller for what code is running, so everything displayed
# uses the real WORKBENCH_VERSION below. (The pill showing a stale
# hard-coded "v2.1.4" misled the 2026-07-31 rollout debug.)
V2_VERSION = "v2.1.4"

from config import WORKBENCH_VERSION as _WB_VERSION


def get_v2_version_label() -> str:
    """Full version label shown in the topbar version pill + V1 switch
    button tooltip. Uses the one real WORKBENCH_VERSION."""
    today = dt.date.today().strftime("%m%d%Y")
    return f"{_WB_VERSION} ({today}) · Quiet Operator"


def get_v2_version_short() -> str:
    """Short form for the topbar pill — the real workbench version."""
    return _WB_VERSION


# ---------------------------------------------------------------------------
# Theme injection — overlay V2 design tokens onto Streamlit defaults
# ---------------------------------------------------------------------------

def render_v2_active_banner() -> None:
    """A bright, unmissable banner at the top of the page confirming V2 mode
    is active. Brian asked for this 2026-05-29 after V1 and V2 looked
    identical (because his run.bat had a quoting bug that silently kept
    ER_THEME unset). The banner is the visual contract: if you see it,
    V2 is on. If you don't see it, V2 mode failed to activate.
    """
    html = """
<div style="
  position: relative; z-index: 100;
  margin: -1rem -2rem 0 -2rem;
  padding: 8px 24px;
  background: linear-gradient(90deg, #B89738 0%, #D4B14B 50%, #B89738 100%);
  color: #0A1628;
  font-family: 'Inter', -apple-system, system-ui, sans-serif;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  text-align: center;
  box-shadow: 0 1px 4px rgba(184,151,56,0.30);
">
  ✨ V2.0 &lsquo;Quiet Operator&rsquo; Active &middot; restyle preview &middot;
  <a href="http://localhost:8501/" style="color: #0A1628; text-decoration: underline; font-weight: 700;">click here for V1</a>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


def inject_v2_theme() -> None:
    """Inject V2 design tokens + font imports + Streamlit overrides.

    Called once near the top of app.py when ER_THEME=v2. Runs AFTER
    _inject_branding() so we can win the cascade with `!important` selectors.
    """
    v = V2
    css = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">

<style>
/* ============================================================
   V2 "Quiet Operator" — Eight Rock Workbench restyle
   Activated by ER_THEME=v2. Overrides V1's _inject_branding CSS.
   ============================================================ */

/* --- V2 design tokens as CSS variables for inline-style consumers --- */
:root {{
  --v2-bg: {v['bg']};
  --v2-bg-soft: {v['bg_soft']};
  --v2-card: {v['card']};
  --v2-ink: {v['ink']};
  --v2-ink-2: {v['ink_2']};
  --v2-ink-3: {v['ink_3']};
  --v2-ink-4: {v['ink_4']};
  --v2-gold: {v['gold']};
  --v2-gold-bright: {v['gold_bright']};
  --v2-gold-soft: {v['gold_soft']};
  --v2-gold-deep: {v['gold_deep']};
  --v2-line: {v['line']};
  --v2-line-faint: {v['line_faint']};
  --v2-pos: {v['pos']};
  --v2-warn: {v['warn']};
  --v2-neg: {v['neg']};
  --v2-blue: {v['blue']};
}}

/* --- Base typography: Inter + JetBrains Mono --- */
html, body, .stApp, [data-testid="stApp"],
[data-testid="stAppViewContainer"], [data-testid="stMain"],
.block-container, .main, .main > div {{
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif !important;
  font-feature-settings: 'cv11','ss01','tnum' !important;
  -webkit-font-smoothing: antialiased !important;
}}

/* --- Force V2 light bg everywhere --- */
html, body,
.stApp, [data-testid="stApp"],
[data-testid="stAppViewContainer"], [data-testid="stMain"],
.main, .main > .block-container, .main > div {{
  background: {v['bg']} !important;
  color: {v['ink']} !important;
}}

/* Content gutters — wider, more breathing room */
.block-container {{
  padding-top: 1.25rem !important;
  padding-bottom: 3rem !important;
  max-width: 1640px !important;
}}

/* --- Headings — V2 weight/letter-spacing --- */
.block-container h1 {{
  font-family: 'Inter', sans-serif !important;
  font-size: 56px !important;
  font-weight: 700 !important;
  letter-spacing: -0.025em !important;
  line-height: 1.04 !important;
  color: {v['ink']} !important;
  margin-bottom: 16px !important;
  border-bottom: none !important;
  padding-bottom: 0 !important;
}}
.block-container h2 {{
  font-family: 'Inter', sans-serif !important;
  font-size: 24px !important;
  font-weight: 700 !important;
  letter-spacing: -0.015em !important;
  color: {v['ink']} !important;
  border-bottom: none !important;
  padding-bottom: 0 !important;
  margin-top: 28px !important;
  margin-bottom: 12px !important;
}}
.block-container h3 {{
  font-family: 'Inter', sans-serif !important;
  font-size: 18px !important;
  font-weight: 600 !important;
  letter-spacing: -0.005em !important;
  color: {v['ink']} !important;
  margin-top: 20px !important;
  margin-bottom: 8px !important;
  border-bottom: none !important;
  border-left: none !important;
  padding-left: 0 !important;
}}
.block-container h4, .block-container h5 {{
  font-family: 'Inter', sans-serif !important;
  font-size: 14px !important;
  font-weight: 600 !important;
  letter-spacing: 0.04em !important;
  text-transform: uppercase !important;
  color: {v['ink_3']} !important;
  border-left: none !important;
  padding-left: 0 !important;
  margin-top: 16px !important;
  margin-bottom: 8px !important;
}}

/* --- Body text --- */
.block-container p, .block-container li,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {{
  color: {v['ink_2']} !important;
  font-size: 14px !important;
  line-height: 1.55 !important;
}}
.block-container strong, .block-container b,
[data-testid="stMarkdownContainer"] strong,
[data-testid="stMarkdownContainer"] b {{
  color: {v['ink']} !important;
  font-weight: 600 !important;
}}

/* --- All monospace numerics --- */
.v2-mono, [class*="v2-mono"],
code, kbd, samp,
[data-testid="stCode"], [data-testid="stCodeBlock"] code {{
  font-family: 'JetBrains Mono', Menlo, monospace !important;
  font-feature-settings: 'tnum' !important;
}}

/* --- TABS — pill row look --- */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  background: {v['card']} !important;
  border: 1px solid {v['line']} !important;
  border-radius: 999px !important;
  padding: 5px !important;
  gap: 4px !important;
  box-shadow: 0 1px 3px rgba(10,22,40,0.04) !important;
  width: fit-content !important;
  margin: 12px 0 24px 0 !important;
}}
[data-testid="stTabs"] [data-baseweb="tab"] {{
  background: transparent !important;
  border: none !important;
  border-radius: 999px !important;
  /* Tighter padding so all 9 tabs fit in the 3/4-width main column
     without horizontal overflow (Brian 5/29 v2.0.13). */
  padding: 7px 13px !important;
  font-family: 'Inter', sans-serif !important;
  font-size: 12px !important;
  font-weight: 500 !important;
  color: {v['ink_3']} !important;
  height: auto !important;
  min-height: 0 !important;
  transition: color 0.15s !important;
  white-space: nowrap !important;
}}
/* Pill row can grow up to its content width, but never overflow horizontally.
   Allow it to fill the available column width so all 9 pills stay on one row. */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
  flex-wrap: nowrap !important;
  width: 100% !important;
  overflow-x: auto !important;
  overflow-y: hidden !important;
  scrollbar-width: thin !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-list"]::-webkit-scrollbar {{
  height: 4px !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-list"]::-webkit-scrollbar-thumb {{
  background: {v['line']} !important;
  border-radius: 2px !important;
}}
[data-testid="stTabs"] [data-baseweb="tab"]:hover {{
  color: {v['ink']} !important;
  background: transparent !important;
}}
[data-testid="stTabs"] [aria-selected="true"] {{
  background: {v['ink']} !important;
  color: white !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{
  display: none !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-border"] {{
  display: none !important;
}}

/* --- BUTTONS — V2 style --- */
.stButton > button, .stDownloadButton > button {{
  font-family: 'Inter', sans-serif !important;
  background: {v['card']} !important;
  color: {v['ink_2']} !important;
  border: 1px solid {v['line']} !important;
  border-radius: 10px !important;
  padding: 8px 16px !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  transition: all 0.15s !important;
  box-shadow: 0 1px 2px rgba(10,22,40,0.04) !important;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  border-color: {v['gold']} !important;
  background: {v['gold_soft']} !important;
  color: {v['ink']} !important;
}}
.stButton > button[kind="primary"] {{
  background: {v['ink']} !important;
  color: white !important;
  border-color: {v['ink']} !important;
}}
.stButton > button[kind="primary"]:hover {{
  background: {v['gold']} !important;
  color: {v['ink']} !important;
  border-color: {v['gold']} !important;
}}

/* --- INPUTS / SELECTS / NUMERICS --- */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input,
[data-baseweb="select"] > div {{
  background: {v['card']} !important;
  border: 1px solid {v['line']} !important;
  border-radius: 8px !important;
  color: {v['ink']} !important;
  font-family: 'Inter', sans-serif !important;
}}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stNumberInput"] input:focus,
[data-baseweb="select"] > div:focus-within {{
  border-color: {v['gold']} !important;
  box-shadow: 0 0 0 3px {v['gold_soft']} !important;
}}

/* Widget labels — smaller, less shouty */
[data-testid="stWidgetLabel"] {{
  font-size: 12px !important;
  font-weight: 500 !important;
  color: {v['ink_3']} !important;
  letter-spacing: 0.02em !important;
}}

/* --- METRIC widget --- */
[data-testid="stMetric"] {{
  background: {v['card']} !important;
  border: 1px solid {v['line']} !important;
  border-radius: 16px !important;
  padding: 18px 22px !important;
}}
[data-testid="stMetricLabel"] {{
  font-size: 11px !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: {v['ink_3']} !important;
  font-weight: 600 !important;
}}
[data-testid="stMetricValue"] {{
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 30px !important;
  font-weight: 700 !important;
  color: {v['ink']} !important;
  letter-spacing: -0.02em !important;
}}
[data-testid="stMetricDelta"] {{
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 12px !important;
}}

/* --- DATAFRAME / TABLE --- */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
  border: 1px solid {v['line']} !important;
  border-radius: 12px !important;
  overflow: hidden !important;
  background: {v['card']} !important;
}}
[data-testid="stDataFrame"] thead th,
[data-testid="stTable"] thead th {{
  background: {v['bg_soft']} !important;
  color: {v['ink_3']} !important;
  font-family: 'Inter', sans-serif !important;
  font-size: 10px !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  font-weight: 600 !important;
  border-bottom: 1px solid {v['line']} !important;
}}
[data-testid="stDataFrame"] tbody td,
[data-testid="stTable"] tbody td {{
  font-family: 'Inter', sans-serif !important;
  color: {v['ink']} !important;
  font-size: 13px !important;
  border-bottom: 1px solid {v['line_faint']} !important;
}}

/* --- EXPANDERS --- */
[data-testid="stExpander"] {{
  background: {v['card']} !important;
  border: 1px solid {v['line']} !important;
  border-radius: 12px !important;
  overflow: hidden !important;
}}
[data-testid="stExpander"] summary {{
  font-family: 'Inter', sans-serif !important;
  font-size: 14px !important;
  font-weight: 600 !important;
  color: {v['ink']} !important;
}}

/* --- ALERTS --- */
[data-testid="stAlertContentInfo"],
[data-baseweb="notification"][kind="info"] {{
  background: {v['blue_soft']} !important;
  color: {v['blue']} !important;
  border: 1px solid #BFDBFE !important;
  border-radius: 10px !important;
}}
[data-testid="stAlertContentSuccess"],
[data-baseweb="notification"][kind="positive"] {{
  background: {v['pos_soft']} !important;
  color: {v['pos']} !important;
  border: 1px solid #A7F3D0 !important;
  border-radius: 10px !important;
}}
[data-testid="stAlertContentWarning"],
[data-baseweb="notification"][kind="warning"] {{
  background: {v['warn_soft']} !important;
  color: {v['warn']} !important;
  border: 1px solid #FCD34D !important;
  border-radius: 10px !important;
}}
[data-testid="stAlertContentError"],
[data-baseweb="notification"][kind="negative"] {{
  background: {v['neg_soft']} !important;
  color: {v['neg']} !important;
  border: 1px solid #FCA5A5 !important;
  border-radius: 10px !important;
}}

/* --- CAPTIONS (st.caption) --- */
[data-testid="stCaptionContainer"] {{
  font-size: 12px !important;
  color: {v['ink_3']} !important;
  font-style: normal !important;
}}

/* --- SIDEBAR (V2 light) --- */
section[data-testid="stSidebar"] {{
  background: {v['bg_soft']} !important;
  border-right: 1px solid {v['line']} !important;
}}
section[data-testid="stSidebar"] > div {{
  background: {v['bg_soft']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] span:not([style*="color"]),
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] strong,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h4 {{
  color: {v['ink']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {{
  color: {v['ink_3']} !important;
  font-size: 11px !important;
}}
section[data-testid="stSidebar"] [data-testid="stTextInput"] input,
section[data-testid="stSidebar"] [data-testid="stTextArea"] textarea,
section[data-testid="stSidebar"] [data-testid="stNumberInputContainer"] input,
section[data-testid="stSidebar"] [data-baseweb="select"] > div {{
  background: {v['card']} !important;
  border-color: {v['line']} !important;
  color: {v['ink']} !important;
}}
/* Property buttons in sidebar — V2 look */
section[data-testid="stSidebar"] [data-testid="stButton"] > button {{
  background: {v['card']} !important;
  border: 1px solid {v['line']} !important;
  color: {v['ink']} !important;
  border-radius: 8px !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {{
  background: {v['gold_soft']} !important;
  border-color: {v['gold']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {{
  background: {v['gold_soft']} !important;
  border: 1px solid {v['gold']} !important;
  border-left: 3px solid {v['gold']} !important;
}}
section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] p {{
  color: {v['gold_deep']} !important;
  font-weight: 600 !important;
}}

/* --- HIDE the V1 top bar that _inject_branding adds (we replace it) --- */
.er-topbar {{ display: none !important; }}

/* --- HIDE V1's left sidebar in V2 mode --- */
/* Brian's screenshot has no sidebar -- pure single-column. Property nav
   happens via ⌘K palette (which loads all 2,530 VA properties). Module
   nav (CRM / Portfolio / Help) is still reachable via Switch-to-V1. */
section[data-testid="stSidebar"] {{ display: none !important; }}
[data-testid="stSidebarCollapsedControl"] {{ display: none !important; }}
[data-testid="collapsedControl"] {{ display: none !important; }}
button[kind="header"][aria-label*="sidebar"] {{ display: none !important; }}
/* Reclaim the horizontal space the sidebar used to take */
[data-testid="stAppViewContainer"] > section.main {{
  margin-left: 0 !important;
  padding-left: 0 !important;
}}
[data-testid="stMain"] {{ margin-left: 0 !important; }}

/* --- V2 nav: "Find anything…" search button (center of the top bar) --- */
.v2-nav-search {{
  display: inline-flex; align-items: center; gap: 10px;
  padding: 7px 12px 7px 14px;
  background: {v['card']};
  border: 1px solid {v['line']};
  border-radius: 10px;
  color: {v['ink_3']};
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  cursor: pointer;
  min-width: 300px; max-width: 480px;
  flex: 1;
  text-decoration: none;
  transition: all 0.15s;
}}
.v2-nav-search:hover {{
  border-color: {v['gold']};
  background: {v['gold_soft']};
  color: {v['ink']};
}}
.v2-nav-search .icon {{ color: {v['ink_3']}; flex-shrink: 0; }}
.v2-nav-search .kbd-hint {{
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  background: {v['bg_soft']};
  border: 1px solid {v['line']};
  border-bottom-width: 2px;
  padding: 1px 6px;
  border-radius: 4px;
  color: {v['ink_3']};
  font-weight: 500;
}}
/* Make the nav grid-flex so search button fills the middle */
.v2-nav {{
  display: flex !important; align-items: center; gap: 20px;
}}
.v2-nav .v2-nav-brand {{ flex-shrink: 0; }}
.v2-nav .v2-nav-crumbs {{ flex-shrink: 0; }}
.v2-nav .v2-nav-search {{ flex: 1; }}
.v2-nav .v2-nav-tag {{ flex-shrink: 0; }}
.v2-nav .v2-avatar {{ flex-shrink: 0; }}

/* ===== Brian 5/31 v2.1.2 — REAL in-place global search ===== */
/* The topbar is now a Streamlit columns row (brand | search input |
   status). Paint the row as the glass bar via the .v2-topbar-mark
   marker so it still reads as one chrome strip. */
[data-testid="stHorizontalBlock"]:has(.v2-topbar-mark) {{
  background: {v['bg_soft']};
  border: 1px solid {v['line']};
  border-radius: 14px;
  padding: 8px 16px;
  margin-bottom: 6px;
  align-items: center;
}}
.v2-topbar-mark {{ display: none; }}
.v2-nav-inline {{ display: flex; align-items: center; gap: 14px; }}
.v2-nav-inline.v2-nav-right {{ justify-content: flex-end; gap: 10px; flex-wrap: wrap; }}
.v2-nav-inline .v2-nav-crumbs .here {{ color: {v['ink']}; font-weight: 600; }}

/* The search input itself — styled as the rounded "Find anything" pill.
   Scoped to the middle column via the .v2-search-mark marker. */
[data-testid="stColumn"]:has(.v2-search-mark) [data-testid="stTextInput"] > div {{
  background: transparent;
}}
[data-testid="stColumn"]:has(.v2-search-mark) [data-testid="stTextInput"] input {{
  background: {v['card']} !important;
  border: 1px solid {v['line']} !important;
  border-radius: 999px !important;
  padding: 9px 18px !important;
  font-size: 14px !important;
  color: {v['ink']} !important;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04) !important;
  transition: border-color 120ms ease, box-shadow 120ms ease;
}}
[data-testid="stColumn"]:has(.v2-search-mark) [data-testid="stTextInput"] input:focus {{
  border-color: {v['gold']} !important;
  box-shadow: 0 0 0 3px rgba(184,151,56,0.16) !important;
  outline: none !important;
}}
[data-testid="stColumn"]:has(.v2-search-mark) [data-testid="stTextInput"] input::placeholder {{
  color: {v['ink_3']} !important;
}}

/* In-place results dropdown (renders right under the bar). */
.v2-search-results {{
  background: {v['card']};
  border: 1px solid {v['line']};
  border-radius: 12px;
  box-shadow: 0 10px 30px rgba(15,23,42,0.12);
  padding: 6px;
  margin: 2px 0 10px 0;
  max-width: 720px;
}}
.v2-search-hd {{
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.6px;
  color: {v['ink_3']}; font-weight: 700; padding: 6px 10px 4px;
}}
.v2-search-empty {{
  font-size: 13px; color: {v['ink_3']}; padding: 12px 10px;
}}
.v2-search-item {{
  display: grid;
  grid-template-columns: 1.4fr 1.6fr auto;
  align-items: baseline;
  gap: 12px;
  padding: 9px 12px;
  border-radius: 8px;
  text-decoration: none !important;
  color: inherit;
  transition: background-color 100ms ease;
}}
.v2-search-item:hover {{ background: {v['gold_soft']}; }}
.v2-search-item .nm {{ font-size: 14px; font-weight: 600; color: {v['ink']}; }}
.v2-search-item .lo {{ font-size: 12px; color: {v['ink_3']}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.v2-search-item .mt {{ font-size: 11px; color: {v['gold_deep']}; font-family: 'JetBrains Mono', monospace; white-space: nowrap; text-align: right; }}

/* Brian 5/29 v2.0.25 — Eight Rock logo in the upper-right corner of
   the topbar. Constrained height so it sits on the same row as the
   rest of the nav chrome. */
.v2-nav .v2-nav-logo {{
  flex-shrink: 0;
  height: 28px;
  width: auto;
  margin-left: 6px;
  display: block;
}}

/* --- V2 version pill (small monospace, next to BM avatar) --- */
.v2-version-pill {{
  display: inline-flex; align-items: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: {v['ink_3']};
  background: {v['bg_soft']};
  border: 1px solid {v['line']};
  padding: 3px 8px;
  border-radius: 999px;
  cursor: help;
}}
.v2-version-pill:hover {{ background: {v['gold_soft']}; color: {v['gold_deep']}; }}

/* --- Hide verdict band on Subject tab (Brian 2026-05-29 EOD) ---
   The JS at the bottom of inject_v2_theme adds body.v2-on-subject when
   the Subject tab (index 0) is the active Streamlit tab. We hide
   .v2-verdict there because the verdict only makes sense on tabs
   where Brian is reasoning about the numbers (Underwriting, Returns,
   IC Memo) -- on Subject he's just looking at property identity. */
body.v2-on-subject .v2-verdict {{ display: none !important; }}

/* Brian 5/29 v2.0.27 — Diligence inspector block ONLY shows on the
   Diligence tab. Tab-watcher JS sets body.v2-on-diligence when that tab
   is active. */
.v2-dd-inspector {{ display: none; }}
body.v2-on-diligence .v2-dd-inspector {{ display: block; }}

/* Brian 5/29 v2.0.29 — V2 inventory landing page styling. */
.v2-landing {{
  margin: 6px 0 12px 0;
}}
.v2-landing-row {{
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}}
.v2-landing-title {{
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: {v['ink']};
  margin: 0;
  line-height: 1.05;
}}
.v2-landing-tagline {{
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: {v['gold_deep']};
  margin: 3px 0 0 0;
}}
.v2-landing-count {{ text-align: right; }}
.v2-landing-count .num {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 30px;
  font-weight: 700;
  color: {v['gold_deep']};
  line-height: 1.05;
}}
.v2-landing-count .lbl {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: {v['ink_3']};
  font-weight: 600;
}}
.v2-landing-quote {{
  margin: 10px 0 4px 0;
  padding: 7px 14px;
  border-left: 3px solid {v['gold']};
  background: {v['gold_soft']};
  border-radius: 0 6px 6px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.v2-landing-quote-text {{
  font-size: 12.5px;
  font-style: italic;
  color: {v['ink']};
}}
.v2-landing-quote-author {{
  font-size: 11.5px;
  color: {v['gold_deep']};
  font-weight: 600;
  letter-spacing: 0.3px;
}}
.v2-landing-section-head {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: {v['ink_3']};
  font-weight: 700;
  margin: 22px 0 10px 0;
}}
.v2-prop-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  margin: 4px 0 12px 0;
}}
/* Property card, 2026-07-30 overhaul: NO underlines anywhere (the old
   cards underlined name, address, and every stat value - they read as a
   page of raw hyperlinks), gold accent rail on hover, class rendered as
   a colored chip, occupancy color-coded. */
.v2-prop-card {{
  display: block;
  position: relative;
  background: {v['card']};
  border: 1px solid {v['line']};
  border-left: 3px solid transparent;
  border-radius: 12px;
  padding: 14px 16px;
  text-decoration: none !important;
  color: inherit;
  transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
}}
.v2-prop-card * {{ text-decoration: none !important; }}
.v2-prop-card:hover {{
  border-color: {v['gold']};
  border-left-color: {v['gold']};
  box-shadow: 0 6px 22px rgba(184, 151, 56, 0.14);
  transform: translateY(-2px);
}}
.v2-prop-card-name {{
  font-size: 16px;
  font-weight: 700;
  color: {v['ink']};
  line-height: 1.25;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.v2-prop-card:hover .v2-prop-card-name {{ color: {v['gold']}; }}
.v2-prop-card-addr {{
  font-size: 11.5px;
  color: {v['ink_3']};
  margin-top: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.v2-prop-card-stats {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid {v['line']};
}}
.v2-prop-card-stats > div {{
  display: flex;
  flex-direction: column;
}}
.v2-prop-card-stats .lbl {{
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: {v['ink_3']};
  font-weight: 600;
}}
.v2-prop-card-stats .val {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 13.5px;
  font-weight: 700;
  color: {v['ink']};
  margin-top: 2px;
}}
.v2-prop-card-stats .val.occ-hi {{ color: #15803d; }}
.v2-prop-card-stats .val.occ-mid {{ color: #b45309; }}
.v2-prop-card-stats .val.occ-lo {{ color: #b91c1c; }}
.v2-cls-chip {{
  display: inline-block;
  min-width: 22px;
  text-align: center;
  padding: 1px 7px;
  border-radius: 999px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 700;
  margin-top: 2px;
}}
.v2-cls-A {{ background: #dcfce7; color: #15803d; }}
.v2-cls-B {{ background: #dbeafe; color: #1d4ed8; }}
.v2-cls-C {{ background: #fef3c7; color: #b45309; }}
.v2-cls-D {{ background: #fee2e2; color: #b91c1c; }}
.v2-cls-x {{ background: {v['line']}; color: {v['ink_3']}; }}

/* --- Hide Streamlit file_uploader's "200MB per file" hint copy ---
   It's noisy and not useful for Brian's workflow. Hide the secondary
   instruction line beneath the dropzone. */
[data-testid="stFileUploaderDropzoneInstructions"] small,
[data-testid="stFileUploaderDropzone"] small,
[data-testid="stFileUploader"] small {{ display: none !important; }}

/* --- Verdict band "Build IC packet" button --- */
.v2-verdict-act {{
  background: rgba(255,255,255,0.10);
  border: 1px solid rgba(255,255,255,0.20);
  color: white;
  padding: 11px 20px;
  border-radius: 10px;
  font-family: 'Inter', sans-serif;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex; align-items: center; gap: 8px;
  white-space: nowrap;
  transition: all 0.2s;
  position: relative;
}}
.v2-verdict-act:hover {{
  background: {v['gold']};
  color: {v['ink']};
  border-color: {v['gold']};
}}

/* ============================================================
   V2 CHROME COMPONENTS — used by the render_v2_* functions
   ============================================================ */

/* --- V2 TOPBAR --- */
.v2-nav {{
  position: sticky; top: 0; z-index: 100;
  background: rgba(250, 250, 247, 0.88);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid {v['line']};
  margin: -1rem -2rem 0 -2rem;
  padding: 14px 32px;
  display: flex; align-items: center; gap: 24px;
}}
.v2-nav-brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 13px; letter-spacing: 0.06em; color: {v['ink']}; text-decoration: none; }}
.v2-nav-brand-mark {{
  width: 26px; height: 26px; border-radius: 6px;
  background: linear-gradient(135deg, {v['gold']}, {v['gold_bright']});
  color: white; display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 800;
}}
.v2-nav-version {{ font-size: 10px; color: {v['ink_4']}; font-family: 'JetBrains Mono', monospace; padding: 2px 8px; border: 1px solid {v['line']}; border-radius: 999px; }}
.v2-nav-crumbs {{ font-size: 13px; color: {v['ink_3']}; display: flex; align-items: center; gap: 8px; flex: 1; }}
.v2-nav-crumbs .sep {{ color: {v['ink_4']}; }}
.v2-nav-crumbs .here {{ color: {v['ink']}; font-weight: 600; }}
/* Brian 5/29 v2.0.26 — breadcrumb links clickable (Pipeline / Active
   Deals both navigate to ?, which drops the ?prop= param and shows
   the inventory view). */
.v2-nav-crumbs a.v2-nav-crumb-link {{
  color: {v['ink_3']};
  text-decoration: none;
  cursor: pointer;
  border-bottom: 1px solid transparent;
  transition: color 120ms ease, border-color 120ms ease;
}}
.v2-nav-crumbs a.v2-nav-crumb-link:hover {{
  color: {v['gold_deep']};
  border-bottom-color: {v['gold']};
}}
.v2-nav-tag {{ background: {v['pos_soft']}; color: {v['pos']}; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }}
.v2-nav-tag .d {{ width: 6px; height: 6px; border-radius: 50%; background: {v['pos']}; }}
.v2-avatar {{ width: 30px; height: 30px; border-radius: 50%; background: linear-gradient(135deg, {v['ink']}, #1F2937); color: white; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; }}

/* --- V2 HERO --- */
.v2-hero {{ margin: 24px 0 32px 0; }}
.v2-hero-eyebrow {{
  font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase;
  color: {v['gold_deep']}; font-weight: 600; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}}
.v2-hero-name {{
  font-family: 'Inter', sans-serif;
  font-size: 56px; line-height: 1.04; font-weight: 700;
  letter-spacing: -0.025em;
  color: {v['ink']};
  margin-bottom: 20px;
}}
.v2-chips {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
.v2-chip {{
  display: inline-flex; align-items: center; gap: 7px;
  padding: 7px 13px; border-radius: 999px;
  background: {v['card']}; border: 1px solid {v['line']};
  font-size: 13px; color: {v['ink_2']};
  transition: all 0.15s; text-decoration: none;
}}
.v2-chip:hover {{ border-color: {v['gold']}; }}
.v2-chip b {{ color: {v['ink']}; font-weight: 600; }}
.v2-chip svg {{ color: {v['gold']}; }}
.v2-chip-link {{ cursor: pointer; }}
.v2-chip-link b {{ color: {v['gold_deep']}; }}
.v2-chip-link:hover {{ background: {v['gold_soft']}; border-color: {v['gold']}; }}
.v2-chip-link:hover b {{ color: {v['ink']}; }}

/* --- V2 STATS BAR --- */
.v2-stats {{
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
  background: {v['line']}; border-radius: 20px;
  overflow: hidden; border: 1px solid {v['line']};
  margin-bottom: 28px;
}}
.v2-stat {{ background: {v['card']}; padding: 24px 28px; transition: background 0.2s; }}
.v2-stat:hover {{ background: {v['gold_soft']}; }}
.v2-stat-label {{
  font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
  color: {v['ink_3']}; font-weight: 600; margin-bottom: 10px;
}}
.v2-stat-value {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 32px; font-weight: 700; letter-spacing: -0.02em;
  color: {v['ink']}; line-height: 1;
}}

/* Verdict-graded stat cards (2026-07-30): value + left rail colored by
   the ratified GO/WATCH/NO-GO bars - the stat strip reads as a verdict. */
.v2-stat-go    {{ border-left: 3px solid #15803d; }}
.v2-stat-watch {{ border-left: 3px solid #b45309; }}
.v2-stat-nogo  {{ border-left: 3px solid #b91c1c; }}
.v2-stat-go    .v2-stat-value {{ color: #15803d; }}
.v2-stat-watch .v2-stat-value {{ color: #b45309; }}
.v2-stat-nogo  .v2-stat-value {{ color: #b91c1c; }}
.v2-stat-unit {{ font-size: 14px; font-weight: 500; color: {v['ink_4']}; margin-left: 4px; }}
.v2-stat-foot {{ font-size: 12px; color: {v['ink_2']}; margin-top: 8px; font-family: 'JetBrains Mono', monospace; }}
.v2-stat-foot.pos {{ color: {v['pos']}; }}
.v2-stat-foot.warn {{ color: {v['warn']}; }}
.v2-stat-foot.neg {{ color: {v['neg']}; }}

/* --- V2 VERDICT BAND --- */
.v2-verdict {{
  background: linear-gradient(135deg, {v['ink']} 0%, #1A2B47 100%);
  color: white;
  border-radius: 20px;
  padding: 28px 36px;
  margin-bottom: 36px;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 28px;
  align-items: center;
  position: relative;
  overflow: hidden;
}}
.v2-verdict::before {{
  content: '';
  position: absolute;
  top: -50%; right: -10%;
  width: 60%; height: 200%;
  background: radial-gradient(ellipse, rgba(184,151,56,0.18), transparent 70%);
  pointer-events: none;
}}
.v2-verdict-tag {{
  background: {v['gold']};
  color: {v['ink']};
  padding: 8px 20px; border-radius: 999px;
  font-size: 22px; font-weight: 800; letter-spacing: 0.04em;
  position: relative; white-space: nowrap;
}}
.v2-verdict-tag.watch {{ background: {v['warn']}; color: white; }}
.v2-verdict-tag.nogo {{ background: {v['neg']}; color: white; }}
.v2-verdict-tag.info {{ background: {v['blue']}; color: white; font-size: 18px; }}
.v2-verdict-text {{ position: relative; }}
.v2-verdict-text h3 {{ font-size: 18px !important; font-weight: 600 !important; margin: 0 0 6px 0 !important; letter-spacing: -0.005em !important; color: white !important; }}
.v2-verdict-text p {{ color: rgba(255,255,255,0.72) !important; font-size: 13px !important; line-height: 1.5 !important; margin: 0 !important; }}

/* --- V2 INSPECTOR BLOCK --- */
.v2-ins-block {{
  background: {v['card']};
  border: 1px solid {v['line']};
  border-radius: 16px;
  margin-bottom: 16px;
  overflow: hidden;
}}
.v2-ins-head {{
  padding: 14px 20px 12px;
  display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid {v['line']};
}}
.v2-ins-head h3 {{
  font-size: 11px !important;
  letter-spacing: 0.12em !important;
  text-transform: uppercase !important;
  color: {v['ink_3']} !important;
  font-weight: 700 !important;
  margin: 0 !important;
}}
.v2-ins-head .pip {{
  /* Brian 5/29 v2.0.38 — `white-space: nowrap` keeps "FRED · 5:18 PM
     ET" on one line. Was wrapping the "ET" to a 2nd line in the
     narrow inspector column. */
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: {v['ink_4']};
  white-space: nowrap;
  flex-shrink: 0;
}}
.v2-ins-body {{ padding: 8px 20px 14px; }}
.v2-ins-row {{
  /* Brian 5/29 v2.0.38 — `gap: 10px` plus `min-width: 0` on the label
     keeps the label + value cleanly on one row when the inspector
     column is narrow. Label uses `text-overflow: ellipsis` to
     gracefully truncate instead of wrapping to a 2nd line. */
  display: flex; justify-content: space-between; align-items: center;
  padding: 9px 0;
  gap: 10px;
  font-size: 13px;
  border-bottom: 1px dashed {v['line_faint']};
}}
.v2-ins-row:last-child {{ border-bottom: none; }}
.v2-ins-row .l {{
  color: {v['ink_2']};
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.v2-ins-row .r {{
  font-family: 'JetBrains Mono', monospace;
  color: {v['ink']};
  font-weight: 500;
  font-size: 12px;
  white-space: nowrap;
  flex-shrink: 0;
}}
.v2-ins-row .r.pos {{ color: {v['pos']}; }}
.v2-ins-row .r.warn {{ color: {v['warn']}; }}
.v2-ins-row .r.neg {{ color: {v['neg']}; }}
.v2-ins-row .vs {{ color: {v['ink_4']}; margin-left: 6px; font-weight: 400; }}

/* DD score box */
.v2-dd-score {{ display: flex; align-items: baseline; gap: 6px; padding: 14px 20px 8px; }}
.v2-dd-score-n {{ font-family: 'JetBrains Mono', monospace; font-size: 44px; font-weight: 700; letter-spacing: -0.03em; line-height: 1; color: {v['gold_deep']}; }}
.v2-dd-score-d {{ font-size: 15px; color: {v['ink_4']}; font-weight: 500; }}
.v2-dd-bar {{ height: 5px; background: {v['line']}; border-radius: 3px; overflow: hidden; margin: 0 20px 14px; }}
.v2-dd-bar-fill {{ height: 100%; background: linear-gradient(90deg, {v['gold']}, {v['gold_bright']}); border-radius: 3px; }}

/* People block */
.v2-person {{ display: flex; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px dashed {v['line_faint']}; }}
.v2-person:last-child {{ border-bottom: none; }}
.v2-person-avatar {{
  width: 30px; height: 30px; border-radius: 50%;
  background: {v['bg_soft']}; color: {v['ink_2']};
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; flex-shrink: 0;
}}
.v2-person-info {{ flex: 1; min-width: 0; }}
.v2-person-info .n {{ font-size: 13px; font-weight: 600; color: {v['ink']}; }}
.v2-person-info .r {{ font-size: 11px; color: {v['ink_3']}; }}

/* Doc row */
.v2-doc-row {{ display: flex; align-items: center; gap: 10px; padding: 9px 0; font-size: 13px; border-bottom: 1px dashed {v['line_faint']}; }}
.v2-doc-row:last-child {{ border-bottom: none; }}
.v2-doc-row .icon {{ width: 24px; height: 24px; border-radius: 6px; background: {v['blue_soft']}; color: {v['blue']}; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; flex-shrink: 0; }}
.v2-doc-row .icon.xls {{ background: {v['pos_soft']}; color: {v['pos']}; }}
.v2-doc-row .icon.doc {{ background: #DBEAFE; color: {v['blue']}; }}
.v2-doc-row .name {{ flex: 1; color: {v['ink']}; font-weight: 500; font-family: 'JetBrains Mono', monospace; font-size: 11px; word-break: break-all; }}
.v2-doc-row .when {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: {v['ink_4']}; white-space: nowrap; }}

/* Brian 5/29 v2.0.26 — property hero now has a 2-column grid: left =
   eyebrow + name + chips, right = Eight Rock logo (top-right). */
.v2-hero.v2-hero-grid {{
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 24px;
  align-items: start;
}}
.v2-hero-text {{ min-width: 0; }}
.v2-hero-logo {{
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  padding-top: 4px;
}}
.v2-hero-logo img {{
  max-width: 220px;
  width: 100%;
  height: auto;
  display: block;
}}
@media (max-width: 900px) {{
  .v2-hero.v2-hero-grid {{ grid-template-columns: 1fr; }}
  .v2-hero-logo {{ justify-content: flex-start; }}
}}

/* Brian 5/29 v2.0.35 — tabs tuned closer to reference screenshot:
   inactive tabs use full INK text (not gray), badges are warm
   cream (#F5F0E4) on a borderless rounded box, active pill is
   substantial (10x18 padding, 999px radius) with a darker
   translucent badge inside. */
[data-testid="stTabs"] > div[data-baseweb="tab-list"] {{
  counter-reset: v2tab;
  gap: 6px;
  border-bottom: none;
  padding: 6px 0;
}}
[data-testid="stTabs"] button[role="tab"] {{
  counter-increment: v2tab;
  background: transparent;
  border-radius: 999px !important;
  border: 1px solid transparent;
  padding: 10px 18px;
  color: {v['ink']};
  font-weight: 500;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  transition: background-color 120ms ease, color 120ms ease;
}}
[data-testid="stTabs"] button[role="tab"]::after {{
  content: counter(v2tab);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  background: #F5F0E4;
  border: none;
  border-radius: 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: {v['ink_2']};
  line-height: 1;
}}
[data-testid="stTabs"] button[role="tab"]:hover {{
  background: {v['bg_soft']};
}}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"],
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] * {{
  /* Brian 5/29 v2.0.36 — active tab text was hard to read. Force
     #FFFFFF on every descendant + bump to font-weight 700 so the
     label is high-contrast on the dark navy pill. Streamlit can nest
     the label inside <p>/<div>/<span> depending on the version; the
     wildcard cascade catches them all. */
  background: {v['ink']} !important;
  color: #FFFFFF !important;
  border-color: {v['ink']} !important;
  font-weight: 700 !important;
  text-shadow: none !important;
  opacity: 1 !important;
}}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"]::after {{
  background: rgba(255, 255, 255, 0.20);
  border-color: transparent;
  color: #FFFFFF;
  font-weight: 700;
}}
/* Hide Streamlit's default red underline on the active tab — the gold
   pill is the active indicator now. */
[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"] {{
  display: none !important;
}}

/* Brian 5/29 v2.0.25 — REPLACED the v2.0.20-v2.0.24 popover-CSS
   acrobatics with a native HTML <details>/<summary> element. The
   previous :has() selectors were matching outer stVerticalBlocks and
   bleeding into other popovers + the page background. This styling is
   self-contained (only matches .v2-section-help) and can't bleed. */
.v2-section-help {{
  display: inline-block;
  position: relative;
  vertical-align: middle;
  flex-shrink: 0;
}}
.v2-section-help-trigger {{
  list-style: none;
  cursor: pointer;
  user-select: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid {v['gold']};
  border-radius: 50%;
  color: {v['gold_deep']};
  font-weight: 700;
  font-size: 11px;
  font-style: italic;
  font-family: Georgia, serif;
  background: transparent;
  margin: 0;
  line-height: 1;
  transition: background-color 120ms ease, border-color 120ms ease;
}}
.v2-section-help-trigger::-webkit-details-marker {{ display: none; }}
.v2-section-help-trigger::marker {{ display: none; content: ""; }}
.v2-section-help-trigger:hover {{
  background: {v['gold_soft']};
  border-color: {v['gold_deep']};
}}
.v2-section-help[open] .v2-section-help-trigger {{
  background: {v['gold_soft']};
}}
/* Brian 5/29 v2.0.34 — panel opens RIGHTWARD from the ⓘ trigger
   (left: 0). The trigger sits next to a section title at the left
   of the card, so opening rightward keeps the panel inside the
   card / viewport. Width clamped to fit narrow viewports too. */
.v2-section-help-panel {{
  position: absolute;
  top: 26px;
  left: 0;
  width: min(380px, calc(100vw - 32px));
  background: {v['card']};
  border: 1px solid {v['line']};
  border-radius: 10px;
  padding: 14px 16px;
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.14);
  z-index: 1000;
  font-size: 12px;
  line-height: 1.55;
  color: {v['ink']};
  text-align: left;
  font-style: normal;
  font-family: 'Inter', -apple-system, system-ui, sans-serif;
  font-weight: 400;
}}
/* Brian 5/31 — left-opening variant for the narrow right rail (e.g. the
   Calibration ⓘ). Anchors the panel's right edge to the trigger so it
   expands LEFTWARD into the page instead of clipping off the right edge. */
.v2-help-left .v2-section-help-panel {{
  left: auto;
  right: 0;
  width: min(340px, calc(100vw - 32px));
}}
/* The Calibration ⓘ sits inline in the inspector head — keep it small and
   aligned, and let the pip keep its margin-left:auto push to the right. */
.v2-ins-head .v2-section-help {{ margin-left: 8px; }}
.v2-ins-head .v2-section-help-trigger {{ width: 17px; height: 17px; font-size: 10px; }}
.v2-section-help-panel-title {{
  font-weight: 700;
  font-size: 13px;
  margin-bottom: 8px;
  color: {v['ink']};
}}
.v2-section-help-panel p {{
  margin: 0 0 8px 0;
}}
.v2-section-help-panel ul {{
  margin: 0 0 8px 0;
  padding-left: 18px;
}}
.v2-section-help-panel li {{
  margin: 2px 0;
}}
.v2-section-help-panel code {{
  background: {v['bg_soft']};
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}}
.v2-section-help-panel strong {{
  color: {v['ink']};
}}

/* Brian 5/29 v2.0.15 — Key Documents: each file is a Streamlit button.
   The .v2-doc-mark marker scopes this CSS to ONLY the doc-row buttons
   in the inspector (avoids polluting every button in the page).
   Sibling-combinator selector targets all stButton elements that follow
   the marker within the same vertical block. */
.v2-doc-mark {{ display: none; }}
.v2-doc-mark ~ [data-testid="stButton"] > button {{
  background: transparent !important;
  border: 1px dashed {v['line_faint']} !important;
  border-radius: 8px !important;
  text-align: left !important;
  padding: 8px 12px !important;
  font-size: 12px !important;
  font-family: 'JetBrains Mono', monospace !important;
  color: {v['ink']} !important;
  height: auto !important;
  min-height: 0 !important;
  margin-bottom: 4px !important;
  font-weight: 500 !important;
  justify-content: flex-start !important;
  white-space: normal !important;
  word-break: break-all !important;
  line-height: 1.4 !important;
  box-shadow: none !important;
}}
.v2-doc-mark ~ [data-testid="stButton"] > button:hover {{
  border-color: {v['gold']} !important;
  background: rgba(184, 151, 56, 0.06) !important;
  color: {v['gold_deep']} !important;
}}
.v2-doc-mark ~ [data-testid="stButton"] > button:focus {{
  box-shadow: 0 0 0 2px rgba(184, 151, 56, 0.25) !important;
}}

/* --- Section card (re-styles V1's section_card class) --- */
.v2-section-card {{
  background: {v['card']};
  border: 1px solid {v['line']};
  border-radius: 16px;
  padding: 20px 24px;
  margin-bottom: 16px;
}}

/* Help-text and overall improvements */
.v2-hint {{ font-size: 12px; color: {v['ink_3']}; font-style: italic; }}

/* === SWITCH-TO-OTHER-VERSION PILLS =================================== */
/* V2 mode: pill lives inside the V2 topbar next to the version chip.
   V1 mode: pill is a floating fixed element top-right (injected separately). */
.v2-switch-pill {{
  display: inline-flex; align-items: center; gap: 6px;
  background: {v['bg_soft']};
  border: 1px solid {v['line']};
  color: {v['ink_2']};
  padding: 5px 12px;
  border-radius: 999px;
  font-family: 'Inter', sans-serif;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-decoration: none;
  transition: all 0.15s;
  cursor: pointer;
  white-space: nowrap;
}}
.v2-switch-pill:hover {{
  background: {v['gold_soft']};
  border-color: {v['gold']};
  color: {v['gold_deep']};
}}
.v2-switch-pill .arrow {{ font-size: 13px; line-height: 1; }}

/* V1-mode floating pill (used when ER_THEME is NOT v2). Injected with its
   own <style> block so it works without the rest of the V2 theme. */


/* ============================================================
   ⌘K COMMAND PALETTE
   ============================================================ */
.v2-cmdk-overlay {{
  position: fixed; inset: 0;
  background: rgba(10,22,40,0.40);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  z-index: 10000;
  display: flex; align-items: flex-start; justify-content: center;
  padding-top: 120px;
  opacity: 0; pointer-events: none;
  transition: opacity 0.18s;
}}
.v2-cmdk-overlay.show {{ opacity: 1; pointer-events: auto; }}
.v2-cmdk-modal {{
  width: 640px; max-width: calc(100vw - 32px);
  background: {v['card']};
  border: 1px solid {v['line']};
  border-radius: 16px;
  box-shadow: 0 40px 80px -20px rgba(10,22,40,0.30);
  overflow: hidden;
  font-family: 'Inter', sans-serif;
}}
.v2-cmdk-input-row {{
  display: flex; align-items: center; gap: 12px;
  padding: 16px 22px;
  border-bottom: 1px solid {v['line']};
}}
.v2-cmdk-input-icon {{ color: {v['ink_3']}; flex-shrink: 0; }}
.v2-cmdk-input {{
  background: transparent; border: none; outline: none;
  color: {v['ink']}; font-size: 16px; font-family: inherit;
  width: 100%; padding: 0;
}}
.v2-cmdk-input::placeholder {{ color: {v['ink_4']}; }}
.v2-cmdk-results {{
  max-height: 420px; overflow-y: auto; padding: 8px;
}}
.v2-cmdk-results::-webkit-scrollbar {{ width: 8px; }}
.v2-cmdk-results::-webkit-scrollbar-thumb {{ background: {v['line']}; border-radius: 4px; }}
.v2-cmdk-section {{
  font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase;
  color: {v['ink_4']}; font-weight: 600;
  padding: 10px 14px 6px;
}}
.v2-cmdk-result {{
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  color: inherit;
  text-decoration: none;
}}
.v2-cmdk-result:hover {{ background: {v['gold_soft']}; }}
.v2-cmdk-result.sel {{
  background: {v['gold_soft']};
  box-shadow: inset 3px 0 0 {v['gold']};
}}
.v2-cmdk-result-name {{
  font-weight: 500; color: {v['ink']};
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-width: 240px;
}}
.v2-cmdk-result-addr {{
  color: {v['ink_3']}; font-size: 12px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  flex: 1; min-width: 0;
}}
.v2-cmdk-result-meta {{
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: {v['ink_4']};
  white-space: nowrap;
}}
.v2-cmdk-empty {{
  padding: 32px;
  text-align: center;
  color: {v['ink_3']};
  font-size: 13px;
}}
.v2-cmdk-foot {{
  display: flex; gap: 16px;
  padding: 10px 22px;
  border-top: 1px solid {v['line']};
  background: {v['bg_soft']};
  font-size: 11px; color: {v['ink_3']};
}}
.v2-cmdk-foot kbd {{
  background: {v['card']};
  border: 1px solid {v['line']};
  border-bottom-width: 2px;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: {v['ink_2']};
}}

/* Make the V1 sidebar property buttons visually flash when number-key tab
   shortcut fires, as confirmation. JS adds .v2-flash class for 0.4s. */
[data-baseweb="tab"].v2-flash {{
  background: {v['gold_soft']} !important;
  transition: background 0.2s ease !important;
}}
</style>
"""
    st.markdown(css, unsafe_allow_html=True)

    # --- JS: toggle body.v2-on-<tab-slug> for the active Streamlit tab ---
    # Brian 5/29 v2.0.27 — extended from "just Subject" to every tab.
    # Index → slug mapping matches the v2.0.27 tab order in app.py:
    #   0=subject 1=underwriting 2=returns 3=market
    #   4=summary 5=diligence    6=owner-portal
    # CSS uses these classes to gate which inspector blocks render per tab
    # (e.g. Diligence inspector ONLY appears under body.v2-on-diligence).
    st.markdown("""
<script>
(function() {
  if (window.__v2_tab_watcher) return;
  window.__v2_tab_watcher = true;

  const TAB_SLUGS = [
    'subject', 'underwriting', 'returns', 'market',
    'summary', 'diligence', 'owner-portal'
  ];

  function syncTabClass() {
    const tabs = document.querySelectorAll('button[data-baseweb="tab"]');
    if (!tabs.length) return;
    const active = Array.from(tabs).findIndex(
      t => t.getAttribute('aria-selected') === 'true'
    );
    // Remove every v2-on-* class first
    TAB_SLUGS.forEach(slug => document.body.classList.remove('v2-on-' + slug));
    if (active >= 0 && active < TAB_SLUGS.length) {
      document.body.classList.add('v2-on-' + TAB_SLUGS[active]);
    }
  }

  document.addEventListener('click', e => {
    if (e.target.closest('button[data-baseweb="tab"]')) {
      setTimeout(syncTabClass, 30);
    }
  }, true);

  const observer = new MutationObserver(syncTabClass);
  observer.observe(document.body, { childList: true, subtree: true,
                                     attributes: true,
                                     attributeFilter: ['aria-selected'] });

  setTimeout(syncTabClass, 100);
  setTimeout(syncTabClass, 500);
  setTimeout(syncTabClass, 1500);
})();
</script>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# V2 chrome renderers
# ---------------------------------------------------------------------------

def _gmaps_url(query: str) -> str:
    """Encode a Google Maps search URL."""
    from urllib.parse import quote_plus
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def render_v2_topbar(prop: dict | None = None) -> None:
    """V2 topbar with a REAL, in-place global search.

    Brian 5/31 v2.1.2 — the "Find anything…" bar is now a native
    st.text_input you type INTO directly. Matching properties drop down
    right under the bar — NO navigating to another page. ⌘K focuses the
    field; clicking a result opens that property. (Prior versions faked
    the bar with HTML that either did nothing or navigated away — Streamlit
    won't run injected <script>, and you can't click into a <div>. A real
    widget is the only thing that works.)
    """
    crumb_here = (prop.get("name") or "—") if prop else "Pick a property"
    when = _et_clock_now()
    version = get_v2_version_label()
    prop_id = prop.get("property_id") if prop else None
    qp = f"?prop={prop_id}" if prop_id else ""
    switch_url = f"http://localhost:8501/{qp}"

    c_left, c_mid, c_right = st.columns([1.0, 1.2, 1.0], vertical_alignment="center")
    with c_left:
        st.markdown(
            f'<div class="v2-topbar-mark"></div>'
            f'<div class="v2-nav-inline">'
            f'<a href="?home=1" target="_self" class="v2-nav-brand" '
            f'title="Home — browse all properties" style="text-decoration:none">'
            f'<div class="v2-nav-brand-mark">8R</div><span>QUARRIE</span></a>'
            f'<div class="v2-nav-crumbs"><span class="here">{crumb_here}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c_mid:
        # Marker scopes the pill styling to THIS column's input (CSS :has()).
        st.markdown('<div class="v2-search-mark"></div>', unsafe_allow_html=True)
        st.text_input(
            "Search properties",
            placeholder="🔍   Find anything…   (press Enter · ⌘K)",
            label_visibility="collapsed",
            key="v2_global_search",
        )
    with c_right:
        # The avatar is a real button (it opens the Appearance dialog), and
        # Streamlit buttons are block elements — so the status pills and the
        # avatar sit in their own columns to stay on one line.
        r_pills, r_avatar = st.columns([9.0, 1.0], vertical_alignment="center")
        with r_pills:
            # Owner ask 2026-08-04: the old "V1" switch pill is replaced by a
            # live who's-online count. Clicking it opens the ?who=1 page
            # (identity + IP + locality per active session).
            try:
                from core import presence as _presence
                _online = _presence.count()
            except Exception:
                _online = 0
            st.markdown(
                f'<div class="v2-nav-inline v2-nav-right">'
                f'<span class="v2-nav-tag"><span class="d"></span>Live · {when}</span>'
                f'<span class="v2-version-pill" title="{version}">{get_v2_version_short()}</span>'
                f'<a class="v2-switch-pill" href="?who=1" target="_self" '
                f'title="Who is on the site right now — click for identities, IPs, locality" '
                f'style="font-size:10px;padding:4px 10px;">'
                f'<span class="arrow">👤</span><span>{_online} online</span></a>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with r_avatar:
            from ui.theme_panel import render_avatar_button
            render_avatar_button()

    # In-place results dropdown — renders right under the bar when there's a
    # query. Clicking a result opens that property (?prop=<id>).
    q = (st.session_state.get("v2_global_search") or "").strip()
    if q:
        _render_v2_search_results(q)


def _render_v2_search_results(q: str) -> None:
    """Render the in-place search dropdown for query `q` (queries the DB)."""
    import html as _html
    from data.db import list_properties
    try:
        # Pull a wider set, then rank by relevance so NAME matches beat
        # incidental address/city matches (typing "Crossroads" should
        # surface "Crossroads Townhomes", not 8 properties on a street
        # that happens to contain the word).
        rows = list_properties(search=q, limit=40)
    except Exception:
        rows = []
    ql = q.lower()

    def _rank(p):
        nm = (p.get("name") or "").lower()
        if nm == ql:
            return 0
        if nm.startswith(ql):
            return 1
        if ql in nm:
            return 2
        return 3  # matched on address/city/owner only

    rows.sort(key=lambda p: (_rank(p), (p.get("name") or "").lower()))
    rows = rows[:8]
    if not rows:
        st.markdown(
            f'<div class="v2-search-results"><div class="v2-search-empty">'
            f'No matches for &ldquo;{_html.escape(q)}&rdquo; — try a property '
            f'name, city, address, or owner.</div></div>',
            unsafe_allow_html=True,
        )
        return
    items = []
    for p in rows:
        pid = _html.escape(str(p.get("property_id") or ""), quote=True)
        name = _html.escape(p.get("name") or "—")
        city = p.get("city") or ""
        state = p.get("state") or ""
        addr = p.get("address") or ""
        units = p.get("units")
        cls = _html.escape(p.get("asset_class") or "—")
        loc = _html.escape(", ".join(
            x for x in [addr, f"{city}, {state}".strip(", ")] if x
        ))
        u = f"{int(units)}u" if isinstance(units, (int, float)) else ""
        items.append(
            f'<a class="v2-search-item" href="?prop={pid}" target="_self">'
            f'<span class="nm">{name}</span>'
            f'<span class="lo">{loc}</span>'
            f'<span class="mt">{u} · {cls}</span></a>'
        )
    st.markdown(
        f'<div class="v2-search-results">'
        f'<div class="v2-search-hd">{len(rows)} match{"es" if len(rows) != 1 else ""}'
        f' · click to open</div>{"".join(items)}</div>',
        unsafe_allow_html=True,
    )


def render_v1_switch_button(selected_property_id: str | None = None) -> None:
    """Floating "Switch to V2.0" pill in V1 mode (top-right corner).

    Renders nothing in V2 mode (V2's topbar has its own switch pill).
    Carries the current property via ?prop=<id> to V2 (port 8502).

    Self-contained: ships its own CSS so it works alongside V1's chrome
    without depending on the V2 theme's CSS being loaded.
    """
    if is_v2():
        return  # V2 topbar handles its own switch pill

    qp = f"?prop={selected_property_id}" if selected_property_id else ""
    target = f"http://localhost:8502/{qp}"
    version_label = get_v2_version_label().split(" · ")[0]  # "V2.0.MMDDYYYY"

    html = f"""
<style>
.v2-switch-pill-floating {{
  position: fixed;
  top: 14px;
  right: 16px;
  z-index: 9999;
  display: inline-flex; align-items: center; gap: 8px;
  background: linear-gradient(135deg, #B89738 0%, #D4B14B 100%);
  color: #0A1628;
  padding: 7px 16px;
  border-radius: 999px;
  font-family: 'Inter', -apple-system, system-ui, sans-serif;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-decoration: none;
  box-shadow: 0 2px 8px rgba(184,151,56,0.30), 0 1px 2px rgba(0,0,0,0.10);
  transition: all 0.15s ease;
  cursor: pointer;
}}
.v2-switch-pill-floating:hover {{
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(184,151,56,0.40), 0 2px 4px rgba(0,0,0,0.12);
  filter: brightness(1.05);
}}
.v2-switch-pill-floating .arrow {{ font-size: 15px; line-height: 1; }}
.v2-switch-pill-floating .label-version {{
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 10px;
  opacity: 0.7;
  margin-left: 4px;
}}
</style>
<a class="v2-switch-pill-floating" href="{target}"
   title="Open this property in the V2.0 'Quiet Operator' restyle (port 8502)">
  <span>Try V2.0</span>
  <span class="arrow">↗</span>
  <span class="label-version">{version_label}</span>
</a>
"""
    st.markdown(html, unsafe_allow_html=True)


def render_v2_property_header(prop: dict) -> None:
    """Eyebrow + 56-60px property name + chip row of identity facts.

    Identity chips Brian asked for (5/27): units, class, location, vintage,
    occupancy, RSF, $/SF, address (clickable → Google Maps).
    """
    name = prop.get("name") or "—"
    units = prop.get("units") or 0
    cls = prop.get("asset_class") or "—"
    city = prop.get("city") or "—"
    state = prop.get("state") or "VA"
    zip_code = str(prop.get("zip") or "")
    year_built = prop.get("year_built")
    occupancy_pct = prop.get("occupancy_pct") or 0
    avg_sqft = prop.get("avg_sqft") or 0
    address = (prop.get("address") or "").strip()

    rsf = int(round(avg_sqft * units)) if avg_sqft and units else 0
    mgr = (prop.get("management_company") or "").strip()

    chips_html = []
    chips_html.append(f'<div class="v2-chip"><b>{units}</b> units</div>')
    chips_html.append(f'<div class="v2-chip">Class <b>{cls}</b></div>')
    chips_html.append(f'<div class="v2-chip"><b>{city}, {state}</b> {zip_code}</div>')
    if year_built:
        chips_html.append(f'<div class="v2-chip">Built <b>{year_built}</b></div>')
    if occupancy_pct:
        pct = occupancy_pct * 100 if occupancy_pct < 1.5 else occupancy_pct
        chips_html.append(f'<div class="v2-chip"><b>{pct:.0f}%</b> occupied</div>')
    if rsf:
        chips_html.append(f'<div class="v2-chip">{rsf:,} RSF</div>')
    if mgr:
        chips_html.append(f'<div class="v2-chip">{mgr[:40]}</div>')
    if address:
        gmaps = _gmaps_url(f"{address}, {city}, {state} {zip_code}")
        chips_html.append(
            f'<a class="v2-chip v2-chip-link" href="{gmaps}" target="_blank" '
            f'rel="noopener noreferrer" title="Open in Google Maps">{address} ↗</a>'
        )

    # Eyebrow format (Brian 5/29 EOD): "LIVE DEAL · UPDATED HH:MM XM" for
    # Crossroads (IC-TRACK token removed per his request); "ACTIVE · UPDATED
    # HH:MM XM" for other properties.
    when_str = dt.datetime.now().strftime("%-I:%M %p") if os.name != "nt" else dt.datetime.now().strftime("%#I:%M %p")
    if name == "Crossroads Townhomes":
        eyebrow = f"LIVE DEAL · UPDATED {when_str.upper()}"
    else:
        eyebrow = f"ACTIVE · UPDATED {when_str.upper()}"

    # Brian 5/29 v2.0.26 — logo moved from topbar to the upper-right of
    # the property hero block (he circled this spot). Two-column hero:
    # left = eyebrow + name + chips; right = logo aligned top-right.
    logo_uri = _eight_rock_logo_data_uri()
    logo_html = (
        f'<div class="v2-hero-logo">'
        f'<img src="{logo_uri}" alt="Eight Rock Capital Partners"/>'
        f'</div>'
        if logo_uri else ""
    )
    html = f"""
<div class="v2-hero v2-hero-grid">
  <div class="v2-hero-text">
    <div class="v2-hero-eyebrow">{eyebrow}</div>
    <div class="v2-hero-name">{name}.</div>
    <div class="v2-chips">{''.join(chips_html)}</div>
  </div>
  {logo_html}
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


def _stat_card_html(label: str, value: str, unit: str | None, foot: str,
                    foot_class: str = "", tone: str = "") -> str:
    """tone: '' neutral, 'go'/'watch'/'nogo' - grades the value against the
    ratified Eight Rock bars (config GO_*/WATCH_*) with a colored value +
    left rail, so the stat bar reads as an instant verdict strip."""
    unit_html = f'<span class="v2-stat-unit">{unit}</span>' if unit else ""
    tone_cls = f" v2-stat-{tone}" if tone else ""
    return f"""
<div class="v2-stat{tone_cls}">
  <div class="v2-stat-label">{label}</div>
  <div class="v2-stat-value">{value}{unit_html}</div>
  <div class="v2-stat-foot {foot_class}">{foot}</div>
</div>"""


def _tone(value: float | None, go: float, watch: float) -> str:
    """Grade a higher-is-better metric against its GO/WATCH bars."""
    if value is None:
        return ""
    return "go" if value >= go else "watch" if value >= watch else "nogo"


def render_v2_stats_bar(prop: dict, metrics: dict | None = None) -> None:
    """4-card stat bar: Purchase Price · Going-in cap · 5-yr IRR · Stab DSCR.

    Reads from deal.json via gather_metrics() (which itself calls
    data.property_io.load_deal). Falls back to session state if the user is
    mid-edit in the Underwriting tab and hasn't saved yet.

    Brian's rename 5/29/2026: "Asking" -> "Purchase Price". The underlying
    field has always been deal.pp (purchase_price); the V2 label is now
    aligned with V1's underwriting terminology.
    """
    m = metrics or {}
    # Read purchase_price (new canonical key). Accept legacy "asking" for
    # back-compat in case any old session-state writer still uses that key.
    purchase_price = m.get("purchase_price") or m.get("asking")
    cap = m.get("going_in_cap")
    irr = m.get("irr_5y")
    dscr_stab = m.get("dscr_stab")
    em = m.get("equity_multiple")
    units = prop.get("units") or 0

    # Purchase Price card (was "Asking" pre-5/29/2026)
    if purchase_price:
        ppu = purchase_price / units if units else 0
        if purchase_price >= 1_000_000:
            v_str = f"${purchase_price/1_000_000:.2f}"
            unit = "M"
        else:
            v_str = f"${purchase_price/1_000:.0f}"
            unit = "K"
        foot = f"${int(ppu):,} / unit"
        ask_card = _stat_card_html("Purchase Price", v_str, unit, foot)
    else:
        ask_card = _stat_card_html("Purchase Price", "—", None, "Set in Underwriting tab")

    # Cap card - graded against GO_CAP/WATCH_CAP (7.5%/7.0%)
    if cap:
        cap_pct = cap * 100 if cap < 1 else cap
        ask_foot = "Computed from underwriting"
        cap_card = _stat_card_html(
            "Going-in cap", f"{cap_pct:.2f}", "%", ask_foot,
            tone=_tone(cap_pct / 100, config.GO_CAP, config.WATCH_CAP))
    else:
        cap_card = _stat_card_html("Going-in cap", "—", None, "Set in Underwriting tab")

    # IRR card
    if irr:
        irr_pct = irr * 100 if irr < 1 else irr
        em_str = f"{em:.1f}× equity multiple" if em else "5-yr levered model"
        # Graded against the 15% LP IRR target (watch band: within 2 pts).
        irr_card = _stat_card_html(
            "5-yr IRR", f"{irr_pct:.1f}", "%", em_str,
            tone=_tone(irr_pct / 100, config.LP_IRR_TARGET,
                       config.LP_IRR_TARGET - 0.02))
    else:
        irr_card = _stat_card_html("5-yr IRR", "—", None, "Set in Underwriting tab")

    # DSCR card - graded against GO_DSCR/WATCH_DSCR (1.30/1.10)
    if dscr_stab:
        dscr_card = _stat_card_html(
            "DSCR Stabilized", f"{dscr_stab:.2f}", "×", "Stabilized year",
            tone=_tone(dscr_stab, config.GO_DSCR, config.WATCH_DSCR))
    else:
        dscr_card = _stat_card_html("DSCR Stabilized", "—", None, "Set in Underwriting tab")

    html = f'<div class="v2-stats">{ask_card}{cap_card}{irr_card}{dscr_card}</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_v2_verdict_band(prop: dict, metrics: dict | None = None) -> None:
    """Verdict band: GO/WATCH/NOGO + rationale.

    Reads from session state if Underwriting tab has stored a verdict.
    Falls back to "Run underwriting to see your verdict" info state.
    """
    from core import calibration

    m = metrics or {}
    verdict_obj = m.get("verdict_result")  # VerdictResult instance if computed
    cap = m.get("going_in_cap")
    dscr = m.get("dscr_stab") or m.get("dscr_year1")
    coc = m.get("coc_year1") or m.get("coc_avg")
    # Brian 5/29/2026: "purchase_price" replaces legacy "asking" key.
    purchase_price = m.get("purchase_price") or m.get("asking")
    units = prop.get("units") or 0
    city = prop.get("city") or ""

    # Try to compute verdict on the fly if metrics are available
    if verdict_obj is None and cap and dscr and purchase_price and units and city:
        try:
            from core import verdict as verdict_mod
            ppu = purchase_price / units
            coc_val = coc if coc else 0.05
            verdict_obj = verdict_mod.evaluate(
                cap=cap, dscr=dscr, coc=coc_val, ppu=ppu, city=city,
            )
        except Exception:
            verdict_obj = None

    if verdict_obj:
        v_text = verdict_obj.verdict
        if v_text == "GO":
            tag_class, h3 = "", "Calibration says proceed."
        elif v_text == "WATCH":
            tag_class, h3 = "watch", "Calibration says caution."
        elif v_text == "NO-GO":
            tag_class, h3 = "nogo", "Calibration says pass."
        else:  # FINANCING-CONSTRAINED-WATCH
            tag_class, h3 = "watch", "Financing-constrained — lender pre-qual required."
        # First rationale item, escaping any HTML
        rationale = verdict_obj.rationale[0] if verdict_obj.rationale else "See Underwriting tab for details."
        # Strip rationale to safe length
        if len(rationale) > 280:
            rationale = rationale[:277] + "..."
        # Three-column verdict band: tag · text · CTA (matches Brian's screenshot)
        html = f"""
<div class="v2-verdict" style="grid-template-columns: auto 1fr auto;">
  <div class="v2-verdict-tag {tag_class}">{v_text}</div>
  <div class="v2-verdict-text">
    <h3>{h3}</h3>
    <p>{rationale}</p>
  </div>
  <a class="v2-verdict-act" href="#ic-memo" title="Jump to IC Memo Check tab">
    <span>Build IC packet</span><span>→</span>
  </a>
</div>
"""
    else:
        try:
            go_cap_t = calibration.get_threshold("GO_CAP")
            dy_t = calibration.get_threshold("MIN_DEBT_YIELD")
            go_str = go_cap_t.format_value() if go_cap_t else "—"
            dy_str = dy_t.format_value() if dy_t else "—"
        except Exception:
            go_str = dy_str = "—"
        html = f"""
<div class="v2-verdict" style="grid-template-columns: auto 1fr auto;">
  <div class="v2-verdict-tag info">…</div>
  <div class="v2-verdict-text">
    <h3>Verdict pending.</h3>
    <p>Run the Underwriting tab to compute your cap, DSCR, CoC, and PPU. Live GO bar: {go_str} cap · {dy_str} debt yield (Norfolk Class-C, calibrated this week).</p>
  </div>
  <a class="v2-verdict-act" href="#underwriting" title="Open the Underwriting tab">
    <span>Open Underwriting</span><span>→</span>
  </a>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


def _calibration_help_html() -> str:
    """Inline ⓘ help for the Calibration inspector block — explains each
    line in plain English (Brian 5/31). Self-contained <details> element
    (the .v2-help-left variant opens leftward so it stays on-screen in the
    narrow right rail)."""
    import html as _h
    rows = [
        ("Going-in cap", "Year-1 NOI ÷ Purchase Price — the headline going-in "
         "yield, before any growth or value-add. Shown against the Eight Rock "
         "GO bar."),
        ("Stabilized DY", "Stabilized Debt Yield = stabilized-year NOI ÷ loan "
         "amount. A leverage-independent lender safety metric — higher is "
         "safer. Shown against the minimum debt-yield floor."),
        ("$/Unit vs submkt", "Your price per unit vs the submarket's GO "
         "ceiling. A negative number means you're BELOW the ceiling — i.e. "
         "you're not overpaying."),
        ("DSCR Y1", "Year-1 Debt Service Coverage = (NOI − asset-mgmt fee) ÷ "
         "annual debt service. The lender's primary credit test in year one."),
        ("DSCR stab", "The same coverage ratio in the stabilized (peak-NOI) "
         "year — confirms the loan stays safe after the value-add plan lands."),
        ("Vacancy", "Your underwritten vacancy assumption vs the legacy market "
         "actual. ✓ when you're conservative (assuming at least as much "
         "vacancy as the market)."),
        ("Exit cap", "Your assumed sale (exit) cap rate vs the comp-derived "
         "market exit cap. ✗ flags an exit cap MORE aggressive (lower) than "
         "the market — an optimism risk worth a second look."),
    ]
    body = "".join(
        f"<p><strong>{_h.escape(n)}</strong> — {_h.escape(d)}</p>"
        for n, d in rows
    )
    legend = ("<p><strong>Marks:</strong> &#10003; clears the bar &middot; "
              "&#9651; within the watch band (close) &middot; &#10007; misses "
              "the bar / risk flag.</p>")
    return (
        '<details class="v2-section-help v2-help-left" name="v2-section-help">'
        '<summary class="v2-section-help-trigger" title="What do these mean?">i</summary>'
        '<div class="v2-section-help-panel">'
        '<div class="v2-section-help-panel-title">Calibration — what each line means</div>'
        f'{body}{legend}'
        '</div></details>'
    )


def render_v2_inspector(prop: dict, metrics: dict | None = None) -> None:
    """Right-rail inspector with the four standard blocks:
    Calibration · Diligence · People · Key documents.

    Pulls all data from existing engines. Safe to call any time."""
    from core import calibration

    blocks: list[str] = []
    m = metrics or {}

    # ===== Calibration (labels match Brian's screenshot 5/29 evening) =====
    # Compares the property's actuals to live calibrated thresholds:
    #   Going-in cap   vs  GO_CAP                                ✓ pass / △ warn / ✗ fail
    #   Stabilized DY  vs  MIN_DEBT_YIELD
    #   $/unit vs sub  vs  PPU_GO_<CITY>
    #   DSCR Y1        vs  1.15
    #   DSCR stab      vs  1.30 (GO_DSCR)
    #   Vacancy        vs  record VACANCY_DEFAULT
    #   Exit cap       vs  EXIT_CAP_DEFAULT
    try:
        thresholds = calibration.get_all_thresholds()
        th_by_name = {t.name: t for t in thresholds}
    except Exception:
        th_by_name = {}

    def _fmt_pct(x):
        if x is None: return "—"
        v = x * 100 if x < 1 else x
        return f"{v:.2f}%"

    def _state(passes: bool, warn: bool = False) -> str:
        return "pos" if passes else ("warn" if warn else "neg")

    def _mark(state: str) -> str:
        return {"pos": "✓", "warn": "△", "neg": "✗"}.get(state, "")

    def _row(label: str, value_str: str, vs_str: str, state: str) -> str:
        return (
            f'<div class="v2-ins-row"><span class="l">{label}</span>'
            f'<span class="r {state}">{value_str} {_mark(state)} '
            f'<span class="vs">{vs_str}</span></span></div>'
        )

    cal_rows: list[str] = []

    # Going-in cap
    you_cap = m.get("going_in_cap")
    go_cap_t = th_by_name.get("GO_CAP")
    go_cap_v = go_cap_t.effective_value if go_cap_t else None
    if you_cap is not None and go_cap_v is not None:
        state = _state(you_cap >= go_cap_v, warn=you_cap >= go_cap_v * 0.93)
        cal_rows.append(_row("Going-in cap", _fmt_pct(you_cap), f"vs {_fmt_pct(go_cap_v)}", state))

    # Stabilized DY  (derived: stabilized_noi or 1.18× current NOI / loan)
    dy_t = th_by_name.get("MIN_DEBT_YIELD")
    dy_floor = dy_t.effective_value if dy_t else None
    you_dy = None
    try:
        from data.property_io import load_deal
        if folder := m.get("folder"):
            if hasattr(folder, "path"):
                _d = load_deal(folder.path)
                if _d and _d.loan_amount:
                    stab_noi = m.get("stabilized_noi") or (_d.noi * 1.18)
                    you_dy = stab_noi / _d.loan_amount
    except Exception:
        you_dy = None
    if you_dy is not None and dy_floor is not None:
        state = _state(you_dy >= dy_floor, warn=you_dy >= dy_floor * 0.93)
        cal_rows.append(_row("Stabilized DY", _fmt_pct(you_dy), f"vs {_fmt_pct(dy_floor)}", state))

    # $/unit vs submarket GO PPU ceiling
    city = prop.get("city") or ""
    units = prop.get("units") or 0
    pp = m.get("purchase_price")
    token = city.upper().replace(" ", "_") if city else ""
    ppu_go = th_by_name.get(f"PPU_GO_{token}")
    if pp and units and ppu_go:
        you_ppu = pp / units
        pct_off = (you_ppu - ppu_go.effective_value) / ppu_go.effective_value * 100
        state = "pos" if pct_off <= 0 else ("warn" if pct_off <= 5 else "neg")
        sign = "+" if pct_off >= 0 else ""
        # Brian 5/29 v2.0.38 — label shortened so it fits on one line in
        # the narrow inspector column (was "$ / unit vs submkt" with a
        # "vs $/u ceiling" right-rail that wrapped awkwardly).
        cal_rows.append(_row("$/Unit vs submkt", f"{sign}{pct_off:.1f}%", "vs ceiling", state))

    # DSCR Y1
    dscr_y1 = m.get("dscr_year1")
    if dscr_y1 is not None:
        state = _state(dscr_y1 >= 1.15, warn=dscr_y1 >= 1.10)
        cal_rows.append(_row("DSCR Y1", f"{dscr_y1:.2f}×", "vs 1.15", state))

    # DSCR stab
    dscr_st = m.get("dscr_stab")
    if dscr_st is not None:
        state = _state(dscr_st >= 1.30, warn=dscr_st >= 1.20)
        cal_rows.append(_row("DSCR stab", f"{dscr_st:.2f}×", "vs 1.30", state))

    # Vacancy assumption (we use 8.0% headline, record city baseline as the threshold)
    vac_t = th_by_name.get("VACANCY_DEFAULT")
    if vac_t:
        you_vac = 0.08  # standard Eight Rock stabilized vacancy
        prop_vac = vac_t.effective_value
        state = "warn" if you_vac < prop_vac else "pos"
        cal_rows.append(_row("Vacancy", _fmt_pct(you_vac), f"vs record {_fmt_pct(prop_vac)}", state))

    # Exit cap
    exit_t = th_by_name.get("EXIT_CAP_DEFAULT")
    if exit_t:
        # Pull the property's own exit_cap from deal.json if available
        you_exit = None
        try:
            from data.property_io import load_deal as _load
            if folder := m.get("folder"):
                if hasattr(folder, "path"):
                    _d2 = _load(folder.path)
                    if _d2 and getattr(_d2, "exit_cap", None):
                        you_exit = _d2.exit_cap
        except Exception:
            pass
        if you_exit is not None:
            state = _state(you_exit >= exit_t.effective_value, warn=you_exit >= exit_t.effective_value * 0.97)
            cal_rows.append(_row("Exit cap", _fmt_pct(you_exit), f"vs {_fmt_pct(exit_t.effective_value)}", state))

    if cal_rows:
        # FRED · time pip — switched to ET 12-hour per Brian 5/29 v2.0.24
        when = _et_clock_now()
        blocks.append(f"""
<div class="v2-ins-block">
  <div class="v2-ins-head"><h3>Calibration</h3>{_calibration_help_html()}<span class="pip">FRED · {when}</span></div>
  <div class="v2-ins-body">{''.join(cal_rows)}</div>
</div>""")

    # ===== Diligence (always render when we have DD state; CSS hides
    # this block unless the Diligence tab is active — v2-on-diligence). =====
    m = metrics or {}
    dd_score = m.get("dd_score")
    dd_open = m.get("dd_open")
    dd_total = m.get("dd_total") or 49
    dd_dealbreakers = m.get("dd_dealbreakers", 0)
    dd_cat_scores = m.get("dd_category_scores") or {}

    # Display labels for the 9 DD categories (matches Brian's reference
    # screenshot — keys map from due_diligence module names).
    _CAT_LABELS = [
        ("ownershipTitle",          "Title & closing"),
        ("financial",               "Financial"),
        ("physicalCondition",       "Physical"),
        ("environmental",           "Environmental"),
        ("zoningRegulatory",        "Zoning / regulatory"),
        ("tenantConcentration",     "Tenant / lease"),
        ("legalLitigation",         "Legal / litigation"),
        ("market",                  "Market"),
        ("regulatoryMultifamily",   "Multifamily reg."),
    ]
    cat_rows = []
    for key, label in _CAT_LABELS:
        if key not in dd_cat_scores:
            continue
        raw = dd_cat_scores.get(key)
        if raw is None:
            val_html = '<span class="r">—</span>'
        else:
            try:
                v = float(raw)
                cls = "pos" if v >= 8 else ("warn" if v >= 5 else "neg")
                val_html = f'<span class="r {cls}">{v:.1f}</span>'
            except (TypeError, ValueError):
                val_html = '<span class="r">—</span>'
        cat_rows.append(
            f'<div class="v2-ins-row"><span class="l">{label}</span>{val_html}</div>'
        )

    # Render the diligence block whenever we have ANY DD state, even if
    # the overall score is None (unscored property) — show "—" gracefully.
    has_any_dd = (
        dd_score is not None
        or m.get("dd_total")
        or dd_cat_scores
    )
    if has_any_dd:
        score_int = int(dd_score) if dd_score is not None else 0
        bar = max(0, min(100, score_int)) if dd_score is not None else 0
        score_display = (
            f'<span class="v2-dd-score-n">{score_int}</span>'
            if dd_score is not None
            else '<span class="v2-dd-score-n">—</span>'
        )
        top_rows = [
            f'<div class="v2-ins-row"><span class="l">Open items</span>'
            f'<span class="r">{dd_open if dd_open is not None else "—"} / {dd_total}</span></div>',
            f'<div class="v2-ins-row"><span class="l">Dealbreakers</span>'
            f'<span class="r pos">{dd_dealbreakers}</span></div>',
        ]
        blocks.append(f"""
<div class="v2-ins-block v2-dd-inspector">
  <div class="v2-ins-head"><h3>Diligence</h3></div>
  <div class="v2-dd-score">{score_display}<span class="v2-dd-score-d">/ 100</span></div>
  <div class="v2-dd-bar"><div class="v2-dd-bar-fill" style="width:{bar}%"></div></div>
  <div class="v2-ins-body" style="padding-top: 0;">
    {''.join(top_rows)}
    {''.join(cat_rows)}
  </div>
</div>""")

    # Brian 5/29 v2.0.33 — Macro Context block REMOVED from the inspector
    # ("Remove this from all screens"). The debt-yield market source and
    # subject city / submarket details surface elsewhere on the
    # Underwriting tab (Market Calibration card) where they belong.

    # ===== People =====
    people: list[str] = []
    owner = (prop.get("owner") or "").strip()
    mgmt = (prop.get("management_company") or "").strip()
    if owner:
        initials = "".join(w[0].upper() for w in owner.split()[:2] if w and w[0].isalpha())[:2] or "—"
        people.append(
            f'<div class="v2-person"><div class="v2-person-avatar">{initials}</div>'
            f'<div class="v2-person-info"><div class="n">{owner[:40]}</div><div class="r">Owner of record</div></div></div>'
        )
    if mgmt:
        initials = "".join(w[0].upper() for w in mgmt.split()[:2] if w and w[0].isalpha())[:2] or "—"
        people.append(
            f'<div class="v2-person"><div class="v2-person-avatar">{initials}</div>'
            f'<div class="v2-person-info"><div class="n">{mgmt[:40]}</div><div class="r">Current management</div></div></div>'
        )
    if people:
        blocks.append(f"""
<div class="v2-ins-block">
  <div class="v2-ins-head"><h3>People</h3></div>
  <div class="v2-ins-body">{''.join(people)}</div>
</div>""")

    if blocks:
        st.markdown("".join(blocks), unsafe_allow_html=True)

    # ===== Key documents — read from the property folder =====
    # Brian 5/29 v2.0.33 — filter out app-internal state files (anything
    # Claude wrote to persist UI state). Brian is a user, not a developer;
    # JSON state files like `acquisition-checklist.json` shouldn't surface
    # as "documents." Only USER-uploaded materials should appear here.
    folder = m.get("folder")
    doc_files: list[Path] = []
    if folder and hasattr(folder, "path") and folder.path.exists():
        # Names that are app-internal state — hidden from the user.
        internal_names = {
            "deal.json", "sources.json", "sales.json", "notes.txt",
            "mystery_shops.json", "value_add_capex.json",
            "property_card_overrides.json", "acquisition-checklist.json",
            "due_diligence.json", "dd_state.json", "owner_portal.json",
            "investors.json", "events.json", "term_sheet.json",
            "_recent_views.json", "_favorites.json",
        }
        # Extensions that are NEVER user-facing documents in this folder.
        internal_exts = {".json"}

        def _is_user_facing(p: Path) -> bool:
            if not p.is_file():
                return False
            n = p.name
            ln = n.lower()
            if n.startswith(".") or n.startswith("~$") or n == "desktop.ini":
                return False
            if ln.startswith("_"):  # underscore-prefixed = internal convention
                return False
            if ln in internal_names:
                return False
            if p.suffix.lower() in internal_exts:
                return False
            return True

        try:
            for f in sorted(folder.path.iterdir()):
                if _is_user_facing(f):
                    doc_files.append(f)
                if len(doc_files) >= 6:
                    break
        except Exception:
            pass

    if doc_files:
        st.markdown(
            f'<div class="v2-doc-mark"></div>'
            f'<div class="v2-ins-block">'
            f'<div class="v2-ins-head"><h3>Key documents</h3>'
            f'<span class="pip">{len(doc_files)} files</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for f in doc_files:
            ext = f.suffix.lower().lstrip(".")
            if ext in ("xlsx", "xls", "csv"):
                label = "XLS"
            elif ext in ("docx", "doc"):
                label = "DOC"
            else:
                label = ext.upper()[:3] or "FILE"
            mtime_fmt = "%#m/%#d" if os.name == "nt" else "%-m/%-d"
            mtime = dt.datetime.fromtimestamp(f.stat().st_mtime).strftime(mtime_fmt)
            btn_label = f"{label}  ·  {f.name}  ·  {mtime}"
            if st.button(
                btn_label,
                key=f"v2_doc_open_{prop.get('property_id', 'na')}_{f.name}",
                use_container_width=True,
                help=f"Open {f.name} in its native application",
            ):
                _open_local_file(str(f.resolve()))


def _open_local_file(path: str) -> None:
    """Open a local file in its native application.

    Windows: os.startfile() invokes the file's shell handler (Excel for
    .xlsx, Acrobat for .pdf, etc.). Mac: 'open'. Linux: 'xdg-open'. Any
    failure surfaces as a Streamlit warning rather than a hard error so
    a missing handler never crashes the page.
    """
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        st.warning(f"Could not open {path}: {exc}")


# ---------------------------------------------------------------------------
# Cmd+K command palette + keyboard shortcuts
# ---------------------------------------------------------------------------

def _gather_palette_props() -> list[dict]:
    """Return the slim property list for the ⌘K palette (fuzzy search).

    Each entry: {id, name, addr, city, st, units, cls, _t (search tokens)}.
    Brian 5/30 v2.1.0 — covers the FULL multi-state inventory (was capped
    at 3,000 alphabetically, which truncated most of the 13K+ library and
    even dropped live deals like Crossroads). Records are slim (~150 bytes),
    so the full set embeds in a couple MB. Search tokens now include
    state + owner so ⌘K can find "all Drucker-owned props in Charlotte".
    """
    try:
        from data.db import list_properties
    except Exception:
        return []
    try:
        rows = list_properties(limit=50000)
    except Exception:
        return []
    out = []
    for p in rows:
        name = p.get("name") or "—"
        addr = (p.get("address") or "").strip()
        city = p.get("city") or "—"
        state = (p.get("state") or "").strip()
        cls = p.get("asset_class") or "—"
        units = p.get("units") or 0
        zip_ = str(p.get("zip") or "")
        owner = (p.get("owner") or "").strip()
        # Search tokens — pre-lowercased for fast client-side substring matching
        tokens = " ".join(filter(None, [
            name.lower(), addr.lower(), city.lower(), state.lower(),
            zip_.lower(), cls.lower(), owner.lower(),
        ]))
        out.append({
            "id": p.get("property_id") or "",
            "name": name,
            "addr": addr,
            "city": city,
            "st": state,
            "units": units,
            "cls": cls,
            "_t": tokens,
        })
    return out


def render_v2_cmdk_palette() -> None:
    """Cmd+K / Ctrl+K -> FOCUS the real in-place search field in the topbar.

    Brian 5/31 v2.1.2 — ⌘K now puts the cursor in the topbar's native
    search input (which lives right there in the bar), so you type and the
    results drop down in place. No navigation. This uses the ONLY mechanism
    that runs JS in Streamlit: an st.components.v1.html iframe whose script
    (same-origin) reaches window.parent.document to focus the input.

    Brian 5/31 v2.1.4 — tab navigation is now Alt+1..7 (was bare 1-9). Ctrl+
    1-9 is a RESERVED browser shortcut (switches browser tabs) that a page
    can't override, which is why Ctrl+number felt broken. Alt+number is free,
    so we bind that; each tab also gets an "Alt+N" hover tooltip to teach it.

    Why an iframe: Streamlit does NOT execute <script> injected via
    st.markdown — that silently broke every prior JS palette. The visible
    search itself is a native st.text_input (see render_v2_topbar), so the
    feature works even if this keyboard bridge is ever blocked.
    """
    try:
        from streamlit.components.v1 import html as _components_html
    except Exception:
        return
    _components_html(
        """
<script>
(function(){
  try {
    var pdoc = window.parent.document;
    function focusSearch(){
      var m = pdoc.querySelector('.v2-search-mark');
      var inp = null;
      if (m) {
        var col = m.closest('[data-testid=\"stColumn\"]') || m.parentElement;
        inp = col && col.querySelector('input');
      }
      if (!inp) inp = pdoc.querySelector('[data-testid=\"stTextInput\"] input');
      if (inp) { inp.focus(); inp.select && inp.select(); return true; }
      return false;
    }
    // Make the shortcut DISCOVERABLE: give each tab a hover tooltip naming
    // its Alt shortcut ("Alt+1" … "Alt+7"). Re-run on rerenders since
    // Streamlit rebuilds the tab buttons.
    function tagTabs(){
      var tabs = pdoc.querySelectorAll('button[data-baseweb=\"tab\"]');
      for (var i = 0; i < tabs.length && i < 9; i++){
        tabs[i].setAttribute('title', 'Keyboard: Alt+' + (i + 1));
      }
    }
    tagTabs();
    setTimeout(tagTabs, 400);
    setTimeout(tagTabs, 1200);
    setTimeout(tagTabs, 2500);
    if (pdoc.__quarrie_kbd) return;            // install listener once per page
    pdoc.__quarrie_kbd = true;
    pdoc.addEventListener('keydown', function(e){
      // Cmd/Ctrl+K -> focus the in-place search input
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        focusSearch();
        return;
      }
      // Alt+1..9 -> jump straight to that tab. We deliberately use ALT, not
      // Ctrl: browsers RESERVE Ctrl+1..9 to switch BROWSER tabs and a web
      // page cannot override them (that was the 'error-prone' behaviour).
      // e.code is keyboard-layout independent, so Alt producing an accented
      // character on some layouts doesn't break the match.
      if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-9]$/.test(e.code)) {
        var tabs = pdoc.querySelectorAll('button[data-baseweb=\"tab\"]');
        var i = parseInt(e.code.slice(5), 10) - 1;
        if (tabs[i]) { tabs[i].click(); e.preventDefault(); tagTabs(); }
        return;
      }
    }, true);
  } catch (err) { /* cross-origin / no parent -- the native input still works */ }
})();
</script>
""",
        height=0,
    )


def apply_query_param_to_state() -> None:
    """Read ?prop=<id> from URL and set selected_property_id in session state.

    Called at the very top of app.py main() BEFORE render_sidebar so the
    sidebar picks up the selection. Idempotent and safe to call every rerun.

    Returns silently if no query param or already applied.
    """
    try:
        qp = st.query_params
    except Exception:
        # Older Streamlit versions used experimental API
        return

    # Brian 5/29 v2.0.26 — breadcrumb "Pipeline" / "Active Deals" links
    # navigate to `?home=1` to clear the property selection and return
    # to the inventory view. Treat as an explicit reset.
    home_flag = qp.get("home")
    if isinstance(home_flag, list):
        home_flag = home_flag[0] if home_flag else None
    if home_flag:
        if st.session_state.get("selected_property_id"):
            st.session_state.pop("selected_property_id", None)
        st.session_state["active_module"] = "deal_analysis"
        try:
            # Drop the marker so a refresh doesn't keep clearing state.
            st.query_params.clear()
        except Exception:
            pass
        return

    prop_id = qp.get("prop")
    if not prop_id:
        return
    # st.query_params returns either str or list-of-str depending on version
    if isinstance(prop_id, list):
        prop_id = prop_id[0] if prop_id else None
    if not prop_id:
        return
    current = st.session_state.get("selected_property_id")
    if current != prop_id:
        st.session_state["selected_property_id"] = prop_id
        # Also force deal_analysis module so ⌘K from CRM/Portfolio/Help
        # routes user to the property page.
        st.session_state["active_module"] = "deal_analysis"
        # Brian 5/31 v2.1.2 — opening a property (e.g. by clicking a global
        # search result) clears the topbar search so its dropdown doesn't
        # linger on the property page. Safe here: this runs BEFORE the
        # search widget is instantiated, so it sets the widget's initial
        # value rather than mutating a live widget.
        if st.session_state.get("v2_global_search"):
            st.session_state["v2_global_search"] = ""


# ---------------------------------------------------------------------------
# Metric extraction — pulls computed values from session state
# ---------------------------------------------------------------------------

def gather_metrics(prop: dict, folder: Any = None) -> dict:
    """Gather underwriting metrics from Streamlit session state and the
    property folder. Returns a dict consumed by the stat bar / verdict /
    inspector renderers.

    Keys (all optional): asking, going_in_cap, dscr_year1, dscr_stab,
    coc_year1, coc_avg, irr_5y, equity_multiple, verdict_result, dd_score,
    dd_open, dd_total, dd_dealbreakers, folder, t12_period, t12_source_file.
    """
    m: dict = {"folder": folder}

    # ----- PRIMARY SOURCE: deal.json on disk + V1 underwriting pipeline -----
    # V1's Underwriting tab persists the slider state to deal.json via
    # data.property_io.save_deal(). We load that file, run the SAME
    # cash flow + IRR + DSCR pipeline V1's `_render_metrics` runs, and
    # surface the computed numbers (purchase_price, going_in_cap, dscr_stab,
    # irr_5y, equity_multiple, coc_year1) for V2's stat bar + verdict band.
    #
    # By reusing V1's `_derive_year1_inputs` + `core.calc` functions, V2's
    # numbers are guaranteed identical to V1's. Brian's "no impact to logic"
    # rule is respected — we're a pure reader / re-runner, not a modifier.
    if folder and hasattr(folder, "path"):
        try:
            from data.property_io import load_deal
            deal = load_deal(folder.path)
            if deal is not None:
                # Basics: purchase price + NOI come straight from deal.json
                m["purchase_price"] = float(deal.pp)
                m["noi_uw"] = float(deal.noi)

                # --- Now run V1's compute pipeline ---
                from core.calc import (
                    DebtTerms, build_debt_schedule, build_cashflow,
                    cap_rate, dscr, cash_on_cash, debt_yield, return_on_cost,
                    effective_year1_vacancy,
                )
                from core.irr import project_irr
                from ui.underwriting import _derive_year1_inputs
                import config

                # Reload sources.json as a raw dict (the underwriting helper expects this shape)
                sources_dict = None
                sj_path = folder.path / "sources.json"
                if sj_path.exists():
                    try:
                        with open(sj_path, encoding="utf-8") as f:
                            sources_dict = json.load(f)
                    except Exception:
                        sources_dict = None

                units = prop.get("units")
                city = prop.get("city")

                # Year-1 GPR + expenses (same recipe V1 uses)
                gpr, expenses = _derive_year1_inputs(deal, sources_dict, units, city=city)

                # Debt schedule
                debt_terms = DebtTerms(
                    loan_amount=deal.loan_amount,
                    annual_rate=deal.interest_rate,
                    amort_months=config.AMORT_MONTHS,
                    io_years=deal.io,
                )
                debt_sched = build_debt_schedule(debt_terms, deal.hp)

                # Year-1 effective vacancy (with reposition spike + stabilization ramp)
                year1_eff_vac = effective_year1_vacancy(
                    base_vac=deal.vacancy_frac,
                    spike_pp=deal.vac_spike_pp / 100.0,
                    stabilization_months=deal.stabilization_months,
                )

                # Cash flow projection (the IRR + EM source)
                cf = build_cashflow(
                    year1_gpr=gpr,
                    year1_vacancy_pct=year1_eff_vac,
                    year1_expenses=expenses,
                    rent_growth=deal.rent_growth,
                    expense_growth=deal.expense_growth,
                    am_fee_pct=deal.am_fee_pct,
                    debt=debt_sched,
                    hold_years=deal.hp,
                    exit_cap=deal.exit_cap,
                    equity_raise=deal.equity_raise,
                    stabilized_vacancy_pct=deal.vacancy_frac,
                    stabilization_year_break=1 if deal.stabilization_months <= 12 else 2,
                )

                # Metrics (mirror V1's _render_metrics exactly)
                m["going_in_cap"] = cap_rate(deal.noi, deal.pp)
                stabilized_noi = max(r.noi for r in cf.rows) if cf.rows else deal.noi
                m["stabilized_noi"] = stabilized_noi
                m["return_on_cost"] = return_on_cost(stabilized_noi, deal.pp)
                m["equity_multiple"] = cf.equity_multiple

                irr_v = project_irr(
                    equity_raise=deal.equity_raise,
                    annual_cashflows=[r.cash_flow for r in cf.rows],
                    exit_proceeds_net=cf.exit_proceeds_net,
                )
                if irr_v is not None:
                    m["irr_5y"] = irr_v

                # DSCR (year-1 going-in DSCR — used by V1 as the headline DSCR)
                ads_y1 = debt_sched.annual_payment[0]
                am_fee_y1 = cf.rows[0].am_fee if cf.rows else 0.0
                noi_after_am_y1 = deal.noi - am_fee_y1
                m["dscr_year1"] = dscr(noi_after_am_y1, ads_y1)

                # Stabilized DSCR (max NOI year, post am-fee, vs that year's debt service)
                # V1's render_metrics shows only year-1 DSCR; we add a stabilized
                # variant for the V2 stat card Brian explicitly asked for.
                if cf.rows:
                    stab_row = max(cf.rows, key=lambda r: r.noi)
                    stab_year_idx = cf.rows.index(stab_row)
                    stab_ads = debt_sched.annual_payment[min(stab_year_idx, len(debt_sched.annual_payment)-1)]
                    stab_noi_after_am = stab_row.noi - stab_row.am_fee
                    if stab_ads > 0:
                        m["dscr_stab"] = dscr(stab_noi_after_am, stab_ads)

                # Cash-on-cash + debt yield
                cf_y1 = noi_after_am_y1 - ads_y1
                m["coc_year1"] = cash_on_cash(cf_y1, deal.equity_raise)
                m["debt_yield"] = debt_yield(deal.noi, deal.loan_amount)
        except Exception as _e:
            # Defensive: if anything in V1's pipeline fails on this property
            # (missing fields, edge-case division-by-zero, etc.), keep going.
            # The stat bar / verdict will fall back to "Set in Underwriting tab"
            # for the affected fields rather than crashing the page.
            m.setdefault("_compute_error", str(_e)[:200])

    # ----- FALLBACK: in-process session state -----
    # If Brian has the Underwriting tab open in THIS same Streamlit process
    # and has edited the dials but NOT yet saved (no deal.json write yet),
    # the session_state holds the live values. Use as a fallback / overlay.
    ss = st.session_state
    pid = prop.get("property_id")
    pfx = f"uw_{pid}_" if pid else "uw_"

    for key in (
        "purchase_price", "going_in_cap", "stab_cap", "dscr_year1", "dscr_stab",
        "coc_year1", "coc_avg", "irr_5y", "equity_multiple",
    ):
        val = ss.get(f"{pfx}{key}") or ss.get(key)
        if val is not None:
            m[key] = val
    # Legacy: some session keys may still use "asking" -- accept either
    legacy_asking = ss.get(f"{pfx}asking") or ss.get("asking")
    if legacy_asking is not None and "purchase_price" not in m:
        m["purchase_price"] = legacy_asking

    # ----- T-12 NOI from sources.json (auxiliary) -----
    # Separate from deal.json's NOI -- sources.json holds the AI-extracted
    # T-12 NOI which may differ from the underwriting model's stabilized NOI.
    if folder and hasattr(folder, "path"):
        try:
            sj_path = folder.path / "sources.json"
            if sj_path.exists():
                with open(sj_path, encoding="utf-8") as f:
                    sj = json.load(f)
                noi = sj.get("noi")
                if isinstance(noi, dict):
                    noi_val = noi.get("value")
                    if isinstance(noi_val, (int, float)):
                        m["noi_t12"] = noi_val
                        m["t12_source_file"] = noi.get("file")
                # If we have pp + NOI but no cap, derive from T-12 NOI
                if "purchase_price" in m and "noi_t12" in m and "going_in_cap" not in m:
                    m["going_in_cap"] = m["noi_t12"] / m["purchase_price"]
        except Exception:
            pass

    # DD score — Brian 5/29 v2.0.27 — DDState is a dataclass, not a dict.
    # Pull real attributes: overall_risk_score, category_scores, items,
    # dealbreakers. Count open items by status.
    try:
        from core import due_diligence as dd_mod
        if folder and hasattr(folder, "path") and hasattr(dd_mod, "load_state"):
            dd_state = dd_mod.load_state(folder.path)
            if dd_state is not None:
                m["dd_score"] = getattr(dd_state, "overall_risk_score", None)
                items = getattr(dd_state, "items", []) or []
                done_statuses = {"Done", "Complete", "complete", "N/A", "na"}
                open_count = sum(
                    1 for it in items
                    if getattr(it, "status", None) not in done_statuses
                )
                m["dd_open"] = open_count
                m["dd_total"] = len(items)
                dealbreakers = getattr(dd_state, "dealbreakers", []) or []
                m["dd_dealbreakers"] = (
                    len(dealbreakers) if hasattr(dealbreakers, "__len__")
                    else int(dealbreakers or 0)
                )
                # category_scores: dict of categoryKey → score (or None
                # when uncomputed). Carry through for inspector render.
                cs = getattr(dd_state, "category_scores", None) or {}
                m["dd_category_scores"] = dict(cs)
    except Exception:
        pass

    return m


# ---------------------------------------------------------------------------
# Brian 5/29 v2.0.29 — V2 INVENTORY LANDING PAGE
# ---------------------------------------------------------------------------
#
# When V2 mode is active and no property is selected, render a proper
# landing page (was just `st.info("Pick a property from the sidebar…")`).
#
# Landing page sections:
#   1. Hero — "Workbench V2." + property count + rotating real-estate quote.
#   2. Search box — filters cards by name / address / city.
#   3. "Recently viewed" grid — properties opened recently (top of list).
#   4. "All properties" grid — everything else.
#
# Recent views persist to `_recent_views.json` next to the Properties
# folder so they survive across sessions + sync across V1↔V2.
# ---------------------------------------------------------------------------

_LANDING_QUOTES: list[tuple[str, str]] = [
    ("Andrew Carnegie",
     "Ninety percent of all millionaires become so through owning real estate."),
    ("Robert Kiyosaki",
     "Real estate provides the highest returns, the greatest values, "
     "and the least risk."),
    ("Theodore Roosevelt",
     "Every person who invests in well-selected real estate in a growing "
     "section of a prosperous community adopts the surest and safest "
     "method of becoming independent."),
    ("John D. Rockefeller",
     "The major fortunes in America have been made in land."),
    ("Marshall Field",
     "Buying real estate is not only the best way, the quickest way, the "
     "safest way, but the only way to become wealthy."),
    ("Mark Twain",
     "Buy land — they're not making it anymore."),
    ("Warren Buffett",
     "Risk comes from not knowing what you're doing."),
    ("Russell Sage",
     "Real estate is an imperishable asset, ever increasing in value. "
     "It is the most solid security that human ingenuity has devised."),
    ("Peter Drucker",
     "The best way to predict the future is to create it."),
    ("Franklin D. Roosevelt",
     "Real estate cannot be lost or stolen, nor can it be carried away. "
     "Purchased with common sense, paid for in full, and managed with "
     "reasonable care, it is about the safest investment in the world."),
    ("Louis Glickman",
     "The best investment on earth is earth."),
    ("Donald Trump",
     "It's tangible, it's solid, it's beautiful. It's artistic, from my "
     "standpoint, and I just love real estate."),
]


def _quote_of_the_day() -> tuple[str, str]:
    """Date-deterministic quote rotation. Stable within a day, changes
    at midnight local. Index = ordinal_day mod len(quotes)."""
    return _LANDING_QUOTES[dt.date.today().toordinal() % len(_LANDING_QUOTES)]


def _recent_views_path() -> Path:
    """JSON file storing the list of recently-viewed property_ids."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "Properties" / "_recent_views.json"


def load_recent_views() -> list[str]:
    """Return the ordered list of recently-viewed property_ids (newest
    first). Empty list when no file or unreadable."""
    fp = _recent_views_path()
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data if x]
    except (OSError, ValueError):
        pass
    return []


def record_property_view(property_id: str | None) -> None:
    """Push `property_id` to the front of the recent-views list. Dedupes
    repeat views; caps the list at 8 entries. Silent failure."""
    if not property_id:
        return
    pid = str(property_id)
    views = load_recent_views()
    views = [v for v in views if v != pid]
    views.insert(0, pid)
    views = views[:8]
    fp = _recent_views_path()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(views, indent=2), encoding="utf-8")
    except OSError:
        pass


def _render_v2_property_grid(properties: list[dict]) -> None:
    """Render a responsive grid of property cards. Each card links to
    `?prop=<id>` so click navigation works without a Streamlit handler."""
    if not properties:
        return
    cards: list[str] = []
    for p in properties:
        pid = p.get("property_id", "")
        name = p.get("name", "—")
        addr = p.get("address") or ""
        city = p.get("city") or ""
        state = p.get("state") or ""
        units = p.get("units")
        cls = p.get("asset_class") or "—"
        built = p.get("year_built")
        occ = p.get("occupancy_pct")

        units_str = f"{int(units):,}" if isinstance(units, (int, float)) else "—"
        built_str = f"{int(built)}" if isinstance(built, (int, float)) else "—"
        if isinstance(occ, (int, float)):
            occ_pct = occ if occ > 1 else occ * 100.0
            occ_str = f"{occ_pct:.0f}%"
        else:
            occ_str = "—"

        addr_line = ", ".join(x for x in [addr, f"{city}, {state}".strip(", ")] if x)
        # Class -> colored chip; occupancy -> color-coded (>=95 green,
        # 90-95 amber, <90 red). "-" values stay neutral.
        cls_key = cls if cls in ("A", "B", "C", "D") else "x"
        occ_cls = ""
        if isinstance(occ, (int, float)):
            pct = occ if occ > 1 else occ * 100.0
            occ_cls = (" occ-hi" if pct >= 95 else
                       " occ-mid" if pct >= 90 else " occ-lo")
        cards.append(
            f'<a class="v2-prop-card" href="?prop={pid}">'
            f'<div class="v2-prop-card-name">{name}</div>'
            f'<div class="v2-prop-card-addr">{addr_line}</div>'
            f'<div class="v2-prop-card-stats">'
            f'<div><span class="lbl">Units</span><span class="val">{units_str}</span></div>'
            f'<div><span class="lbl">Class</span>'
            f'<span class="v2-cls-chip v2-cls-{cls_key}">{cls}</span></div>'
            f'<div><span class="lbl">Built</span><span class="val">{built_str}</span></div>'
            f'<div><span class="lbl">Occ</span>'
            f'<span class="val{occ_cls}">{occ_str}</span></div>'
            f'</div>'
            f'</a>'
        )
    st.markdown(
        f'<div class="v2-prop-grid">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def render_v2_inventory_landing() -> None:
    """Render the V2 landing page (shown when no property is selected).

    Hero + rotating quote + search box + recently-viewed grid +
    inventory grid. Cards navigate via `?prop=<id>`.

    Brian 5/29 v2.0.31 — search bug fixed: was calling
    `list_properties(limit=500)` then filtering in Python, which silently
    dropped any property whose row sat past the limit cutoff (the DB has
    2,500+ properties). Now we pass the user's query straight to the
    `search=` param so the DB does the matching — name, address, city,
    market, owner, manager all covered. Recently-viewed properties are
    also explicitly fetched by ID so they ALWAYS appear, even if their
    row is past the no-search limit cap.
    """
    from data.db import list_properties, get_property

    author, quote_text = _quote_of_the_day()

    # Hero block — count phrase uses the TRUE inventory size.
    try:
        n_total = len(list_properties(limit=100000) or [])
    except Exception:
        n_total = 0
    st.markdown(
        f'<div class="v2-landing">'
        f'<div class="v2-landing-row">'
        f'<div>'
        f'<h1 class="v2-landing-title">Quarrie.</h1>'
        f'<div class="v2-landing-tagline">Where Eight Rock breaks ground.</div>'
        f'</div>'
        f'<div class="v2-landing-count">'
        f'<div class="num">{n_total:,}</div>'
        f'<div class="lbl">properties to dig into</div>'
        f'</div>'
        f'</div>'
        f'<div class="v2-landing-quote">'
        f'<span class="v2-landing-quote-text">&ldquo;{quote_text}&rdquo;</span>'
        f'<span class="v2-landing-quote-author">&nbsp;— {author}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Search box — placed AT THE TOP per Brian's markup.
    st.markdown(
        '<div class="v2-landing-search-wrap"></div>',
        unsafe_allow_html=True,
    )
    search = st.text_input(
        "Find a property by name, address, or city",
        placeholder="Search properties…",
        label_visibility="collapsed",
        key="v2_landing_search",
    )
    search_clean = (search or "").strip()

    # Fetch — DB-side filtering when searching, otherwise top-N.
    try:
        if search_clean:
            filtered = list_properties(search=search_clean, limit=300) or []
        else:
            filtered = list_properties(limit=500) or []
    except Exception:
        filtered = []

    # Recents — ALWAYS fetched explicitly by id so they appear even when
    # past the no-search inventory cap.
    recents = load_recent_views()
    recent_props: list[dict] = []
    if recents:
        present_ids = {p.get("property_id") for p in filtered}
        for pid in recents:
            if not pid:
                continue
            if pid in present_ids:
                # Use the row already loaded
                recent_props.append(next(
                    p for p in filtered if p.get("property_id") == pid
                ))
            else:
                try:
                    p = get_property(pid)
                except Exception:
                    p = None
                if p:
                    recent_props.append(p)
        # If we're searching, only keep recents that match the query too.
        if search_clean:
            sl = search_clean.lower()
            recent_props = [
                p for p in recent_props
                if sl in (p.get("name") or "").lower()
                or sl in (p.get("address") or "").lower()
                or sl in (p.get("city") or "").lower()
            ]

    # "All properties" grid = filtered MINUS recents (no dupes)
    recent_id_set = {p.get("property_id") for p in recent_props}
    other_props = [
        p for p in filtered
        if p.get("property_id") not in recent_id_set
    ]

    if recent_props:
        st.markdown(
            '<div class="v2-landing-section-head">Recently viewed</div>',
            unsafe_allow_html=True,
        )
        _render_v2_property_grid(recent_props)

    if other_props:
        if recent_props:
            st.markdown(
                '<div class="v2-landing-section-head">All properties</div>',
                unsafe_allow_html=True,
            )
        _render_v2_property_grid(other_props)

    if not recent_props and not other_props:
        st.info(
            f"No properties match `{search_clean}`. Clear the search box to "
            "see the full inventory."
            if search_clean else
            "No properties in the workbench yet. Add one from the sidebar "
            "to begin."
        )
