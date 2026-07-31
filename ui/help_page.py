"""Help — plain-English guide to the four headline Eight Rock features.

Audience: a 20-year-old reading the app for the first time. No real-estate
or financial-modeling background assumed. Each section follows the same
shape:

    What it is        — one paragraph, in everyday language.
    Why it's different — one paragraph comparing to CoStar / Yardi / etc.
    How it works       — three numbered steps.
    Where to find it   — which sidebar module + tab.
    A worked example   — concrete numbers walked through.

Section anchors (the `id=` values on the headings) match the `help_anchor`
values passed into `section_card()` calls elsewhere in the app, so the
ⓘ buttons next to feature headings can jump directly here via
``st.session_state["help_anchor"]``.

Per Brian's [[file-naming]] convention this file uses kebab-case as a Python
module (`help_page.py` rather than `help.py`) to avoid colliding with the
builtin `help` name.
"""

from __future__ import annotations

import streamlit as st

import config
from ui.components import section_card


# Anchor IDs — must match the help_anchor values passed to section_card calls.
ANCHORS = {
    "calibration":          "Market Calibration",
    "dd-bidirectional":     "Bidirectional DD Verdict Tightening",
    "acquisition-checklist": "AI Acquisition Checklist Co-Pilot",
    "forced-seller-radar":  "Forced-Seller Radar",
}


def render_help() -> None:
    """Render the Help document.

    Reads ``st.session_state["help_anchor"]`` to decide which section to
    auto-scroll to on load. The anchor is consumed (popped) so a second
    visit to Help doesn't jump again.
    """
    c = config.COLORS

    target_anchor = st.session_state.pop("help_anchor", None)

    _render_intro()
    _render_toc(target_anchor)
    _render_calibration_section()
    _render_dd_bidirectional_section()
    _render_acquisition_checklist_section()
    _render_forced_seller_radar_section()
    _render_footer()

    if target_anchor and target_anchor in ANCHORS:
        # After the page renders, scroll the matching anchor into view.
        # Using `scrollIntoView({block: "start"})` puts the heading at the
        # top of the viewport, not the middle, so the section is fully read.
        st.markdown(
            f"""
<script>
(function() {{
  const target = window.parent.document.getElementById("help-{target_anchor}");
  if (target) {{
    target.scrollIntoView({{behavior: "smooth", block: "start"}});
  }}
}})();
</script>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Intro + TOC
# ---------------------------------------------------------------------------

def _render_intro() -> None:
    c = config.COLORS
    with section_card("How this workbench is different", icon="❓", accent="ac"):
        st.markdown(
            f"""
Most real-estate software does **one** thing well — CoStar shows you data,
Yardi runs your back office, Excel templates do the math. This workbench
glues those together and adds four features that, as far as we can tell,
nobody else has put in one product:

1. **Market Calibration** — your underwriting bars move with the market.
2. **Bidirectional DD Verdict Tightening** — one finding ripples through every
   other verdict. *(🚧 design locked, build in progress.)*
3. **AI Acquisition Checklist Co-Pilot** — the AI fills in 70 of the 157
   close-of-deal checklist items from your uploaded docs. *(🚧 manual checklist
   ships today; auto-fill on the roadmap.)*
4. **Forced-Seller Radar** — find owners likely to sell BEFORE they list.

This page explains each one in everyday language. You don't need a finance
or real-estate background to follow along.
"""
        )


def _render_toc(target_anchor: str | None) -> None:
    c = config.COLORS
    with section_card("Table of Contents", icon="📑"):
        items_html = []
        for anchor, title in ANCHORS.items():
            highlight = (
                f"background:{c['blbg']};font-weight:700;"
                if anchor == target_anchor else ""
            )
            items_html.append(
                f'<a href="#help-{anchor}" '
                f'style="display:block;padding:8px 12px;margin-bottom:4px;'
                f'border-radius:4px;text-decoration:none;color:{c["ac3"]};'
                f'border:1px solid {c["bdr"]};{highlight}">'
                f'<b>{title}</b></a>'
            )
        st.markdown("\n".join(items_html), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section: Market Calibration
# ---------------------------------------------------------------------------

def _render_calibration_section() -> None:
    c = config.COLORS
    _anchor("calibration")
    with section_card(
        "1.  Market Calibration",
        icon="📐",
        accent="ac",
        subtitle="Your underwriting bars adapt to the market, every Monday.",
    ):
        st.markdown(
            f"""
##### What it is, in plain English

Imagine you're buying a used car. You walk in with a rule: "I won't pay
more than $10,000 unless it has under 50,000 miles." That's your **bar**.
Now imagine gas prices double overnight. Suddenly your $10,000 bar looks
generous — every other buyer is dropping out, and the dealer is desperate.
Your bar should *tighten*: maybe now you only buy for $8,500.

That's what **Market Calibration** does for real estate. Instead of
hardcoded thresholds (a "GO" cap rate of 6.5%, a "WATCH" of 7.0%), the
bars **move with the market every Monday morning**. When 10-year treasury
rates jump 50 basis points, your "GO" bar tightens automatically. When
local rent growth slows, your underwriting gets more conservative on
revenue assumptions.

##### Why this is different from CoStar / Yardi

- **CoStar** sells you raw data — sale comps, rent comps, ownership records.
  You still have to decide what to do with it.
- **Yardi** runs your accounting + property management. It doesn't tell
  you whether a deal pencils.
- **Standard underwriting templates** (Excel, Argus) use hardcoded
  thresholds set when the template was built. They don't adapt.

Our calibration **closes the loop**: it pulls fresh data from three
sources (FRED, property records, city assessor portals) every Monday, recomputes the
thresholds, and writes them back into your underwriting bars. As far as
we know, no commercial product does this.

##### How it works in 3 steps

1. **Pull fresh data.** Every Monday at 8 AM, a scheduled task pulls:
   - 10-year US Treasury yield (from FRED, the Federal Reserve's data API)
   - Regional rent growth and vacancy by submarket (from the property record)
   - Per-city tax assessments (from city assessor portals — Norfolk,
     Virginia Beach, etc.)
2. **Recompute thresholds.** A small ruleset turns the data into new
   threshold values. Example rule: **GO cap rate = base rate +
   (current 10-year treasury − baseline treasury)**. If treasuries spiked
   100 bps since calibration baseline was set, the GO bar moves up 100 bps.
3. **Lock the floor.** Your hand-set values in `config.py` are the
   **conservative floor** — the calibration cron can only **tighten** bars
   (make them harder to clear), never **loosen** below your manual setting.
   This protects against bad data: if a FRED outage returned a 1% treasury
   yield, the system won't drop your GO bar through the floor.

##### Where to see it in the app

**Subject tab → Market Calibration card.** The subtitle on the card tells
you when it was last recalibrated and how many thresholds are currently
"market-widened" (tightened above your floor), "overridden" (you manually
set them), or "at floor" (still at your hand-set value).

##### A worked example

It's **February 1**. The 10-year treasury is at 4.0%. Your `config.py`
sets GO cap rate at 6.5%. Calibration sees no spread vs baseline, so the
GO bar stays at 6.5%.

It's **May 1**. The 10-year jumped to 4.7%. Calibration's rule says: GO
cap = 6.5% + (4.7% − 4.0%) = **7.2%**. The bar tightens automatically.

A deal you're underwriting comes in at a **6.8% cap rate**. In February
that would've been "GO" (6.8% > 6.5%). On May 1 it's now "WATCH" (6.8% <
7.2%). The system caught the regime change without you having to remember
to update a spreadsheet.

##### Why it's defensible

The novelty isn't pulling data — anyone can hit FRED. It's the
**floor-locked, market-widening, multi-source rule engine** that writes
back into the underwriting decision thresholds, with full audit history
(`calibration_history` SQLite table) of when each bar moved and why.
"""
        )


# ---------------------------------------------------------------------------
# Section: Bidirectional DD Verdict Tightening
# ---------------------------------------------------------------------------

def _render_dd_bidirectional_section() -> None:
    c = config.COLORS
    _anchor("dd-bidirectional")
    with section_card(
        "2.  Bidirectional DD Verdict Tightening",
        icon="📋",
        accent="ac",
        subtitle="🚧  Design locked, build in progress.",
    ):
        st.markdown(
            f"""
> **Status:** The Due Diligence checklist (49 items, 9 categories) and the
> per-category risk scoring **already ship** today. The **bidirectional
> re-propagation** described below is the next chunk — design locked,
> implementation queued behind the cloud-foundation work.

##### What it is, in plain English

Due diligence is the period between signing a Letter of Intent and closing,
when you verify everything the seller told you. There are 49 specific
things to check — roof condition, electrical, lease files, capex history,
tax history, environmental, etc.

In most software, each check is **independent**. You find a roof problem,
you mark "Roof" red. You move on to electrical. They don't talk to each
other.

In real life, **findings ripple**. If the roof needs $80k, that money has
to come from somewhere — usually it eats into your capex budget. That
might tighten the *financing* verdict (lender wants more reserves). The
extra capex push timing might delay stabilization by 4 months — that
tightens the *exit timing* verdict. And going the *other* direction:
if you discover the seller is providing a 12-month tax abatement, the
*tax verdict* loosens, which loosens the *Year-1 NOI* projection, which
loosens the *debt service coverage* verdict.

Our system models the 9 DD categories as a **graph of dependencies**.
When ANY item changes, the system recomputes verdicts both **downstream**
(tightening cascades) and **upstream** (loosening cascades). You see the
ripple, with arrows showing which verdicts moved and why.

##### Why this is different from anything in market

Every DD product we've reviewed (DealPath, CRELink, the AppFolio DD module)
treats checklist items as independent. Find a problem on item 23, mark it
red, move on. Our DAG-based re-propagation is, to our knowledge, new in
the multifamily DD product space.

##### How it will work in 3 steps

1. **Define dependencies.** Each of the 49 items is a node in a graph.
   Edges encode "if this item moves, these other items should re-check."
   Built once, version-controlled.
2. **Detect a verdict change.** When you (or the AI) updates a DD item's
   status, risk score, or finding, the system walks the DAG forward and
   backward to find affected items.
3. **Recompute + flag.** Affected items get a yellow "re-check" badge.
   Click any of them and you see what changed upstream and the new
   suggested verdict. You accept or override.

##### Where to see it in the app

**Deal Analysis module → Due Diligence tab.** The header card today shows
overall risk score, dealbreaker count, and IC-readiness badge. When the
bidirectional logic ships, the same tab will get a new "Verdict Ripples"
strip showing which downstream and upstream items moved on your last edit.

##### A worked example

You start DD on a 100-unit deal. Initial verdict: GO. You run the roof
inspection — finds 4 buildings with end-of-life shingles, $120k of capex
to address.

**Forward ripple:**
- Capex budget verdict tightens: was $5,000/unit, finding adds $1,200/unit
  on top.
- Financing verdict tightens: lender's required capex reserve increases.
- Exit timing verdict tightens: 4-month renovation delay pushes
  stabilization from month 18 to month 22, shrinking IRR.

**Backward ripple (a week later):**
You negotiate a $150k seller credit. Capex verdict loosens (now net-positive
on the roof). Financing verdict can loosen back (reserve isn't needed).
**But** the timing verdict doesn't fully reverse — the renovation still
takes 4 months even if the money problem is solved. The system catches
this nuance: not every backward-ripple fully undoes a forward-ripple.

##### Why it's defensible

The DAG-based re-propagation, with **both forward (tightening) and
backward (loosening)** edges, applied to a standardized 9-category
multifamily DD model, is the novel piece. The graph itself (which items
depend on which) is the trade-secret part — it encodes Eight Rock's
underwriting judgment.
"""
        )


# ---------------------------------------------------------------------------
# Section: AI Acquisition Checklist Co-Pilot
# ---------------------------------------------------------------------------

def _render_acquisition_checklist_section() -> None:
    c = config.COLORS
    _anchor("acquisition-checklist")
    with section_card(
        "3.  AI Acquisition Checklist Co-Pilot",
        icon="📅",
        accent="ac",
        subtitle="🚧  157-item checklist ships today; AI auto-fill on the roadmap.",
    ):
        st.markdown(
            f"""
> **Status:** The 157-item, 8-phase checklist (LOI Acceptance → Close →
> Day-90 Post-Closing) is **live** today in the Acquisition Checklist tab.
> Per-property state, phase navigation, progress tracking, PDF export
> all work. The **AI auto-fill** — reading your OM/T-12/rent-roll and
> pre-populating ~70 of the 157 items with citations — is the next build.

##### What it is, in plain English

Once you sign a Letter of Intent and shake hands with the seller, there
are 157 specific things you have to do before you can close — order title
work, get insurance bids, send tenant estoppels, confirm tax assessments,
set up bank accounts, take over utility billing, etc. Done well, this
phase takes a small team a few weeks. Done badly, deals fall apart at
the closing table.

Eight Rock's 157-item checklist (built from Lindahl, Beardsley, and our
own deal history) covers all of it. Today, you have to fill it in by
hand. The **AI Co-Pilot** reads every doc you upload — Offering Memo,
trailing-12 P&L, current rent roll, market study, environmental Phase I,
title commitment, survey, prior tax bills — and **auto-fills about 70 of
the 157 items**, with a citation to the exact page or line where it
found the answer.

You verify each AI-filled answer, edit it if wrong, or override entirely.
What used to take a full day of typing becomes about an hour of clicking
"verified" or "fix this."

##### Why this is different from just-using-ChatGPT

You **could** paste your OM into ChatGPT and ask it questions. People do.
What ChatGPT can't do:

- **Hold state across 157 specific items.** It doesn't know which items
  you've already filled in, which need verification, which you've
  overridden.
- **Cite WHERE in the doc the answer came from.** It hallucinates citations.
- **Re-process when docs update.** Half-way through DD, the seller sends a
  revised rent roll. ChatGPT can't tell you which of your 157 items now
  need rechecking. Our co-pilot diffs the new doc against the old one
  and re-flags only the affected items.
- **Survive across sessions.** Today's ChatGPT chat doesn't remember
  yesterday's. Our checklist state is durable, per-property.

##### How it works in 3 steps

1. **You upload docs** to the Subject tab — OM, T-12, rent roll, etc.
2. **The co-pilot reads each doc** and extracts structured answers per
   checklist item. Example: item 47 "Verify property tax rate" gets
   filled with `1.23 per $100, source: OM page 41, line 'RE Taxes'`.
3. **You verify in the Acquisition Checklist tab.** Each AI-filled item
   shows a 🤖 badge, the answer, and the source citation. One click:
   "Verified." Done. The badge turns green.

##### Where to see it in the app

**Deal Analysis module → 📅 Acquisition Checklist tab.** The manual
checklist with phase navigator and per-property progress saves to
`acquisition-checklist.json` inside the property folder. When AI auto-fill
ships, each item will gain a 🤖 button to "ask the co-pilot to fill this."

##### A worked example

You're closing on Crossroads Townhomes. You upload:
- Newmark OM (PDF, 47 pages)
- March 2026 T-12 (xlsx)
- 4/17/2026 rent roll (xlsx)
- AR aging (xlsx)

The co-pilot reads all four. It fills in:
- Item 12 "Total units": 26 (rent roll, header row)
- Item 47 "Tax rate": 1.23 per $100 (OM page 31)
- Item 89 "Current occupancy": 92.3% (rent roll, 22 occupied of 24)
- Item 103 "Last roof replacement year": 2003-2015 (OM page 18)
- ... 66 more items.

You spend 40 minutes clicking "Verified" on the 70 AI-filled items. The
remaining 87 (things like "confirm insurance quotes received" or "tenant
estoppel returns") still require human work — those happen during DD.

##### Why it's defensible

The novelty isn't the LLM call — anyone can call Claude or GPT-4. It's
the **structured 157-item state machine** with per-item source citations,
diff-aware re-processing on doc updates, and durable per-property storage.
The checklist itself is also non-trivial IP — it's distilled from
thousands of multifamily closings.
"""
        )


# ---------------------------------------------------------------------------
# Section: Forced-Seller Radar
# ---------------------------------------------------------------------------

def _render_forced_seller_radar_section() -> None:
    c = config.COLORS
    _anchor("forced-seller-radar")
    with section_card(
        "4.  Forced-Seller Radar",
        icon="🎯",
        accent="ac",
        subtitle="Find owners likely to sell BEFORE they list.",
    ):
        st.markdown(
            f"""
##### What it is, in plain English

When a property goes on the market through a broker, every buyer in the
market sees it at the same time. You pay full price (or close to it).

But every property has an owner with a story. Some of those stories end
in "we need to sell, soon" — a loan maturing they can't refinance, a
partnership splitting up, a fund's hold period ending, an owner who's
gotten old and tired. They haven't called a broker yet. **If you can
find them first**, you negotiate without competition, often at a 10-20%
discount.

The **Forced-Seller Radar** scans every property in our Hampton Roads
watchlist (3,370 parcels) and scores each one on **6 distress signals**.
A high composite score = an owner who has reasons to sell. You see the
top 25 ranked by score, every one with the specific signals that flagged
it, so you know WHY it's on the list before you pick up the phone.

##### Why this is different from anything in market

- **Reonomy / CompStak** sell raw ownership and loan data. They don't
  score it.
- **Brokers** push deals they're already listing — by definition, you're
  not first.
- **Loan-pull lists** (e.g., commercial loans maturing in 18 months) are
  available as a service, but they're just one signal.

Our composite combines **6 signals** — each independently meaningful, but
collectively much more predictive than any one alone. To our knowledge,
this exact multi-signal multifamily distress scoring product doesn't
exist commercially.

##### How it works in 3 steps

1. **Weekly data pull.** Every Monday's scheduled task refreshes the data
   feeding all 6 signals.
2. **Score each property 0-100 on each signal.** Then weight them into a
   composite (higher = more distress likelihood).
3. **Rank + display.** The CRM module's Forced-Seller Radar card shows
   the top N (you pick — default 25) with each signal visible per row.

##### The 6 signals and how points are awarded

Each property is scored 0-N on each signal; points sum to the composite,
which is then capped at 100. Theoretical max before the cap is 120, so a
score of 100 means the property hit the top tier on roughly five of six
signals. The exact thresholds are in `core/distress_radar.py`.

| # | Signal | Points | When it fires |
|---|---|---:|---|
| 1 | **Sale vintage** | **+30** | Bought 2020-2022 (bridge-loan era — maturity wall coming due). Property must have a recorded sale ≥ $1M. |
|   |  | +8 | Bought 2023 (locked in already-high rates; some refinance pressure). |
| 2 | **Rate shock since purchase** | **+30** | Current 10Y treasury (from FRED `DGS10`) is ≥ 3 pp above estimated rate at the time of sale. |
|   |  | +20 | Shock ≥ 2 pp. |
|   |  | +10 | Shock ≥ 1 pp. |
| 3 | **Recent assessment jump** | **+25** | Latest assessed value ≥ 40 % above prior year (proxy for a 2024-25 sale at premium). |
|   |  | +15 | Latest assessment ≥ 20 % above prior. |
| 4 | **Holding period** | **+10** | Owner has held the property 5-8 years (the typical institutional disposition window). |
|   |  | +8 | Held > 15 years (often tax-motivated retirement sale, 1031 motivation gone). |
| 5 | **Institutional ownership** | **+10** | Owner name contains any of: `LLC`, `LP`, `L.P.`, `FUND`, `CAPITAL`, `PARTNERS`, `INC`, `CORP`, `TRUST`, `REIT`, `INVESTORS`, `ACQUISITION`. Funds have hold periods and structured liquidity needs that mom-and-pop owners don't. |
| 6 | **Market softness** | **+15** | County/city unemployment (from BLS LAUS) ≥ 6.0 %. |
|   |  | +8 | Unemployment ≥ 5.0 %. |

The composite is *not* a probability — it's a relative ranking score.
A property scoring 80 is meaningfully more distress-likely than one
scoring 50; the gap between 95 and 100 is mostly noise.

##### Where to see it in the app

**CRM & Sourcing module → 🎯 Pipeline & Sourcing tab → Forced-Seller
Radar card.** Sliders let you set top N, minimum assessed value, and
city filter. Each row shows:

- **Score** (0-100 composite)
- **City / Address / Owner / Year built / Units (est)**
- **Last Sale date** and **Sale Price** — the inputs to signals 1 & 2
- **Assessed** — the latest value
- **Reasons** — the human-readable list of which specific signals fired
  on this row (e.g., *"Bought 2021 (bridge-loan vintage); Rate shock:
  bought at ~1.2% 10Y, refi at 4.5%; Institutional owner (Curlew Apts Ii,
  Lp; structured for liquidity); Norfolk unemployment elevated (6.2%)"*).

The CSV export ("📥 Download skip-trace CSV") gives you the full table
formatted for mail-merge into outreach letters.

##### A worked example

The radar surfaces *Huntington Pointe Apartments* (2031 Stanford Ln,
Newport News) at the top of today's list with a **composite score of
100**. Reading the **Reasons** cell, we see:

| Signal | Trigger | Points |
|---|---|---:|
| Sale vintage | Bought 2021 (bridge-loan era) | +30 |
| Rate shock | Bought at ~1.2% 10Y, current 4.5% (+330 bps) | +30 |
| Assessment jump | Latest assessment +44 % vs prior | +25 |
| Institutional owner | "Huntington Pointe Apartments L…" (LP) | +10 |
| Holding period | 5 yrs (disposition window) | +10 |
| Market softness | Newport News unemployment 6.1 % | +15 |
| **Raw total** |  | **120** |
| **Capped composite** |  | **100** |

The story this composite tells: an institutional LP bought at the bottom
of the rate cycle, the market has reassessed the property up sharply
(other buyers know it's worth more), the operator is sitting on a
maturity wall they can't refinance into without bringing fresh equity,
and the local job market is no longer providing the rent growth that
would otherwise paper over the gap. **That is exactly the seller you want
to reach before they call a broker.**

You hand them a soft LOI at an 8-10 % discount to the broker comp.
Three weeks later, you're under contract — months before the property
would have hit market.

##### Why it's defensible

The novelty is the **specific multi-signal composite tuned for
multifamily**, embedded in a CRM workflow (not a data feed), with weekly
auto-refresh. The signal selection and weights are tunable in code —
they encode Eight Rock's read of what predicts distress in this
asset class.
"""
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _anchor(anchor_id: str) -> None:
    """Emit an invisible anchor element so the in-page scroll can target it.

    The `id="help-<anchor>"` matches the `target_anchor` lookup in
    ``render_help`` and the `help_anchor` values passed to ``section_card``
    calls elsewhere.
    """
    st.markdown(
        f'<div id="help-{anchor_id}" style="height:1px;margin-top:-20px"></div>',
        unsafe_allow_html=True,
    )


def _render_footer() -> None:
    c = config.COLORS
    st.markdown(
        f'<div style="margin-top:32px;padding:16px;background:{c["bg3"]};'
        f'border:1px solid {c["bdr"]};border-radius:6px;font-size:13px;'
        f'color:{c["tx2"]};line-height:1.6">'
        f'<b style="color:{c["tx"]}">Want a quick tour?</b><br>'
        f'Click any ⓘ icon next to a feature heading anywhere in the app — '
        f'hover for a one-line summary, click to jump back to this Help '
        f'document with the matching section opened.'
        f'</div>',
        unsafe_allow_html=True,
    )
