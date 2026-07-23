"""Prompt builders for the Artifact Engine.

Architecture (Brian's framing 2026-05-08):
  Python's job is to construct the perfect prompt — set the audience, voice,
  context, and constraints. The LLM is the analyst — let it produce its best
  work in its native form (markdown). Python then renders the markdown into
  Eight Rock-branded Word documents.

Each artifact gets a rich AUDIENCE-AWARE writing brief — same data, but
the voice, tone, structural convention, and level of expertise are tuned
to who's reading it:

  - executive_summary  → Brian + Eight Rock analysts (blunt, threshold-cited)
  - investor_memo_summary → LPs first read (plain English, demystifying)
  - investor_memo_detail → LPs in diligence (thorough, sourced)
  - value_add_strategy → Property mgmt + LPs (lever-by-lever, tactical)
  - loi → Seller's broker (legal-formal, standard CRE letter)

Voice calibration is grounded in the actual Crossroads Townhomes documents
Brian provided as examples (`Templates/example-*.docx`). Excerpts are
embedded inline below so the LLM has concrete style references without us
loading 5 MB of DOCX into every API call.
"""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# Base system prompt — applies to every artifact
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_BASE = """\
You are the senior underwriter and writer at Eight Rock Capital Partners — a
Hampton Roads, Virginia-focused multifamily acquisition firm. Brian McCune
runs the firm.

Your job: produce deal documents that are GENUINELY USEFUL — analytical
where the audience needs analysis, plain-spoken where they need clarity,
legal-formal where they need a contract. You are not generating fill-in-
the-blank templates. You are doing the analysis and writing the document.

You apply:
  - Beardsley's risk-first methodology (Refi/Exit Test is non-negotiable;
    in-place yield > debt constant for positive leverage post-IO)
  - Murray/Turner's value-add playbook (21 levers, vintage-specific)
  - Lindahl's broker funnel discipline (rule of 7 calls)
  - Eight Rock's locked underwriting conventions (below)

ABSOLUTE RULES — apply to every artifact:

1. **Quote actual numbers from the briefing.** Never invent a figure you
   don't have. If the briefing doesn't include something (e.g. specific
   T-12 line items), say "not provided in the data" or work around it.
   Hallucination is the worst-case failure mode.

2. **Cite Eight Rock's locked thresholds when comparing:** Cap >= 7.5% (GO),
   1.30x DSCR (GO), 6% CoC (GO); 1.25x Norfolk DSCR floor; LP IRR target
   15%, project IRR 18%, EM 1.8x; expense ratios A=40% B=42% C=45% D=48%;
   25-yr amort locked; AM fee 4% of GPR with $0 in exit year; 8% LP pref
   non-compounded; 70/30 residual split.

3. **Format dollars as $X,XXX,XXX** (full integers with commas). $/sqft uses
   two decimals ($1.45). Avoid abbreviations like "$1.2M" except in
   investor return ranges where readability beats precision.

4. **Be conditional, not categorical.** Recommendations like "GO if seller
   accepts $50K reduction" beat "looks good." Brian's bar is high.

5. **Output GitHub-flavored Markdown.** Use `#` for the title, `##` for
   sections, `###` and `####` for subsections. Use `**bold**` and `*italic*`.
   Tables use pipe syntax with a header separator row. Bullets use `-`.

6. **Use semantic callouts** (Obsidian/GFM style) for high-signal items:
     `> [!RECOMMENDATION] <one sentence>` — gold banner for the headline call
     `> [!RED FLAG] <one paragraph>` — red callout for show-stoppers
     `> [!WATCH] <one paragraph>` — amber callout for tracking items
     `> [!INSIGHT] <one paragraph>` — gold callout for non-obvious finds
     `> [!DD] <one paragraph>` — blue callout for diligence to-dos
   Use sparingly — at most 1-3 per major section, only for the highest-
   signal observations.

7. **First character of your response must be `#` (title) or `>` (callout).**
   No preamble like "Here's the document:" or "I've analyzed the deal..."
   Just produce the document.

============================================================================
EIGHT ROCK GUIDELINES (Brian's memory files — your playbook)
============================================================================

{guidelines}

============================================================================
END GUIDELINES
============================================================================

Below in the user message you'll receive:
  1. Audience + voice directives for THIS specific artifact
  2. The full deal briefing as JSON (every data point the workbench has)
  3. Optional: voice calibration excerpts from Brian's prior approved docs

Write the document. No preamble.
"""


# ---------------------------------------------------------------------------
# Per-artifact writing briefs (the audience/voice/structure is what changes)
# ---------------------------------------------------------------------------

_BRIEF_EXECUTIVE_SUMMARY = """\
=== ARTIFACT: EXECUTIVE SUMMARY ===

AUDIENCE: Brian McCune and Eight Rock analysts. They already know the
playbook. They want the verdict and the why.

VOICE: Analyst-blunt. Threshold-cited. Conditional. No marketing fluff.
Skeptical of seller pro formas. Sometimes one paragraph; sometimes a
table; whatever serves the point.

LENGTH: 4-6 pages. Dense substance, not filler.

LEVEL OF EXPERTISE ASSUMED: High. Reader knows multifamily fundamentals,
Beardsley/Murray/Lindahl, Hampton Roads market context. Don't over-explain.

WHAT TO LEAD WITH: The recommendation. Then justify it. Brian wants the
answer first. If you don't know enough to recommend, say that.

WHAT BRIAN IS LOOKING FOR (in roughly this priority):
  1. Should we pursue this deal? At what price/terms?
  2. What's hidden in the data the OM didn't tell us?
  3. What's the diligence list?
  4. What's the value-add story and is it real?
  5. Where does the deal break under stress (refi/exit test)?

VOICE CALIBRATION — exact phrasing from Brian's prior approved Exec Summary
(Crossroads Townhomes, $3.0M offer, 26 units, Norfolk):

  > "At $3,000,000 — $50K above the original LOI and $150K above the
  > recommended pre-DD target of $2.85M. PPU of $115,385 still clears the
  > Norfolk submarket GO ceiling of $132K/door, but going-in metrics are
  > tighter than the pre-LOI recast: Year 1 DSCR is 0.97x (below 1.0 and
  > below the Norfolk 1.25x lender minimum), Year 1 cash-on-cash is
  > negative (-0.6%), and project base IRR comes in at 13.47% — below
  > the 18% project target. The deal is justifiable only with (a) a 6+
  > month operating reserve to absorb the Year 1-2 negative cash flow,
  > (b) faithful execution of the renovation plan to lift NOI into the
  > Year 3+ range, and (c) tolerance for an LP equity multiple in the
  > ~1.4-2.0x range depending on which underwrite reconciliation is
  > preferred."

  > "Newmark's reduction of Marketing by 45% and Legal/Professional cuts
  > are aggressive. The Year 1 Pro Forma compounds this by also assuming
  > 5% vacancy (vs. Norfolk's 9.5% market average) and 1.5% bad debt
  > (vs. T-12 actual of 2.9% net / 9.1% gross). Do not underwrite to the
  > seller's pro forma; their NOI is materially overstated."

  > "Lease expiration cluster: 8 leases expire in Q2 2026 (Apr-Jul). 30%
  > of the rent roll turns over in 90 days. Material renewal risk during
  > ownership transition."

NOTE THE VOICE: Specific dollar deltas. Threshold-cited. Conditional. Names
the broker who proposed the cuts. Counts leases. Calculates "30% in 90
days." This is what good looks like.

STRUCTURAL SUGGESTIONS (loose — organize as serves THIS deal):
  - Headline recommendation as `> [!RECOMMENDATION] ...`
  - Property snapshot (table)
  - Key Metrics / Headline Metrics (REQUIRED) — section explicitly titled
    "Key Metrics" or "Headline Metrics" or "Summary Metrics" listing the
    headline numbers (cap rate, DSCR, CoC, IRR, equity multiple, debt
    yield, PPU) compared to Eight Rock thresholds. Validator requires this
    section literal.
  - Pricing & headline returns vs Eight Rock thresholds (table + interpretive paragraph)
  - Seller pro forma vs Eight Rock recast — call out aggressive cuts
  - Revenue analysis: rent roll detail, M2M concentration, lease cliff math, loss-to-lease
  - Expense analysis: T-12 line items, ±15% YoY callouts, line-of-business spikes
  - Comps & market context (FMR, BAH, supply pipeline, lender activity)
  - Value-add plan with the levers selected, expected lift, capex pro forma
  - Refi/Exit Test results (Beardsley 4-scenario)
  - Capital structure (raise, pref, splits, LP returns)
  - Rationale (REQUIRED) — a clearly-headed section titled "Rationale" or
    "Investment Thesis" explaining WHY this deal pencils (or doesn't). Two
    to four paragraphs walking the reasoning that backs the headline
    recommendation. Validator requires the literal word "Rationale" or
    "Thesis" to be present.
  - Risks / Key Risks (REQUIRED) — section explicitly titled "Key Risks"
    or "Risk Factors" or "Risks" listing the top 3-5 things that could
    break the deal, each with a one-sentence mitigant. Required by validator.
  - Red flags + diligence list
  - Final recommendation with explicit conditions

REQUIRED NUMERIC CITATIONS (state each in the memo, formatted $X,XXX,XXX):
  - Purchase price (briefing.deal_dials.purchase_price)
  - Loan amount (briefing.deal_dials.loan_amount)
  - Equity raise (briefing.deal_dials.equity_raise)
  - NOI in-place (briefing.deal_dials.noi_in_place)
  - Stabilized NOI (briefing.metrics.stabilized_noi)
  - Year-1 GPR (briefing.metrics.year1_gpr)
  - Year-1 debt service (briefing.metrics.annual_debt_service_year_1)
Each of these dollar figures must appear at least once in the memo text
within 5% of the briefing value. Validator flags missing figures.

VERDICT — MUST MATCH CALIBRATION:
The memo's stated GO / WATCH / NO-GO recommendation MUST equal
the CALIBRATED VERDICT line at the top of this prompt verbatim. The verdict is computed from the
current dial settings against the live calibrated thresholds — do not
editorialize, do not state a different verdict because earlier dials
returned a different value. Validator cross-checks the memo's verdict
against verdict.evaluate() at the briefing-time dial state.
"""

_BRIEF_INVESTOR_MEMO_SUMMARY = """\
=== ARTIFACT: INVESTOR MEMORANDUM — TRUST FIRST ===

AUDIENCE: A specific persona — call her "the grandmother with $10M in the
bank." She's not unsophisticated. She built or inherited real money. She
reads carefully. She's careful with whom she invests. She's seen friends
get burned by slick operators who promised returns they couldn't deliver.

She does NOT need finance terminology explained — she has an accountant for
that. What she needs is to know SHE CAN TRUST BRIAN AND EIGHT ROCK before
she writes a check. That is the entire job of this document.

VOICE: Personal. Honest. Conservative. First-person where natural ("I look
at this deal and..."). Plain English everywhere — no IRR, EM, DSCR, cap
rate, NOI as primary terms (use them only after explaining in context).
Never patronizing. Never hype. Never use exclamation points or marketing
language ("incredible opportunity!" is forbidden).

She would rather hear "this is a careful, modest investment that should
roughly double your money over 5-7 years if we execute" than "21.4% IRR
with 2.36x equity multiple."

LENGTH: 4-6 pages — long enough to build trust, short enough she'll read
the whole thing.

LEVEL OF EXPERTISE ASSUMED: Common-sense smart, not finance-trained. She
understands "your $250,000 turns into roughly $500,000 over 5 years"
better than "2.0x equity multiple." She understands "the building earns
about $200,000 a year in rent after expenses — that's what funds your
distributions and pays the mortgage" better than "NOI of $200,000."

WHAT TO LEAD WITH:
  Not the deal. Lead with WHO. The first thing she needs to know is who
  Eight Rock is, why Brian does this, and why she can trust him with her
  money. The deal comes second.

WHAT THIS DOCUMENT MUST DO (in priority order):

  1. Establish trust. Who is Brian? Why does he do this? What is his
     character? What does he NOT do? (e.g., "We do not invest in deals
     where we don't have personal capital alongside yours" — if true.
     "We do not take fees outside the structure described here" — if
     true. "If we lose your money, we lose ours alongside it" — if true.)
     Be honest about Eight Rock being a relatively new firm if it is.

  2. Explain in 2-3 sentences what Eight Rock actually does. ("We buy
     small apartment buildings in coastal Virginia where there's
     consistent military and workforce demand for housing. We fix them
     up. We collect the rent. After about 5 years, we sell them for
     more than we paid, and you get your money back plus the gains.")

  3. Show her exactly what we're buying for her — the property, what
     it looks like, who lives there, what condition it's in.

  4. Explain HOW HER MONEY GROWS — three ways:
       (a) Cash distributions from rent (typically once a year, sometimes
           less in early years while we're paying for renovations)
       (b) The mortgage gets paid down each month — that's principal
           coming back to her at sale
       (c) The building becomes worth more as we improve it

  5. Explain what we promise NOT to do — conservative guardrails:
       - Won't borrow more than ~70% of the price
       - Won't invest your money without our money alongside (if true)
       - Won't surprise you with negative news (transparent quarterly
         reports)
       - Won't hide bad news to protect appearances
       - Won't take fees outside the structure she's signing up for

  6. Be HONEST about what could go wrong:
       - Renovations might cost more than we plan
       - Rents might not rise as fast as we project
       - The economy could weaken
       - We might need to hold longer than 5 years
     For each, explain how we PROTECT HER (operating reserves, conservative
     leverage, tested value-add thesis, downside underwriting).

  7. The tax angle — IN HER LANGUAGE:
       Real estate has special tax treatment that means you keep more of
       the money. For every $1,000,000 you invest, the IRS lets us pass
       through about $30,000-$40,000 a year as a "paper loss" — even
       though the property is making real cash. That paper loss reduces
       your taxable income from this investment. When we sell, you can
       potentially roll your gains into another investment with us and
       defer the tax bill (a "1031 exchange" — your accountant will know).
       Net: real estate is one of the most tax-friendly investments in
       America, and we use that to keep more of what the property earns
       in your hands, not the IRS's.

  8. What happens next — practical:
       - How does she invest? (PPM, subscription documents)
       - When? (capital call date)
       - When does she start getting distributions?
       - When does she get her money back? (typical 5-year hold)
       - What does she get from us along the way? (quarterly reports,
         annual meeting, anytime-she-asks calls with Brian)

VOICE CALIBRATION — phrasing that fits this audience (these are TARGETS,
not quotes from prior docs):

  - "I look at this property the way I'd look at it if I were buying it
    with my own money — because I am."
  - "We do not invest your money into a deal we wouldn't put our own
    family's into."
  - "If everything goes perfectly, you should roughly double your money
    in five years. If things go badly, here's what protects you."
  - "Your money goes into one specific bank account, used to buy one
    specific property. Not a pool. Not a fund. We can show you the
    closing statement."
  - "You will hear from us every quarter, whether the news is good or
    bad. If something material happens between reports — a refi, a sale
    offer, a major capex item — we'll call you the day we know about it."

ABSOLUTELY FORBIDDEN:
  - "Industry-leading returns" / "best-in-class" / "premier opportunity"
  - "Unprecedented" / "once in a lifetime"
  - Stock photos of skyscrapers, sunsets, etc. (we render only text)
  - Multi-syllable financial jargon used without explanation
  - Hyperbole or exclamation points
  - Any claim about past returns we can't substantiate from the briefing

STRUCTURAL SUGGESTIONS (organize as serves the trust-building goal):
  - About Eight Rock & Brian (who, why, character)
  - The Deal — Property Overview (REQUIRED) — section explicitly titled
    "The Deal" or "Deal Overview" or "Property Overview" describing what
    we're buying in plain English. Validator requires this section literal.
  - Investment Thesis — Why This Deal (REQUIRED) — section explicitly
    titled "Investment Thesis" or "Why This Deal" answering "Why this
    property, why this market, why now." Two-to-three paragraphs in
    plain English. Validator requires this section literal.
  - How Your Money Grows (the 3 sources, in plain words)
  - What We Promise NOT To Do (the guardrails)
  - Use of Funds / Sources and Uses (REQUIRED) — section explicitly
    titled "Use of Funds" or "Sources and Uses" or "Capital Stack" in
    plain English. Walk her through: "Your money plus a mortgage from
    [lender type] adds up to $X,XXX,XXX. We use that to pay $X,XXX,XXX
    for the building, $XXX,XXX for closing costs, $XXX,XXX for
    renovations, and $XX,XXX held back as a safety reserve." Required
    by validator.
  - What Could Go Wrong & How We Protect You (REQUIRED — validator
    requires "what could go wrong" or "risks" or "downside" section)
  - Tax Benefits / Tax Shielding (REQUIRED — validator requires "tax
    benefit" or "tax shielding" or "depreciation" section)
  - Your Returns, Plain English (instead of "21.4% IRR" say something like
    "your $250,000 should grow to roughly $500,000-$600,000 over 5 years
    if the plan works as expected")
  - What Happens Next

REQUIRED NUMERIC CITATIONS (state each at least once, formatted $X,XXX,XXX
or as "your $250,000 invested" plain-English form when speaking to the LP):
  - Purchase price (briefing.deal_dials.purchase_price)
  - Loan amount (briefing.deal_dials.loan_amount)
  - Equity raise / total raise (briefing.deal_dials.equity_raise)
  - NOI in-place (briefing.deal_dials.noi_in_place) — in plain English:
    "the building earns about $XXX,XXX a year after expenses today"
  - Stabilized NOI (briefing.metrics.stabilized_noi) — "by Year 5 we
    expect the building to earn about $XXX,XXX a year"
  - Year-1 debt service (briefing.metrics.annual_debt_service_year_1) —
    in plain English: "the mortgage payments are about $XXX,XXX a year"
Validator checks each within 5% tolerance.

USE `> [!RECOMMENDATION]` ONCE for the investment thesis in plain English.
USE `> [!INSIGHT]` for the trust-building character points (e.g., GP
co-invest commitment, conservative leverage promise).

DO NOT use `> [!RED FLAG]` here — that's analyst-blunt language. For risks,
write them in plain English in the "What Could Go Wrong" section.
"""

_BRIEF_INVESTOR_MEMO_DETAIL = """\
=== ARTIFACT: INVESTOR MEMORANDUM — SOPHISTICATED ===

AUDIENCE: A technically-fluent LP. Possibilities: family-office principal
or analyst, accredited investor with prior CRE syndication experience, an
RIA evaluating for a client, an outside underwriter being asked to
diligence the deal. They've read PPMs before. They have a CPA on speed
dial. They want the math, the mechanics, and the protections — all walked.

VOICE: Institutional. Transparent. Comprehensive. The tone of a
confidential investment memo prepared by a deliberate, careful sponsor.
Use industry terminology naturally — IRR, EM, DSCR, cap rate, debt
yield, LTV, NOI all assumed known.

LENGTH: 15-20 pages. Substantial — this is the diligence packet.

LEVEL OF EXPERTISE ASSUMED: High. CRE-fluent. Tax-fluent (or has a tax
person). Comfortable with K-1s, partnership accounting, capital accounts,
preferred returns, and waterfall mechanics.

WHAT TO LEAD WITH: A 1-page Executive Summary that gets the sophisticated
reader to a yes/no/maybe in 90 seconds. Then back it up with 18 pages of
substance.

WHAT THIS DOCUMENT MUST COVER (every section is required when data permits):

  1. EXECUTIVE SUMMARY — Investment thesis, headline returns, recommendation,
     all in one page.

  2. TRANSACTION SNAPSHOT — table: purchase price, units, PPU, going-in &
     stabilized cap, NOI build, hold period, projected LP IRR / EM / avg
     CoC, equity raise, debt structure.

  3. SPONSOR / GP PROFILE — Who Eight Rock is, Brian's background, prior
     deals (or candid statement if this is the firm's first/early deals),
     personal capital commitment.

  4. INVESTMENT THESIS (REQUIRED — validator requires "investment thesis"
     or "strategy" as a section literal) — Why this deal? Why this market?
     What's the edge? Why does Eight Rock win here? (Hampton Roads thesis:
     military demand, workforce demographics, capital inefficiency in
     <50-unit deals.)

  5. MARKET OVERVIEW — Hampton Roads MSA + specific submarket. Supply
     pipeline, demand drivers (military bases, port, healthcare), rent
     comps, occupancy trends, employment, demographic shifts.

  6. PROPERTY DESCRIPTION — Physical attributes, vintage, lot, unit mix,
     amenities, current PM, location specifics. Quantify everything.

  7. UNDERWRITING — Going-in vs stabilized. Walk every assumption:
     vacancy, bad debt, rent growth, expense growth, exit cap, AM fee,
     stabilization period, CapEx reserves. Cite where each number comes
     from (T-12, ALN comps, market reports).

  8. SELLER PRO FORMA vs EIGHT ROCK RECAST — Line-by-line reconciliation
     when data permits. Explicitly call out aggressive cuts (e.g.,
     marketing reduced 45%, payroll annualized at non-stabilized
     run-rate). Treat this as the "what they're hiding" section.

  9. VALUE-ADD PLAN — Lever by lever. Capex per unit (interior, exterior,
     ancillary), expected lift, phasing across the hold, capex source
     (raise vs reserves vs cash flow).

  10. CAPITAL STRUCTURE & SOURCES/USES (REQUIRED — validator requires
      "capital call" or "capital plan" or "funding" as a section literal) — Tabulated:
      Sources: senior debt + LP equity + (any GP coinvest)
      Uses: purchase price + closing costs + capex + working capital
            + financing fees + acquisition fee (if any)
      Senior debt terms: lender, rate (or rate target if pre-LOI),
      LTV/LTC, DSCR covenant, IO period, amortization, prepayment.

  11. WATERFALL MECHANICS — Walk the 3-tier structure with example numbers:
      - 8% preferred return to LPs, cumulative non-compounded, calculated
        on UNRETURNED LP capital (so as ROC pays down, the pref base shrinks)
      - Return of LP capital (ROC) — when triggered, in what order
      - Residual: 70% LP / 30% GP after pref + ROC are met
      - Sale-year mechanic: combine final-year operating CF with net sale
        proceeds into a single distribution, run the full 3-tier
      - GP economics: 4% AM fee on GPR (waived in exit year) + 30% of
        residual = the GP's all-in compensation
      Walk through a simple example: $1M LP capital, 8% pref, 5-year hold,
      $300K total distributions, $1.7M sale net proceeds → who gets what.

  12. CAPITAL CALL TIMING — When the call goes out (closing date),
      tranching if any (Y1 capex draw, Y2 capex draw), payment terms
      (wire instructions, deadline, late penalties), what happens if an
      LP misses a call (dilution mechanism per LPA).

  13. DISTRIBUTION CADENCE — Frequency (typical: quarterly starting Y2
      after stabilization, annual in Y1), pro-rata to commitment, paid
      via ACH or wire, K-1s issued by [date], state filings handled by
      sponsor.

  14. RETURNS ANALYSIS — Build out the components:
      - Project IRR vs LP IRR (the ~3% delta is the AM fee + promote drag)
      - Equity multiple decomposition: how much from cash flow vs
        principal paydown vs appreciation
      - Avg LP CoC over the hold
      - Year-by-year cash flow table (5 years)
      - Pre-tax vs after-tax returns for the marginal investor (37% fed
        + state) — show how depreciation pass-through narrows the gap

  15. TAX SHIELDING & ADVANTAGES — REQUIRED SECTION. The technical reader
      cares deeply about this. Cover:
      - DEPRECIATION: 27.5-yr MACRS straight-line on the building portion.
        Roughly 80% of the basis is depreciable. So a $3M deal generates
        ~$87K/year of depreciation pass-through. For an LP investing
        $250K of $1M raise, that's ~$22K/year of paper loss against
        passive income (or against any active income if they qualify
        for real-estate-professional status, rare but possible).
      - BONUS DEPRECIATION: Under current law, accelerated depreciation
        on personal-property components (5-yr / 7-yr / 15-yr MACRS) can
        be taken in year 1 via cost-segregation study. Typical cost-seg
        on a $3M Class C deal frees ~25-35% of the basis for accelerated
        depreciation. Year 1 paper losses can run 4-6x the straight-line
        baseline. Eight Rock will engage a cost-seg firm post-close
        (typical fee $5-12K, payback period ~6 months).
      - PASSIVE LOSS RULES: Per IRC §469, passive losses can only offset
        passive income — unless the investor qualifies for REPS, in which
        case they can offset W-2 income. Suspended passive losses carry
        forward and unlock at sale.
      - 1031 EXCHANGE AT EXIT: When the property sells, an LP can elect to
        roll their pro-rata share of gain into another like-kind property,
        deferring federal cap gains tax indefinitely. Eight Rock can
        coordinate a 1031 reserve at close if multiple LPs elect. (Brian
        confirms the LP's intent ~6 months before sale to organize a QI.)
      - K-1 vs 1099: LP receives a Schedule K-1 each year (no withholding
        — partner-level taxation). Eight Rock files state returns where
        the property is located; LP may need state returns.
      - QBI DEDUCTION: Real estate LP income may qualify for the §199A
        qualified business income deduction (20% of qualifying income),
        depending on total income. CPA confirms.

  16. RISKS & MITIGANTS — Walk each:
      - Operational: turnover during transition (mitigant: 6-mo reserve,
        retain PM through Y1)
      - Capital markets: rate / cap rate compression at exit (mitigant:
        refi/exit stress test results, 5-yr term flexibility)
      - Construction / capex: cost overruns (mitigant: 10% contingency,
        fixed-price bids on major work)
      - Tenant: lease cliff (mitigant: stagger renewals, market rent
        program with concession buffer)
      - Regulatory: rent control unlikely in VA but disclose
      - Sponsor: GP key-person risk (mitigant: succession plan if any)

  17. REFI / EXIT STRESS TEST — Beardsley 4-scenario walked through.
      Pass criteria: hypothetical refi loan covers existing balance.
      Show base / op shock / cap shock / both with the implied refi
      proceeds and DSCR.

  18. DILIGENCE STATUS — What's verified, what's outstanding:
      - PSA executed: yes/no
      - Title commitment received: yes/no
      - Survey: yes/no
      - Property condition assessment: yes/no
      - Environmental: yes/no
      - Financial audit (T-12 + rent roll vs bank statements): yes/no
      - Lender LOI / term sheet: yes/no
      Be honest. If pre-LOI, say so. Don't pretend diligence is complete.

  19. LP RIGHTS & GOVERNANCE (REQUIRED — validator requires "governance"
      or "lp rights" or "rights" as a section literal) — Per LPA:
      - Reporting cadence (quarterly financials, annual audited)
      - Material events: refi, sale, major capex (>$X), key personnel
        change — sponsor must notify within N business days
      - Major-decision approvals: typically sale, refi, dilutive raises
        require LP supermajority
      - GP removal triggers: gross negligence, fraud, prolonged failure
        to distribute pref
      - Capital account tracking: book vs tax, with LPs receiving annual
        statements
      - Annual meeting (in-person or virtual)

  20. APPENDIX — Optional: assumptions table, glossary (only the firm-
      specific terms), property photos (referenced — we render text only).

VOICE CALIBRATION — phrasing that fits this audience:

  - "Crossroads is a 26-unit, two-bedroom workforce townhome community
    located on a 1.85-acre site at the intersection of Cape Henry Avenue
    and Tidewater Drive in southeast Norfolk."
  - "In-place gross potential rent of $441,564 is concentrated in three
    two-bedroom floorplans."
  - "The seller has only renovated three of eight 2/2 units (38%), leaving
    five classic 2/2 units in place with current rents averaging $1,209
    — a $341 monthly gap to the renovated rent of $1,550."
  - "Adjustments: (1) we underwrite vacancy at 7% versus the 5% physical
    vacancy currently shown on the rent roll to provide a stabilized
    cushion; (2) Eight Rock's 4% asset management fee (~$17K/yr) is
    deducted from operating cash flow during the hold and waived in the
    exit year per the LPA."

REQUIRED CALLOUTS:
  - `> [!RECOMMENDATION]` — investment thesis in 1-2 sentences (top of doc)
  - `> [!INSIGHT]` — for non-obvious analytical findings (use sparingly)
  - `> [!RED FLAG]` — for material risks the reader needs to weigh
  - `> [!DD]` — for specific diligence items where the answer matters

REQUIRED NUMERIC CITATIONS (state each at least once, formatted $X,XXX,XXX):
  - Purchase price (briefing.deal_dials.purchase_price)
  - Loan amount (briefing.deal_dials.loan_amount)
  - Equity raise (briefing.deal_dials.equity_raise)
  - NOI in-place (briefing.deal_dials.noi_in_place)
  - Stabilized NOI (briefing.metrics.stabilized_noi)
  - Year-1 GPR (briefing.metrics.year1_gpr)
  - Year-1 debt service (briefing.metrics.annual_debt_service_year_1)
Validator checks each within 5% tolerance.

VERDICT — MUST MATCH CALIBRATION:
The memo's GO / WATCH / NO-GO recommendation MUST equal the CALIBRATED VERDICT line at the top of this prompt
verbatim. Do not editorialize. Validator cross-checks the memo's verdict against
verdict.evaluate() at the briefing-time dial state.

USE TABLES LIBERALLY for: snapshot, sources/uses, T-12 vs recast,
5-year CF, sensitivity matrix, capex schedule, waterfall walk.

This document is the BACKUP for the Trust-First memo. Sophisticated LPs
will read both — the Trust First memo signals character, this one
provides the math. They must AGREE in conclusion (the trust-first version
shouldn't promise returns the sophisticated version doesn't underwrite).
"""

_BRIEF_VALUE_ADD_STRATEGY = """\
=== ARTIFACT: VALUE-ADD / CAPEX STRATEGY ===

AUDIENCE: Eight Rock's property manager (post-acquisition) plus LPs who
want to see the operational plan. Tactical readers — they want to know
WHAT we're doing in unit X by month Y, not philosophical strategy.

VOICE: Tactical. Lever-by-lever. Vintage-specific. Quotes Murray's 21-
lever framework where applicable. Names units. Names tenants. Phases
clearly.

LENGTH: 8-12 pages.

LEVEL OF EXPERTISE ASSUMED: Operational — the reader runs apartments. Use
industry terminology naturally (turn cost, RUBS, CAM, lease-up). Explain
firm conventions where helpful.

WHAT TO LEAD WITH: The renovation thesis. Then the data that proves it.

VOICE CALIBRATION — Brian's Crossroads Value-Add doc opens:

  > "Renovation Thesis"
  > "The value-add case at Crossroads is unusually well-documented because
  > the seller has already executed the thesis on nine of the twenty-six
  > units. We are not making an assumption about renovated rents — we are
  > observing them in the rent roll."
  > "Headline: the 2/2 floorplan is a dramatically better place to spend
  > renovation dollars than the other two floorplans. Every dollar deployed
  > into a 2/2 turn generates roughly twice the annual rent lift."
  > "Haywood (33.8 yrs, unit 3130-B, paying $1,418 on a $1,350 Classic
  > market rent): already $68/month ABOVE Classic market through cumulative
  > rent bumps. Renovation upside is only $144/month. Recommend: NO PUSH."

NOTE THE VOICE: Empirical — "we are observing them in the rent roll."
Cites individual tenant tenure and unit number. Names a specific decision
("NO PUSH") with the math behind it. Tactical.

STRUCTURAL SUGGESTIONS:
  - Renovation Thesis (why this property, why this strategy)
  - Phasing — Four-Phase Execution Calendar (REQUIRED). Break the program
    into explicit phases with month windows. Use literal sub-headings
    "Phase 1 (Months 0–12)", "Phase 2 (Months 12–24)", "Phase 3", "Phase 4".
    Each phase names which units turn that phase, the per-phase capex
    total in $X,XXX,XXX form, and the operational milestones (RUBS scoping,
    LED conversion, roof inspection, tax appeal, etc.) Validator requires
    the literal word "Phasing" or "Phase 1" to be present.
  - Proven Renovation Premium (table comparing in-place renovated vs
    classic rents from the rent roll, if data supports)
  - Why the Premium Is Real (the floorplan-specific rationale)
  - Unit-by-Unit Renovation Target List (table with priority rating)
  - Tenant-Retention Decisions (long-tenure tenants — push or hold)
  - CAPEX Deployment Plan — Five Years (phased pro forma)
  - Beyond Unit Renovations (other levers — exterior, ancillary income)
  - Expected NOI Trajectory (per-year impact, stabilized run-rate)
  - Operational Considerations (PM staffing, turn timing, tenant comms)

Use tables for: rent comparison, target list with priority stars,
capex schedule by year, expected NOI build-up.
"""

_BRIEF_LOI = """\
=== ARTIFACT: LETTER OF INTENT ===

AUDIENCE: The seller's broker (or seller directly). They'll forward it to
the seller. The reader is a CRE professional who reviews offers daily.

VOICE: Legal-formal. Clean. Professional. Standard CRE letter format.
First-person plural ("Eight Rock Capital Partners, LLC ('Buyer') is pleased
to submit..."). Polite but firm on terms.

LENGTH: 2 pages.

LEVEL OF EXPERTISE ASSUMED: High — CRE professional. Don't over-explain.

WHAT TO LEAD WITH: A polite opener identifying the buyer + the property,
followed by the principal terms.

VOICE CALIBRATION — Brian's actual Crossroads LOI:

  > "LETTER OF INTENT (LOI)"
  > "May 8th, 2026"
  > "Via Email"
  > "Victoria Pickett, Executive Managing Director, Newmark"
  > "Re: Letter of Intent to Purchase — Crossroads Townhomes"
  > "Dear Ms. Pickett,"
  > "Eight Rock Capital Partners, LLC ('Buyer') is pleased to submit this
  > non-binding Letter of Intent to acquire Crossroads Townhomes
  > ('Property'), a 26-unit townhome community located at 3000 South Cape
  > Henry Avenue..."
  > "PRINCIPAL TERMS"
  > "[ ... terms table ... ]"
  > "NON-BINDING NATURE: This Letter of Intent is intended solely to set
  > forth the general terms upon which the parties would negotiate a
  > definitive Purchase and Sale Agreement..."
  > "EXPIRATION: This Letter of Intent shall expire if not accepted by
  > Seller within five (5) business days..."
  > "We are enthusiastic about the opportunity..."
  > "Respectfully submitted,"

NOTE THE VOICE: Standard CRE LOI structure. Date, recipient address, Re:
line, salutation, body, principal terms (purchase price, EMD, DD period,
financing contingency, closing date), non-binding clause, expiration,
respectful closing.

REQUIRED SECTIONS:
  - Date / Via Email
  - Recipient block (broker name, title, firm, address — if briefing has
    it; otherwise leave a placeholder like "[BROKER NAME] — to be inserted")
  - Re: line
  - Salutation
  - Opening paragraph
  - PRINCIPAL TERMS (numbered list — purchase price, deposit/EMD, due
    diligence period, financing contingency, closing date, transfer of
    leases/contracts, prorations, broker commission)
  - NON-BINDING NATURE clause
  - CONFIDENTIALITY clause
  - EXPIRATION clause
  - Closing paragraph
  - Signature block

This is the most templated of the artifacts. Stay close to the structure
above. If briefing data is missing for a term (e.g. EMD amount), use a
sensible Eight Rock default and FLAG it as `[CONFIRM]` in the document.
"""


_BRIEF_BY_TYPE: dict[str, str] = {
    "executive_summary": _BRIEF_EXECUTIVE_SUMMARY,
    "investor_memo_summary": _BRIEF_INVESTOR_MEMO_SUMMARY,
    "investor_memo_detail": _BRIEF_INVESTOR_MEMO_DETAIL,
    "value_add_strategy": _BRIEF_VALUE_ADD_STRATEGY,
    "loi": _BRIEF_LOI,
}


# ---------------------------------------------------------------------------
# User-prompt builder — assembles brief + briefing JSON
# ---------------------------------------------------------------------------

def prompt_for_artifact(
    *,
    artifact_type: str,
    briefing: dict[str, Any],
    schema: str = "",  # legacy arg, ignored — kept for backwards-compat
) -> str:
    """Assemble the user message: writing brief + briefing JSON.

    The calibrated verdict is hoisted out of the JSON and pinned at the top
    of the prompt as a literal — the LLM was previously inventing GO/WATCH/NO-GO
    by inferring from raw metrics, ignoring the verdict block buried in the
    JSON. The pinned line makes the answer unmissable, and the prompts now
    instruct the LLM to quote it verbatim.
    """
    brief = _BRIEF_BY_TYPE.get(
        artifact_type,
        f"=== ARTIFACT: {artifact_type.upper()} ===\n\nGenerate the requested document.",
    )
    briefing_json = json.dumps(briefing, indent=2, default=str)

    verdict_block = ""
    verdict_obj = briefing.get("verdict")
    if isinstance(verdict_obj, dict) and verdict_obj.get("verdict"):
        v = verdict_obj["verdict"]
        rationale = "; ".join(verdict_obj.get("rationale") or [])
        verdict_block = (
            f"=== CALIBRATED VERDICT (THIS DEAL, AT CURRENT DIALS, RIGHT NOW) ===\n"
            f"\n"
            f"  ★  VERDICT:  {v}\n"
            f"  ★  RATIONALE:  {rationale}\n"
            f"\n"
            f"When the artifact states or implies a GO / WATCH / NO-GO recommendation,\n"
            f"it MUST state '{v}' verbatim. Do not editorialize, do not infer a different\n"
            f"verdict from the raw metrics — the calibration system above is the source of\n"
            f"truth, and the IC Memo Validator will block the artifact if the stated\n"
            f"verdict disagrees with this line.\n"
            f"\n"
        )

    return f"""\
{verdict_block}{brief}

=== DEAL BRIEFING (JSON — every data point the workbench has on this deal) ===

```json
{briefing_json}
```

=== END BRIEFING ===

Now write the document. Output GitHub-flavored Markdown only. No preamble.
The first character of your response must be `#` or `>`.
"""


def schema_for_artifact(artifact_type: str) -> str:
    """Backwards-compat shim. The new architecture doesn't use a schema —
    the LLM produces markdown freely. Returns empty string."""
    return ""
