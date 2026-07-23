"""Shared UI primitives — most importantly the `section_card` wrapper used
across every tab to give each logical section a consistent bordered "card"
appearance (matches the property-header card in `ui/property_detail.py`).

Per Brian 2026-05-08: every section on every tab should sit inside a
rounded white card so the eye finds the boundaries between sections at a
glance, instead of scanning a wall of unbroken markdown.

Usage
-----
    from ui.components import section_card

    with section_card("Sale History"):
        render_sales(...)

    with section_card("Tax Assessment History", icon="🏛️", accent="ac"):
        render_assessment_history(...)

    with section_card(
        "Subject vs Market",
        icon=config.EXCEL_ICON_HTML,   # raw HTML icon instead of emoji
        accent="ac",
        subtitle="Rent-roll-priority subject metrics vs HUD/BAH/Census.",
    ):
        ...

The `accent` arg is a key into `config.COLORS` (e.g. "ac" for Eight Rock
gold, "src_rr" for rent-roll green). When set, a 3px left border in that
color is added to the title so the title block reads as a tinted ribbon —
useful for drawing the eye to a section that matters.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Iterator

import streamlit as st

import config


# ─────────────────────────────────────────────────────────────────────────
# Brian 5/29 v2.0.16: helper used to strip leading icons from titles when
# V2 mode is active. The `section_card` helper handles its own icon by
# itself; this helper is for OTHER section-like titles that bake an emoji
# into the label string (st.expander, markdown headers, etc.).
#
# Inline env check avoids a circular import with ui/v2_theme_05292026.py.
# ─────────────────────────────────────────────────────────────────────────

def _is_v2_mode() -> bool:
    return os.environ.get("ER_THEME", "").lower() == "v2"


_LEADING_ICON_RE = re.compile(r"^(#+\s+)?[^\w\s]+\s+(?=\w)")


# ─────────────────────────────────────────────────────────────────────────
# Brian 5/29 v2.0.20 — Central section-help dictionary.
#
# Every section_card whose title matches a key here gets a small ⓘ
# popover trigger placed next to the heading. Hover shows the short
# tooltip; click opens an inline popover with the detailed body so the
# analyst can read about the calc/definition WITHOUT navigating away.
#
# Add new entries here as new sections appear. Keys are matched against
# the section's `title` string exactly.
#
# Each entry: (short_tooltip_string, details_markdown_string).
# ─────────────────────────────────────────────────────────────────────────

SECTION_HELP: dict[str, tuple[str, str]] = {
    # --- Subject tab ---
    "Sale History": (
        "Recorded deeds and tax-assessor sale events.",
        "**Sale History** lists every recorded sale event for this property "
        "from the city's deed records and the tax assessor's transfer log.\n\n"
        "- **Date** — recording date (not closing date — which can be ~1–4 weeks earlier).\n"
        "- **Price** — gross consideration. LLC↔LLC transfers at $0 or $1 are flagged "
        "as non-arms-length.\n"
        "- **Grantor / Grantee** — seller / buyer of record. LLCs are unmasked when "
        "the deed names a member or the entity is tracked in the owners table.\n\n"
        "Use this to benchmark the seller's basis, spot quick flips, and verify "
        "the current owner before LOI."
    ),
    "Tax Assessment History": (
        "Annual assessed value from the city assessor.",
        "**Tax Assessment History** is the year-by-year assessed value from the "
        "city assessor's office (land + improvements).\n\n"
        "- The assessment usually trails market by 6–18 months and runs "
        "60–80% of true market value, but jumps year-over-year are a useful "
        "indicator that the assessor saw a comp or capex bump.\n"
        "- Reassessment cadence varies by city — Norfolk reassesses annually; "
        "Hampton + Newport News every 2 years; smaller cities every 4.\n\n"
        "Pair with **Sale History** to detect post-sale reassessment shock that "
        "the previous owner didn't budget for."
    ),
    "Property Card": (
        "Per-row source: rent roll → T-12 → OM → ALN → manual.",
        "**Property Card** resolves each field from the BEST source available, "
        "with manual overrides winning when set:\n\n"
        "1. **Manual** (gold) — values you entered via the ✏️ Edit popover. "
        "Your edits save automatically per property.\n"
        "2. **🟢 RR** — current rent roll's summary block.\n"
        "3. **🟠 T12** — trailing-12-month operating statement.\n"
        "4. **🟣 OM** — offering memorandum / marketing materials.\n"
        "5. **⚪ ALN** — ALN multifamily survey row (default fallback).\n\n"
        "Click **✏️ Edit** to override any field. Leave a field blank to revert "
        "to the auto-pull. Use **Clear all** to reset every override."
    ),
    "Notes": (
        "Free-form analyst notes — auto-saved as you type.",
        "**Notes** is a plain-text scratchpad — anything you type "
        "auto-saves automatically. Useful for "
        "open questions, contact log entries, or a running call summary.\n\n"
        "These notes don't feed any downstream model — they're for you and "
        "the team. The Artifact Engine on the Summary tab will incorporate "
        "them when present."
    ),
    "Documents": (
        "Files uploaded to the property folder.",
        "**Documents** lists every file in the property folder (T-12, rent "
        "roll, OM, photos, deeds, etc.) plus a **Re-parse** action that "
        "re-runs the document extractor over every file and refreshes "
        "`sources.json`.\n\n"
        "To upload new docs use the **Upload Property Materials** section "
        "below — it triggers AI extraction with per-field provenance."
    ),

    # --- Performance & Market ---
    "Comparables": (
        "Bucket 1 = ≤3 mi + same class. Bucket 2 = ≤5 mi + any class.",
        "**Comparables** finds nearby ALN-surveyed properties.\n\n"
        "- **Bucket 1 (gold)** — within 3 miles AND same asset class (most "
        "directly comparable; what an appraiser would use).\n"
        "- **Bucket 2** — within 5 miles, any class. Use to triangulate when "
        "Bucket 1 is thin (under 4 comps).\n\n"
        "Each row shows price/unit, occupancy, rent, year built, and current "
        "owner — the inputs you need for a defensible PPU bracket and going-in "
        "cap. Click a property name to jump to its own workbench page."
    ),
    "Rent Listing URLs": (
        "Active rent listings scraped for unit-level pricing.",
        "**Rent Listing URLs** holds the canonical URL for the subject AND "
        "Bucket-1 comps on every major rental site (Apartments.com, "
        "RentCafe, Zillow, plus 13 secondary marketing sites).\n\n"
        "- **Latest scrape squares** — the most recent successful scrape, "
        "clickable to open the source listing.\n"
        "- **Scrape now** — pulls fresh unit-level pricing from the URL "
        "below it. The result flows directly into the rent-gap calc on "
        "the Underwriting tab."
    ),
    "Data Sources & Last Refresh": (
        "Every ETL source with its last-pull timestamp.",
        "**Data Sources & Last Refresh** lists every external pull the "
        "workbench depends on (FRED, BLS, ALN, HUD BAH, Census BPS, HMDA, "
        "rent listings, etc.) with its last-pull timestamp and row count.\n\n"
        "- **Refresh All** — re-pulls every source.\n"
        "- **Per-source 🔄 Refresh** — re-pulls just that one (faster).\n\n"
        "Sources auto-refresh on a schedule: daily for FRED, weekly Monday "
        "for the multi-source bundle, monthly for the full pull, and "
        "annually on Sep 25 for the BAH reminder."
    ),

    # --- Underwriting ---
    "Deal Dials": (
        "The sliders that drive every downstream metric.",
        "**Deal Dials** is the canonical input panel — every metric on every "
        "tab keys off the values committed here.\n\n"
        "- **Purchase Price** — base for cap, PPU, leverage.\n"
        "- **NOI (T-12)** — pulled from the T-12 if uploaded; otherwise an "
        "ALN-derived estimate.\n"
        "- **Down %** — drives loan amount + LTV.\n"
        "- **Interest Rate / Amort / IO** — debt terms.\n"
        "- **Vacancy / Rent growth / Expense growth** — forecast assumptions.\n"
        "- **Exit Cap / Hold years / AM fee** — exit + sponsor economics.\n\n"
        "Click **Save dial** to commit your slider state — it persists "
        "across sessions and stays in sync between V1 and V2."
    ),
    "Year-1 KPIs": (
        "Headline KPIs computed off the live deal dials.",
        "**Year-1 KPIs** is a three-row grid:\n\n"
        "- **Row 1 — Headline (big tiles)** — Cap Rate · Project IRR · Untrended "
        "Return on Cost · Equity Multiple. The four numbers underwriters care "
        "about most.\n"
        "- **Row 2 — Returns & risk** — Y1 Cash on Cash · DSCR · Debt Yield · "
        "Breakeven Occupancy.\n"
        "- **Row 3 — Sizing & leverage** — Price/Unit · Loan Amount · LP Equity "
        "Raise · Cap − Debt Constant spread.\n\n"
        "Each tile is colored against the Eight Rock GO bar (green = pass, "
        "yellow = watch, red = NO-GO). Hover any tile for its formula + the "
        "WHY behind the threshold.\n\n"
        "Below the grid: NOI Trend strip showing T-12 → T-3 run rate → Year-1 "
        "Forecast → Stabilized so you can spot near-term momentum or decel."
    ),
    "Refi / Exit Stress Test": (
        "Beardsley's 4-scenario refi-at-exit risk test.",
        "**Refi / Exit Stress Test** simulates a hypothetical lender "
        "re-underwriting the deal at the exit year under 4 stress points, "
        "applies the 3-test max loan (DSCR / LTV / Debt Yield), and PASS/FAILs "
        "based on whether the new loan covers the existing balance.\n\n"
        "- **Scenario 1** — base case (your dials at exit).\n"
        "- **Scenario 2** — rates +150 bps, cap +50 bps.\n"
        "- **Scenario 3** — NOI −10% from your forecast.\n"
        "- **Scenario 4** — combo (rates +150, cap +50, NOI −10%).\n\n"
        "This is Beardsley's single most-important risk metric: it answers "
        "'**is this deal actually safe — or is it just safe IF nothing breaks?**'"
    ),
    "Rent Roll": (
        "Unit-level rents loaded from the most recent rent roll.",
        "**Rent Roll** is the unit-level view of the property — one row per "
        "unit with floorplan, sqft, market rent, actual rent, status (occupied / "
        "vacant / notice), tenant name, and lease term.\n\n"
        "- **Vacant** and **Notice** rows are tinted so they're visible at a "
        "glance.\n"
        "- The summary row at the bottom drives loss-to-lease, occupancy, and "
        "the T-3 NOI estimate.\n\n"
        "Auto-extracted from the rent-roll file (XLSX or PDF) you uploaded "
        "in the Documents section."
    ),
    "Value-Add Levers": (
        "21-lever income/expense menu (illustrative, read-only).",
        "**Value-Add Levers** is the Murray/Lindahl 21-lever menu — toggle "
        "each lever you intend to pull and see the annual NOI lift. "
        "Currently read-only; phase 2 will commit toggled levers as actual "
        "model adjustments to the 5-year cash flow."
    ),
    "Value-Add CAPEX (Short Hold)": (
        "Per-year unit renovation ramp → value created at exit.",
        "**Value-Add CAPEX** models a year-by-year unit-renovation ramp.\n\n"
        "**Inputs (per property):**\n"
        "- Units renovated each year (Y1–Y5)\n"
        "- Cost per renovated unit\n"
        "- Monthly rent bump per renovated unit\n\n"
        "**Math:**\n"
        "- Total CAPEX = Σ(units × cost/unit)\n"
        "- Stabilized annual rent ↑ = Σ(units) × $bump × 12\n"
        "- **Value at exit = rent ↑ ÷ exit cap** (capitalization)\n"
        "- $ value per $1 CAPEX = value at exit ÷ total CAPEX\n\n"
        "Saved per-property to `value_add_capex.json` — survives reruns + "
        "syncs across V1↔V2."
    ),
    "5-Year Cash Flow": (
        "Year-by-year GPR → NOI → Cash Flow projection.",
        "**5-Year Cash Flow** rolls every dial forward through the hold:\n\n"
        "- **GPR** grows at the rent-growth dial.\n"
        "- **Vacancy loss** = GPR × vacancy %. Year-1 includes the renovation "
        "spike if set; year-N reverts to the stabilized vacancy dial.\n"
        "- **EGI** = GPR − vacancy.\n"
        "- **Expenses** grow at the expense-growth dial.\n"
        "- **NOI** = EGI − expenses.\n"
        "- **AM fee** = NOI × AM fee %. Zero in the exit year (no fee on the "
        "sale year).\n"
        "- **Debt service** uses the amortization schedule; IO years pay "
        "interest only.\n"
        "- **Cash flow** = NOI − AM fee − debt service.\n\n"
        "The bottom block shows the **exit math**: Year-6 NOI ÷ Exit Cap → "
        "Gross Sale Proceeds, minus loan payoff = Net to Equity. Project IRR "
        "and Equity Multiple are computed off the full -equity → +cash flows "
        "→ +net-to-equity vector."
    ),
    "Amortization Schedule": (
        "Full 25-year P&I split.",
        "**Amortization Schedule** is the year-by-year breakdown of debt "
        "service into Principal + Interest for the full amortization term.\n\n"
        "Use it to spot the post-IO step-up (when the interest-only period "
        "burns off and the principal piece kicks in) — that's the cliff that "
        "wrecks DSCR in many deals if not modeled explicitly."
    ),
    "Sensitivity": (
        "Tornado view of DSCR / Cap / IRR / EM under shocks.",
        "**Sensitivity** stress-tests the deal across three dials:\n\n"
        "- Vacancy % (× 5 levels)\n"
        "- Rent growth % (× 5 levels)\n"
        "- Expense growth % (× 5 levels)\n\n"
        "Each cell shows the resulting Project IRR (or DSCR / Cap / EM "
        "depending on the view toggle). Red = below NO-GO bar; gold = watch; "
        "green = passes GO bar.\n\n"
        "The 'cone of plausibility' is the green region — if it shrinks to a "
        "narrow band, the deal is THIN and only works in a narrow assumption "
        "envelope."
    ),
    "Verdict": (
        "GO / WATCH / NO-GO based on calibrated thresholds.",
        "**Verdict** evaluates every metric against the live Market "
        "Calibration thresholds (city-specific overrides + config.py floor) "
        "and rolls them up to a single GO / WATCH / NO-GO call with the "
        "specific bars that passed/failed listed below.\n\n"
        "- **GO** — every metric clears its GO bar.\n"
        "- **WATCH** — at least one metric is below GO but above NO-GO.\n"
        "- **NO-GO** — at least one metric breaches NO-GO.\n"
        "- **FINANCING-CONSTRAINED-WATCH** — DSCR or debt yield is the binding "
        "constraint; pricing works at lower leverage.\n\n"
        "See Market Calibration below for the threshold inputs."
    ),
    "Market Calibration": (
        "Live thresholds: floor / market-pull / override.",
        "**Market Calibration** is the threshold engine.\n\n"
        "- **Floor** — `config.py` constants are the locked floor — Monday's "
        "cron job can only widen thresholds conservatively, never breach "
        "the floor.\n"
        "- **Market pull** — FRED rates, assessor cap rates, ALN occupancy "
        "rolling into the displayed values.\n"
        "- **Override** — per-city manual overrides (Norfolk vs Newport News "
        "vs Hampton each have their own).\n\n"
        "Adjust here when the market moves; the Verdict + Sensitivity sections "
        "above pick up changes on the next rerun."
    ),

    # --- Diligence ---
    "Risk Categories": (
        "DD scores rolled up by risk category.",
        "**Risk Categories** rolls up the master checklist into the 9 standard "
        "DD categories (Physical, Title, Lease, Environmental, Financial, "
        "Insurance, Tax, Survey, Zoning) and shows the per-category 0-100 score.\n\n"
        "Each category is weighted differently depending on the deal strategy "
        "(value-add, core, distressed). The overall score gates IC readiness — "
        "the IC Memo Validator tab won't pass until the DD score crosses the "
        "strategy-weighted threshold."
    ),
    "Master Checklist": (
        "Per-property DD checklist (49 default items).",
        "**Master Checklist** is the 49-item default DD list (from the "
        "cre-due-diligence skill) — one row per item with status "
        "(Open / In Progress / Done / N/A), notes, and the responsible party.\n\n"
        "Each item belongs to a risk category and contributes to that "
        "category's score when marked Done."
    ),

    # --- Returns / Waterfall ---
    "Investor Returns": (
        "LP IRR · Project IRR · LP EM · LP Capital Raised.",
        "**Investor Returns** is the four-tile headline for the waterfall:\n\n"
        "- **LP IRR** — the limited partner's internal rate of return AFTER "
        "the waterfall (pref + ROC + 70/30 promote splits). Target ≥ 15%.\n"
        "- **Project IRR** — the deal-level gross return BEFORE the GP "
        "promote. Target ≥ 18%.\n"
        "- **LP Equity Multiple** — total LP cash distributions ÷ LP capital "
        "in. Target ≥ 1.8x.\n"
        "- **LP Capital Raised** — Eight Rock convention is 100% LP capital, "
        "0% GP co-invest.\n\n"
        "The waterfall mechanics are in **Year-by-Year Waterfall** below."
    ),
    "Year-by-Year Waterfall": (
        "Pref accrual → ROC → 70/30 splits.",
        "**Year-by-Year Waterfall** shows the cash pot moving through three "
        "tiers each year:\n\n"
        "- **Tier 1 — 8% Pref** — LPs accrue 8% on unreturned capital. Pref "
        "carry rolls forward when the cash pot can't cover it.\n"
        "- **Tier 2 — Return of Capital** — once pref is current, remaining "
        "cash returns LP capital until LP capital is zeroed.\n"
        "- **Tier 3 — 70/30 Promote** — after ROC, residual cash splits "
        "70% to LP / 30% to GP.\n\n"
        "The 'Pref carry start / end' columns let you see when accrued pref "
        "is paid down vs rolled."
    ),
    "Distribution Summary": (
        "Total LP and GP cash flows for IRR.",
        "**Distribution Summary** lists the LP and GP cash flow vectors "
        "used to compute the IRRs above. Year 0 is the initial equity "
        "outflow; years 1..N are operating distributions; year N also "
        "includes the exit (sale year + final waterfall residual)."
    ),

    # --- Summary / Exec ---
    "Artifact Engine — LLM-Generated Deal Documents": (
        "LLM-generated artifacts saved to the property folder.",
        "**Artifact Engine** generates five LLM-powered analytical "
        "documents from the deal state:\n\n"
        "1. **Executive Summary** — 2-page overview.\n"
        "2. **Investor Memo (Trust-First)** — LP-facing memo.\n"
        "3. **IC Memo** — internal investment-committee memo.\n"
        "4. **Sponsor Q&A** — pre-empts LP questions.\n"
        "5. **Operating Plan** — first-100-days execution plan.\n\n"
        "Each artifact is generated from scratch using the workbench's "
        "live data — calibration thresholds, dial state, comps, "
        "rent roll, T-12. Generated docs save into the property folder "
        "alongside the source materials (no Generated/ subfolder).\n\n"
        "Requires `ANTHROPIC_API_KEY` in `.env` or environment."
    ),

    # --- Owner Portal (for completeness) ---
    "Term Sheet Evaluator": (
        "Compare a lender's term sheet against deal economics.",
        "**Term Sheet Evaluator** lets you paste in a lender's term sheet "
        "and see how it lands against the deal: max loan (DSCR / LTV / DY "
        "binding), debt service y1, post-IO step-up, and net cash to LP "
        "after origination + reserves."
    ),
    "Deal-level Settings": (
        "Sponsor-economics preferences (pref, splits, fees).",
        "**Deal-level Settings** holds the sponsor-side economics:\n"
        "- Preferred return (default 8%)\n"
        "- Tier 3 split (default 70/30 LP/GP)\n"
        "- Asset management fee % (default 2.0% of NOI)\n"
        "- Acquisition fee % (default 1.0% of pp)\n"
        "- Disposition fee % (default 1.0% of sale)"
    ),
    "Investors": (
        "LP capital ledger by investor.",
        "**Investors** is the LP capital ledger — one row per investor "
        "with their commitment, capital contributed to date, and current "
        "distribution position (pref owed, ROC remaining)."
    ),
    "Record an Event": (
        "Log a distribution, capital call, or investor update.",
        "**Record an Event** captures distributions, capital calls, IR "
        "updates, and ops events into the property's event log. Each "
        "event feeds the distribution engine + IR mailer."
    ),
    "Event History": (
        "Chronological event log for the property.",
        "**Event History** is the chronological log of every event "
        "recorded against the property — distributions, capital calls, "
        "IR communications, ops updates."
    ),
}


def _md_to_help_html(md: str) -> str:
    """Minimal markdown→HTML for SECTION_HELP details bodies. Handles the
    subset of markdown we actually use: bold (`**x**`), inline code
    (`` `x` ``), bullet lists (`- x`), and paragraphs (blank-line
    separated)."""
    import html as _html
    import re as _re
    s = _html.escape(md, quote=False)
    # Bold + inline code BEFORE list/paragraph processing so they don't
    # split mid-token.
    s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = _re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    out: list[str] = []
    in_ul = False
    for line in s.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("- ", "* ")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{stripped[2:]}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if not stripped:
            continue
        out.append(f"<p>{stripped}</p>")
    if in_ul:
        out.append("</ul>")
    return "".join(out)


def _section_help_html(title: str) -> str:
    """Return an inline HTML ⓘ control for the section heading. Renders
    as a native <details>/<summary> element so the browser handles open/
    close — no Streamlit popover (which was repeatedly fighting CSS and
    bleeding into the page background through over-broad :has selectors).

    Hover → browser title tooltip with the short summary.
    Click  → reveal the details panel.

    Returns "" when the title isn't in SECTION_HELP."""
    if not title or title not in SECTION_HELP:
        return ""
    import html as _html
    short, details = SECTION_HELP[title]
    body = _md_to_help_html(details)
    safe_title = _html.escape(title)
    safe_short = _html.escape(short, quote=True)
    # Brian 5/29 v2.0.26 — `name="v2-section-help"` makes every <details>
    # element on the page part of a single exclusive accordion group:
    # opening one auto-closes the others. Supported in Chrome 120+,
    # Safari 17.2+, Firefox 128+ (all 2023-2024).
    return (
        f'<details class="v2-section-help" name="v2-section-help">'
        f'<summary class="v2-section-help-trigger" '
        f'title="{safe_short}">i</summary>'
        f'<div class="v2-section-help-panel">'
        f'<div class="v2-section-help-panel-title">{safe_title}</div>'
        f'{body}'
        f'</div>'
        f'</details>'
    )


# Kept for backwards-compat with any leftover call sites — now a no-op
# wrapper that emits the HTML via st.markdown.
def _section_help_button(title: str) -> None:
    html_blob = _section_help_html(title)
    if not html_blob:
        return
    st.markdown(html_blob, unsafe_allow_html=True)


def v2_strip_icon(text: str) -> str:
    """Drop a leading emoji/symbol icon when V2 mode is active.

    Preserves leading markdown header tokens (``"##### "``) and only strips
    the icon character(s) between them and the actual title:

      "🎨 Data Source Color Key"      → "Data Source Color Key"
      "### 📋 Comp Call Checklist"    → "### Comp Call Checklist"
      "##### 🛠️ Value-Add Lever Menu" → "##### Value-Add Lever Menu"
      "Already plain text"            → "Already plain text"

    No-op in V1. Wrap titles passed to ``st.expander`` / ``st.markdown``
    / ``st.subheader`` etc. at the call site so V1 keeps its icons.
    """
    if not _is_v2_mode():
        return text
    return _LEADING_ICON_RE.sub(lambda m: m.group(1) or "", text)


@contextmanager
def section_card(
    title: str | None = None,
    *,
    icon: str = "",
    accent: str | None = None,
    subtitle: str | None = None,
    help_anchor: str | None = None,
    help_summary: str | None = None,
) -> Iterator[None]:
    """Wrap content in a bordered card with an optional title block.

    Args:
        title: section heading (rendered as bold 15px). Pass None for an
            unlabeled card (rare — use only when the inner content has its
            own header that should stay top-of-card).
        icon: emoji string or raw HTML (e.g. `config.EXCEL_ICON_HTML`)
            placed before the title.
        accent: key into `config.COLORS` (e.g. "ac", "src_rr"). When given,
            adds a 3px left ribbon to the title block in that color.
        subtitle: optional short caption rendered under the title in
            `tx2` color.
        help_anchor: optional anchor slug. When set, renders a small ⓘ
            button next to the title. Hover shows ``help_summary``; click
            switches to the Help module and scrolls to the matching
            section in ``ui/help_page.py``. Anchor strings must match the
            keys in ``ui.help_page.ANCHORS``.
        help_summary: one-line tooltip shown on hover over the ⓘ button.
            Required when ``help_anchor`` is set; ignored otherwise.
    """
    c = config.COLORS
    # Brian 5/29 v2.0.16: in V2 mode, suppress section-card icons entirely.
    # V1 still gets its icons. This is the single chokepoint — no per-call
    # edits needed across the codebase for section_card.
    if _is_v2_mode():
        icon = ""
    with st.container(border=True):
        if title is not None:
            accent_color = c.get(accent) if accent else None
            border_left = (
                f"border-left:3px solid {accent_color};padding-left:10px;"
                if accent_color
                else ""
            )
            sub_html = (
                f'<div style="font-size:12px;color:{c["tx2"]};'
                f'margin-top:3px;line-height:1.4">{subtitle}</div>'
                if subtitle
                else ""
            )
            icon_html = f'{icon} ' if icon else ""

            # Brian 5/29 v2.0.25 — section-help ⓘ is now an INLINE HTML
            # <details>/<summary> element appended to the title's flex
            # row. No more 2-column split, no more Streamlit popover,
            # no more :has() CSS hacks bleeding into the page background.
            # Browser handles the open/close natively + hover tooltip.
            help_html_blob = _section_help_html(title) if title else ""

            if help_anchor:
                # Legacy help_anchor → click jumps to the Help module page.
                col_title, col_help = st.columns([0.94, 0.06])
                with col_title:
                    st.markdown(
                        f'<div style="margin-bottom:10px;{border_left}">'
                        f'<div style="font-size:15px;font-weight:700;color:{c["tx"]};'
                        f'line-height:1.3;display:flex;align-items:center;gap:8px">'
                        f'<span>{icon_html}{title}</span></div>{sub_html}</div>',
                        unsafe_allow_html=True,
                    )
                with col_help:
                    if st.button(
                        "ⓘ",
                        key=f"section_help_btn::{help_anchor}",
                        help=help_summary or "Open Help",
                    ):
                        st.session_state["active_module"] = "help"
                        st.session_state["help_anchor"] = help_anchor
                        st.rerun()
            else:
                # Single-row title with the inline HTML help element. No
                # column split — the <details> sits next to the title text.
                st.markdown(
                    f'<div style="margin-bottom:10px;{border_left}">'
                    f'<div style="font-size:15px;font-weight:700;color:{c["tx"]};'
                    f'line-height:1.3;display:flex;align-items:center;gap:8px">'
                    f'<span>{icon_html}{title}</span>{help_html_blob}'
                    f'</div>{sub_html}</div>',
                    unsafe_allow_html=True,
                )
        yield


@contextmanager
def subsection_card(title: str | None = None) -> Iterator[None]:
    """Lighter-weight bordered card for nested sections. Same visual as
    `section_card` but no accent / subtitle / icon plumbing — keeps the
    inner card from out-shouting its parent.
    """
    c = config.COLORS
    with st.container(border=True):
        if title is not None:
            st.markdown(
                f'<div style="font-size:13px;font-weight:600;color:{c["tx2"]};'
                f'margin-bottom:8px">{title}</div>',
                unsafe_allow_html=True,
            )
        yield
