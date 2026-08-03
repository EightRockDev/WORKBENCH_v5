**EIGHT ROCK CAPITAL PARTNERS**

**Eight Rock Workbench v5.0**

Competitive Analysis & Technical Specification

*Skip Trace & POC Intelligence · Compliant Outreach · Full-Loop Acquisition Platform*

Prepared for Brian McCune · July 21, 2026 (rev. 5 — adds Section 10 (Multi-Tenancy & Role Model) and Section 11 (LLM-Optional Architecture, Crawl-Walk-Run))

Baseline build: v2.4.1 · Confidential — internal strategy document

**Contents**

[**1. Executive Summary** [4](#executive-summary)](#executive-summary)

[**2. Competitive Landscape — Top 10 Platform Teardown** [5](#competitive-landscape-top-10-platform-teardown)](#competitive-landscape-top-10-platform-teardown)

[**2.1 Yardi (Matrix · Voyager · Breeze · Virtuoso AI)** [5](#yardi-matrix-voyager-breeze-virtuoso-ai)](#yardi-matrix-voyager-breeze-virtuoso-ai)

[**2.2 CoStar Group (CoStar Suite · LoopNet · Apartments.com)** [5](#costar-group-costar-suite-loopnet-apartments.com)](#costar-group-costar-suite-loopnet-apartments.com)

[**2.3 AppFolio (Property Manager · Stack · Realm-X)** [5](#appfolio-property-manager-stack-realm-x)](#appfolio-property-manager-stack-realm-x)

[**2.4 RealPage (OneSite · Lumina AI · Buildium · Cherre)** [5](#realpage-onesite-lumina-ai-buildium-cherre)](#realpage-onesite-lumina-ai-buildium-cherre)

[**2.5 Reonomy (Altus Group)** [6](#reonomy-altus-group)](#reonomy-altus-group)

[**2.6 Terrakotta AI (YC W24)** [6](#terrakotta-ai-yc-w24)](#terrakotta-ai-yc-w24)

[**2.7 EliseAI** [6](#eliseai)](#eliseai)

[**2.8 Radix / redIQ (merged Aug 2024)** [6](#radix-rediq-merged-aug-2024)](#radix-rediq-merged-aug-2024)

[**2.9 Primer (PropRise — YC/General Catalyst-backed)** [7](#primer-proprise-ycgeneral-catalyst-backed)](#primer-proprise-ycgeneral-catalyst-backed)

[**2.10 Dealpath (Blackstone-backed)** [7](#dealpath-blackstone-backed)](#dealpath-blackstone-backed)

[**2.11 Watch list (second tier)** [7](#watch-list-second-tier)](#watch-list-second-tier)

[**3. How the Top 10 Stack Up Against the Workbench** [8](#how-the-top-10-stack-up-against-the-workbench)](#how-the-top-10-stack-up-against-the-workbench)

[**4. Module A — Skip Trace & POC Intelligence Engine (new, flagship)** [9](#module-a-skip-trace-poc-intelligence-engine-new-flagship)](#module-a-skip-trace-poc-intelligence-engine-new-flagship)

[**4.1 Functional requirements** [9](#functional-requirements)](#functional-requirements)

[**4.2 Resolution pipeline (stages, all idempotent and resumable)** [9](#resolution-pipeline-stages-all-idempotent-and-resumable)](#resolution-pipeline-stages-all-idempotent-and-resumable)

[**4.3 Unit economics** [10](#unit-economics)](#unit-economics)

[**4.4 Compliance gate (built-in — no competitor ships this)** [10](#compliance-gate-built-in-no-competitor-ships-this)](#compliance-gate-built-in-no-competitor-ships-this)

[**4.5 Data contract (poc_record, abridged)** [11](#data-contract-poc_record-abridged)](#data-contract-poc_record-abridged)

[**4.6 Acceptance criteria** [11](#acceptance-criteria)](#acceptance-criteria)

[**5. Module B — Compliant Outreach Engine (new)** [13](#module-b-compliant-outreach-engine-new)](#module-b-compliant-outreach-engine-new)

[**5.1 Capabilities** [13](#capabilities)](#capabilities)

[**5.2 Acceptance criteria** [13](#acceptance-criteria-1)](#acceptance-criteria-1)

[**7. Phase 0 — Current Data-Set Analysis & ALN De-Identification** [14](#phase-0-current-data-set-analysis-aln-de-identification)](#phase-0-current-data-set-analysis-aln-de-identification)

[**7.1 Current data-set inventory (read-only scan of v2.4.1, July 21, 2026)** [14](#current-data-set-inventory-read-only-scan-of-v2.4.1-july-21-2026)](#current-data-set-inventory-read-only-scan-of-v2.4.1-july-21-2026)

[**7.2 Replacement taxonomy (Eight Rock native)** [15](#replacement-taxonomy-eight-rock-native)](#replacement-taxonomy-eight-rock-native)

[**7.3 Build-then-cutover plan** [15](#build-then-cutover-plan)](#build-then-cutover-plan)

[**7.4 Acceptance criteria — "not discernible" defined** [15](#acceptance-criteria-not-discernible-defined)](#acceptance-criteria-not-discernible-defined)

[**6. Modules C–G — Completing the Loop** [17](#modules-cg-completing-the-loop)](#modules-cg-completing-the-loop)

[**6.1 Module C — Forced-Seller Radar v2 + GRANITE Tabs 2–5** [17](#module-c-forced-seller-radar-v2-granite-tabs-25)](#module-c-forced-seller-radar-v2-granite-tabs-25)

[**6.2 Module D — Inbox → Deal Engine** [17](#module-d-inbox-deal-engine)](#module-d-inbox-deal-engine)

[**6.3 Module E — Document AI & Underwriting Engine hardening** [17](#module-e-document-ai-underwriting-engine-hardening)](#module-e-document-ai-underwriting-engine-hardening)

[**6.4 Module F — Data Independence (executes the Phase 0 spine, Section 7)** [17](#module-f-data-independence-executes-the-phase-0-spine-section-7)](#module-f-data-independence-executes-the-phase-0-spine-section-7)

[**6.5 Module G — Platform & Commercialization Infrastructure** [18](#module-g-platform-commercialization-infrastructure)](#module-g-platform-commercialization-infrastructure)

[**8. System Architecture (v5.0 target)** [19](#system-architecture-v5.0-target)](#system-architecture-v5.0-target)

[**8.1 Security Requirements — Fifth Dimension parity benchmark (adopted July 21, 2026)** [19](#security-requirements-fifth-dimension-parity-benchmark-adopted-july-21-2026)](#security-requirements-fifth-dimension-parity-benchmark-adopted-july-21-2026)

[**9. Pilot Deployment, Multi-User Operation & Authentication** [21](#pilot-deployment-multi-user-operation-authentication)](#pilot-deployment-multi-user-operation-authentication)

[**9.1 Deployment reference — dedicated office server** [21](#deployment-reference-dedicated-office-server)](#deployment-reference-dedicated-office-server)

[**9.2 File storage & the OneDrive rule (read this first)** [21](#file-storage-the-onedrive-rule-read-this-first)](#file-storage-the-onedrive-rule-read-this-first)

[**9.3 Multi-user concurrency control (the fix for simultaneous edits)** [22](#multi-user-concurrency-control-the-fix-for-simultaneous-edits)](#multi-user-concurrency-control-the-fix-for-simultaneous-edits)

[**9.4 Authentication & user administration** [22](#authentication-user-administration)](#authentication-user-administration)

[**10. Multi-Tenancy, Organizations & the Role Model** [24](#multi-tenancy-organizations-the-role-model)](#multi-tenancy-organizations-the-role-model)

[**10.1 Tenancy model — shared reference layer, org-private deals** [24](#tenancy-model-shared-reference-layer-org-private-deals)](#tenancy-model-shared-reference-layer-org-private-deals)

[**10.2 Organization & user taxonomy (data model)** [24](#organization-user-taxonomy-data-model)](#organization-user-taxonomy-data-model)

[**10.3 The role-preset library (why setup is point-and-click)** [24](#the-role-preset-library-why-setup-is-point-and-click)](#the-role-preset-library-why-setup-is-point-and-click)

[**10.4 Permission model & the three explicit answers** [25](#permission-model-the-three-explicit-answers)](#permission-model-the-three-explicit-answers)

[**10.5 Admin & lifecycle — simple to set up, nothing lost when people leave** [26](#admin-lifecycle-simple-to-set-up-nothing-lost-when-people-leave)](#admin-lifecycle-simple-to-set-up-nothing-lost-when-people-leave)

[**10.6 Acceptance criteria** [26](#acceptance-criteria-2)](#acceptance-criteria-2)

[**11. Deterministic Core vs. AI-Augmented Layer (LLM-Optional Architecture)** [28](#deterministic-core-vs.-ai-augmented-layer-llm-optional-architecture)](#deterministic-core-vs.-ai-augmented-layer-llm-optional-architecture)

[**11.1 Module classification** [28](#module-classification)](#module-classification)

[**11.2 The boundary — how AI plugs in without the core depending on it** [28](#the-boundary-how-ai-plugs-in-without-the-core-depending-on-it)](#the-boundary-how-ai-plugs-in-without-the-core-depending-on-it)

[**11.3 Crawl · Walk · Run — the build and marketing spine** [28](#crawl-walk-run-the-build-and-marketing-spine)](#crawl-walk-run-the-build-and-marketing-spine)

[**11.4 Marketing positioning — the category claim** [29](#marketing-positioning-the-category-claim)](#marketing-positioning-the-category-claim)

[**11.5 Acceptance criteria** [29](#acceptance-criteria-3)](#acceptance-criteria-3)

[**12. Pricing & Positioning** [30](#pricing-positioning)](#pricing-positioning)

[**13. Build Sequence & Verification** [31](#build-sequence-verification)](#build-sequence-verification)

[**Appendix A — Source Notes** [32](#appendix-a-source-notes)](#appendix-a-source-notes)

# **1. Executive Summary**

This document specifies Eight Rock Workbench v5.0 — the release that converts a working internal underwriting platform (v2.4.1: Python/Streamlit/SQLite, 40+ modules, document AI, GRANITE loan radar, 7-city municipal sale-history engine, one-click IC-ready Excel export) into a full-loop acquisition system that out-executes every commercial platform serving small and mid-size multifamily investors. It is written to be executed by AI engineering agents: every module carries data contracts, pipeline stages, vendor selections with unit economics, and acceptance criteria.

The July 2026 competitive analysis that grounds this spec covered ten platforms in depth (Yardi, CoStar/LoopNet, AppFolio, RealPage + Buildium + Cherre, Reonomy, Terrakotta AI, EliseAI, Radix/redIQ, Primer, Dealpath) plus the second tier (HelloData/Grace Hill, Archer, Keyway, Blooma, Fifth Dimension, PropStream, DealMachine). Three structural findings drive the v5.0 design:

- **Nobody owns the full loop.** Every competitor owns one or two slices — sourcing/ownership (Reonomy, Terrakotta), rent comps (HelloData, Radix), doc extraction into Excel (Primer, redIQ, Keyway), pipeline (Dealpath), management ops (Yardi, AppFolio, RealPage). No product connects find → pierce → contact → underwrite → close → manage. The Workbench already owns the middle of that loop; v5.0 closes both ends.

- **The 10–49 unit deal space is a data desert.** Yardi Matrix tracks only 50+ unit properties — confirmed directly by Yardi’s own account executive when Eight Rock’s 26-unit Crossroads Townhomes deal fell below the Matrix floor. CoStar’s ~\$40K/yr median suite price is uneconomic for occasional small-deal buyers. This is precisely Eight Rock’s hunting ground, and it is structurally unserved.

- **The AI arms race is ops-facing, not acquisitions-facing.** AppFolio Realm-X, RealPage Lumina (with OpenAI), and Yardi Virtuoso all ship agents for leasing, maintenance, and AP — none do underwriting, comp selection, LLC piercing, or deal analysis. The acquisitions-side AI field is small, young, and quote-gated (Primer, Archer, Keyway), leaving the transparent-price SMB segment open.

The headline addition in v5.0 is the Skip Trace & POC Intelligence Engine (Module A): full-stack contact resolution — LLC piercing to the true decision-maker, cell/landline/email/mailing-address waterfall, relatives and associates, and a built-in TCPA/DNC compliance gate — natively wired into the sale-history engine, GRANITE distress radar, and a new compliant Outreach Engine. This single module replaces a Reonomy seat (\$400–\$500/mo), a Terrakotta seat (\$149–\$249/mo), and a batch skip-trace vendor, while adding compliance machinery none of them ship.

Positioning: the integrated middle of the market — live data + auto-parse + underwrite + comps + sale history + owner contact + outreach — below \$5K/yr remains essentially empty. v5.0 is specified to occupy it at \$99–\$1,250/mo tiering with a metered data layer, per the pricing model established in the June 2026 strategy brief and validated against July 2026 competitor prices in Section 8.

# **2. Competitive Landscape — Top 10 Platform Teardown**

Findings as of July 20, 2026. Pricing marked (verified) comes from primary sources — vendor emails received by Eight Rock, published price pages, or court/press filings; other figures are best-available third-party estimates. Full source list in Appendix A.

## **2.1 Yardi (Matrix · Voyager · Breeze · Virtuoso AI)**

- **What it is:** The multifamily incumbent stack — Breeze/Breeze Premier PM software (\$1–\$3/unit/mo, \$100–\$400/mo minimums), Voyager enterprise ERP (quote-only), Matrix market data (quote-only; tracks ~22M units, ownership with LLC transparency, all-loan-type debt data), and the June 2026 Virtuoso AI agent suite (Chat IQ, inspection agents, Smart AP).

- **Strengths:** Deepest institutional data moat in multifamily; loan maturity data across all loan types; verified rent comps; senior-level ownership contacts.

- **Weaknesses vs. Workbench:** Matrix hard floor at 50+ units — Eight Rock’s own 26-unit deal was invisible to it (confirmed by Yardi AE, July 2026). Quote-gated pricing, dated Voyager UI, no underwriting workbench, no skip tracing (office-level contacts only, no cell/email waterfall), AI is ops-only.

## **2.2 CoStar Group (CoStar Suite · LoopNet · Apartments.com)**

- **What it is:** The CRE data monopoly — 8.5M property records, verified sale/lease comps, true-owner research, tenant and loan data. Median verified contract ~\$40K/yr for Suite (all markets, 1 license); low-end deals \$3K–\$24K/yr; firm-wide subscription mandates.

- **Strengths:** Four decades of human-verified comp research; true-owner entity resolution; unmatched breadth.

- **Weaknesses vs. Workbench:** Facing an April 2026 antitrust class action alleging ~80% listings monopoly and coerced subscriptions. No underwriting model, no skip-trace waterfall (research-desk contacts, no API), pricing catastrophic for a 1–3 person shop, per-market licensing punishes multi-market small buyers. Thin on Class-C sale records that municipal deed chains capture — the Workbench’s 7-city transfer-chain engine reaches deeper in Hampton Roads.

## **2.3 AppFolio (Property Manager · Stack · Realm-X)**

- **What it is:** SMB-to-mid PM platform: Core \$1.40/unit/mo, Plus \$3.00, Max \$5.00; 50-unit and \$280/mo minimums; Realm-X AI (Assistant, Messages, Flows, and 2025 "Performers" — autonomous leasing/maintenance agents).

- **Strengths:** Best-in-class PM UX; genuinely shipped agentic AI for ops; strong integration marketplace (Stack).

- **Weaknesses vs. Workbench:** Zero acquisition capability: no market data, no comps, no ownership records, no underwriting, no skip tracing. Real first-year cost runs 3–5x sticker after fees (\$16.6K–\$26K for a 200-unit book). Support decay is the top complaint theme. It manages what you already bought; it cannot help you buy.

## **2.4 RealPage (OneSite · Lumina AI · Buildium · Cherre)**

- **What it is:** Institutional multifamily ERP + revenue management + the Lumina AI workforce (co-developed with OpenAI); Buildium (\$62–\$400/mo published tiers) covers SMB; acquired Cherre (July 14, 2026) for entity-resolved data infrastructure.

- **Strengths:** Largest lease-transaction dataset in multifamily; five shipped AI agents; Cherre adds a 4B-entity identity graph.

- **Weaknesses vs. Workbench:** Operating under a DOJ consent decree (Nov 2025) with a court-appointed monitor: pricing models restricted to 12-month-old data and state-level geography — a structural handicap on its data edge. Uneconomic below ~500 units; Buildium’s AI is the weakest of the majors; no acquisitions or skip-trace capability anywhere in the stack; serious reputational overhang.

## **2.5 Reonomy (Altus Group)**

- **What it is:** CRE property intelligence: ~50M properties, 200+ filters, CMBS/debt data, ML "likely to sell" scores, and True Owner LLC piercing with contact info. Pricing (verified from vendor email to Eight Rock, July 18, 2026): \$500/mo monthly or \$400/mo billed annually; +\$75/mo for 1K exports; API and bulk feeds quote-based.

- **Strengths:** The benchmark for entity resolution + contact data; self-serve pricing; portfolio ("same owner") rollups.

- **Weaknesses vs. Workbench:** Accuracy degrades exactly where Eight Rock hunts — smaller properties and secondary markets; sales comps weak, lease comps absent; ~80% aspirational owner-match accuracy; no underwriting, no T-12/rent-roll ingestion, no outreach, no compliance tooling; \$4,800–\$6,000/yr/seat for one slice of the loop.

## **2.6 Terrakotta AI (YC W24)**

- **What it is:** AI CRE prospecting: power dialer (~100 contacts/hr) with voice-cloned AI voicemail drops, AI skip tracer with LLC relationship mapping, phone grading (A+/A/F), PropGPT natural-language property search, Chrome extension, CRM sync. \$149–\$249/user/mo with 1,000 monthly research credits (roll over). Eight Rock has an active trial (signup July 17, 2026).

- **Strengths:** Fastest outreach workflow in CRE; cheap; transparent pricing; phone verification API (\<2s/number, 1K bulk).

- **Weaknesses vs. Workbench:** Pure top-of-funnel: no underwriting, no T-12/rent-roll/OM ingestion, no comps, no municipal records, no proprietary property data (public-record cross-referencing only). ~5-person team = continuity risk. Voice-cloned voicemail drops to cells sit in TCPA gray-to-hot water (prerecorded-voice rules) and Terrakotta pushes that compliance burden onto the customer. Credits model caps heavy research.

## **2.7 EliseAI**

- **What it is:** Category-leading conversational AI for leasing/resident ops (LeaseAI, ResidentAI, VoiceAI, CRM). \$250M Series E led by a16z (Aug 2025); ARR \>\$100M; 75% of NMHC Top-50 are customers. Quote-based, ~\$3–\$8/unit/mo benchmark with \$2K–\$10K setup.

- **Strengths:** Best-executed AI in property operations; enormous conversation dataset; enterprise trust.

- **Weaknesses vs. Workbench:** Entirely ops-side — nothing for sourcing, underwriting, or acquisitions. Enterprise sales motion and per-unit economics don’t pencil below a few hundred units. Not a competitor for the acquisition wallet; listed because it defines the AI quality bar and takes budget share from the same customer.

## **2.8 Radix / redIQ (merged Aug 2024)**

- **What it is:** redIQ (now Radix’s transaction suite): rent-roll + operating-statement extraction (200K+ docs/yr), valuationIQ underwriting model, QuickSync Excel plugin; Radix layer adds daily multifamily survey data (rent, occupancy, traffic, concessions). Quote-based, historically ~\$10K–\$25K+/yr team subscriptions.

- **Strengths:** The reference standard for doc standardization at institutional scale; "analysis in 5 minutes" reputation; survey data piped into the underwriting context.

- **Weaknesses vs. Workbench:** Institutional pricing and sales motion; survey data thin outside larger metros; no sourcing, no ownership/skip data, no municipal/tax records, no outreach. Extraction pushes into their model or a plugin — not into the customer’s own template with formulas preserved, which is the Workbench’s (and Primer’s) pattern.

## **2.9 Primer (PropRise — YC/General Catalyst-backed)**

- **What it is:** The closest AI-native analog: agents ingest deal rooms/broker materials/county records, reconcile conflicts, and populate the customer’s own Excel model preserving templates/macros/formulas, with deterministic validation rules and low-confidence flagging. 40+ firms / \$50B+ AUM claimed; ~1-week onboarding; quote-based pricing.

- **Strengths:** Validated the "preserve my Excel" pattern the Workbench pioneered internally; credible QA architecture (deterministic validation + confidence flags); firm-memory concept.

- **Weaknesses vs. Workbench:** No proprietary market/rent/ownership data — pure ingestion; no sourcing radar, no skip tracing, no outreach, no municipal records; quote-gated; young. Beats the Workbench today on polish of extraction QA; loses on everything surrounding it.

## **2.10 Dealpath (Blackstone-backed)**

- **What it is:** The institutional deal-pipeline system of record; 2025 added Dealpath AI (OM auto-screening into pipeline) and AI Studio (configurable AI workflows). Quote-based; ~5-user minimum; 6–8 week white-glove implementation.

- **Strengths:** Workflow/approval discipline at institutional scale; blue-chip trust; AI screening layer now shipping.

- **Weaknesses vs. Workbench:** A 5-seat minimum and multi-week implementation make it a non-starter for 1–3 person shops. It manages deal flow but owns no data: no comps, no ownership, no underwriting model, no skip tracing.

## **2.11 Watch list (second tier)**

HelloData (acquired by Grace Hill, May 2025 — 35M-unit public-listing rent comps, \$0.10/call API; roadmap now serves PM training customers). Archer (AI multifamily underwriting + BOVs, quote-based, institutional lean). Keyway (pivoted to T-12/rent-roll analyzers for institutional finance teams). Blooma (lender-side origination AI). Fifth Dimension (\$26M Series A, May 2026 — institutional AI underwriting/IC memos; the biggest new threat, but aimed at BXP-scale clients). PropStream (\$99–\$699/mo) and DealMachine (\$119–\$279/mo, free unlimited skip tracing gated by export caps) — residential-investor tools that validate SMB willingness to pay for owner-contact data but carry no multifamily underwriting depth.

# **3. How the Top 10 Stack Up Against the Workbench**

Scoring the full acquisition loop. ● = shipped/strong, ◐ = partial/weak, — = absent. Workbench column reflects v2.4.1 today plus (v5) where this spec adds the capability.

| **Capability**                                       | **8RW v2.4.1→v5** | **Yardi**        | **CoStar** | **AppFolio**     | **RealPage**     | **Reonomy**  | **Terrakotta** | **EliseAI** | **Radix/redIQ** | **Primer** | **Dealpath**   |
|------------------------------------------------------|-------------------|------------------|------------|------------------|------------------|--------------|----------------|-------------|-----------------|------------|----------------|
| Off-market sourcing radar (distress/loan maturity)   | ● GRANITE         | ◐ Matrix         | ◐          | —                | —                | ◐ scores     | ◐ filters      | —           | —               | —          | —              |
| Municipal deed/sale-history chains (Class-C depth)   | ● 7 cities        | —                | ◐          | —                | —                | ◐            | —              | —           | —               | ◐          | —              |
| LLC piercing → true owner                            | (v5) ●            | ◐                | ●          | —                | —                | ●            | ◐              | —           | —               | —          | —              |
| Skip trace: cell/email/mail waterfall + relatives    | (v5) ●            | —                | —          | —                | —                | ◐            | ◐              | —           | —               | —          | —              |
| TCPA/DNC/litigator compliance gate                   | (v5) ●            | —                | —          | —                | —                | —            | —              | —           | —               | —          | —              |
| Compliant outreach (dial/VM/mail/email)              | (v5) ●            | —                | —          | —                | —                | —            | ● dialer       | ◐ leasing   | —               | —          | —              |
| OM/T-12/rent-roll AI extraction                      | ● shipped         | —                | —          | —                | —                | —            | —              | —           | ●               | ●          | ◐              |
| Preserve-your-own-Excel export                       | ● shipped         | —                | —          | —                | —                | —            | —              | —           | ◐ plugin        | ●          | —              |
| Full underwriting engine (IRR/waterfall/sensitivity) | ● shipped         | —                | —          | —                | —                | —            | —              | —           | ● theirs        | ◐          | —              |
| Codified buy-box GO/WATCH/NO-GO verdict              | ● shipped         | —                | —          | —                | —                | —            | —              | —           | —               | —          | —              |
| Rent comps \<50-unit properties                      | ◐→● (v5)          | — 50+ floor      | ◐          | —                | ◐                | ◐            | —              | —           | ◐               | —          | —              |
| Deal pipeline / CRM                                  | (v5) ●            | ◐                | —          | ◐                | ◐                | ◐            | ● light        | ● leasing   | ◐               | ◐          | ●              |
| LP portal + auto reporting                           | (v5) ◐            | ●                | —          | ●                | ●                | —            | —              | —           | —               | —          | ◐              |
| Property management ops                              | — (non-goal)      | ●                | —          | ●                | ●                | —            | —              | ● AI        | —               | —          | —              |
| Transparent published pricing                        | ● target          | ◐ Breeze         | —          | ◐                | ◐ Buildium       | ●            | ●              | —           | —               | —          | —              |
| Entry price for a 2-seat small shop (annual)         | \$99–399/mo       | \$100/mo PM only | ~\$40K/yr  | \$280/mo PM only | \$62+/mo PM only | \$400–500/mo | \$149–249/seat | quote       | ~\$10K+/yr      | quote      | quote, 5 seats |

**Bottom line:** the Workbench already leads the field outright on five rows (buy-box verdict, template-true export, municipal deed chains, integrated underwriting, distress radar on free data). v5.0 takes the four rows where Reonomy/Terrakotta/CoStar currently win — LLC piercing, contact waterfall, outreach, pipeline — and adds the one row nobody ships at all: a built-in compliance gate. After v5.0, no platform at any price matches the full-loop column, and the nearest paid assembly of point tools (Reonomy + Terrakotta + redIQ + Dealpath) costs \$20K–\$40K+/yr across four logins.

# **4. Module A — Skip Trace & POC Intelligence Engine (new, flagship)**

Purpose: for any property in the system — an ALN/self-sourced record, a GRANITE distress hit, a municipal deed-chain result, or a manually entered address — resolve every relevant point of contact (owner, principal behind the LLC, property manager, lender, prior investors) to verified, compliance-scrubbed contact data, and keep it fresh. This module is the direct answer to the \#1 capability gap identified in the June 2026 strategy brief ("pierce the LLC — the \#1 sourcing feature buyers expect") and the centerpiece of v5.0.

## **4.1 Functional requirements**

- FR-A1 One-click "Resolve Contacts" on any property record returns a ranked POC list within 60 seconds: true owner (individual), controlling entity chain, property manager of record, registered agent, lender contact (from GRANITE loan match), and prior owners (from deed chain).

- FR-A2 Full-stack contact payload per person: mobile phones (graded), landlines, emails (deliverability-scored), current + prior mailing addresses, relatives/associates (for hard-to-reach owners), other properties owned (portfolio rollup), age band, deceased flag.

- FR-A3 LLC piercing runs automatically: entity → state registry → officers/members/registered agent → person skip trace → portfolio chaining via shared tax-mailing addresses. Multi-layer entities (LLC owned by LLC) recurse to depth 4 with a confidence score at each hop.

- FR-A4 Every phone/email is stamped with a compliance state (see 4.4) before it is displayed as callable. Numbers failing the gate render with a red lock and the reason; the dialer and export functions refuse them.

- FR-A5 Batch mode: trace up to 5,000 properties per run (GRANITE watchlist sweeps), with per-record cost preview before execution and a hard monthly budget cap per tenant.

- FR-A6 Freshness: contact records carry retrieved_at and re-verify on use if older than 90 days (phone) / 180 days (email/address). DNC scrub status expires at 31 days per FTC rule and re-scrubs automatically on campaign start.

- FR-A7 Provenance: every field stores its source vendor, query id, and cost — extending the Workbench’s existing source-provenance color-coding convention (T-12 \> rent roll \> ALN) to people data.

## **4.2 Resolution pipeline (stages, all idempotent and resumable)**

- S1 ENTITY ANCHOR — input: parcel/APN or address. Pull owner-of-record + tax mailing address from assessor data (already ingested for 7 Hampton Roads cities + Charlottesville; extend per Module F). Output: owner_entity, mailing_address.

- S2 PORTFOLIO CHAIN — group all parcels sharing the tax mailing address or exact entity name → portfolio_id. This free step alone frequently identifies the principal (the mailing address is often the owner’s home or their agent).

- S3 ENTITY RESOLUTION — if owner is an entity: query state corporate registry for officers/members/registered agent. Vendors: Virginia SCC Clerk’s Information System (free API/scrape, primary for home market); Cobalt Intelligence SOS API (all 50 states, ~\$0.50–\$2.00/lookup) or OpenSOSData (\$0.03–\$0.10 live, \$0.003 cached) for expansion markets; OpenCorporates for historical filings. Supplements: SEC EDGAR full-text (Form D syndication filings name managers — free), UCC filings (debtor/secured-party pairs), and signatory names on recorded deed instruments already captured by the sale-history engine.

- S4 PERSON SKIP TRACE — waterfall (cheapest first, stop on grade-A match): Tier 1 Datazapp bulk append (\$0.01–\$0.03/record) → Tier 2 BatchData Skip Tracing API (\$0.07–\$0.18/record pay-per-match; 75–85% phone, 60–70% email hit rates; returns DNC flags, phone type/confidence, LLC/trust handling natively) → Tier 3 Enformion/Endato person API (from \$0.25/match self-serve, to ~\$0.01 at Pro volume; relatives/associates, court records). Tier 4 (deferred, credentialed): TLOxp or idiCORE for high-value targets — requires permissible-purpose credentialing and (TLO) site inspection; not resellable in SaaS; gate behind an Eight-Rock-internal-only flag.

- S5 VALIDATION & GRADING — every candidate phone through Trestle: Phone Validation \$0.015/query (line type, active status, prepaid/carrier), Real Contact \$0.03 (name-to-number match grade), litigator check add-on +\$0.005. Grade A = active, line-type known, name-match ≥0.8, no litigator flag. Emails through deliverability check (+\$0.005). Grades A/B/F drive UI and dialer eligibility — this mirrors and exceeds Terrakotta’s A+/A/F phone grading.

- S6 COMPLIANCE STAMP — see 4.4. Runs before any contact is marked usable.

- S7 PERSIST & MONITOR — write poc_records with provenance; subscribe portfolio_id to change detection (new deed recordation, new UCC, listing appearance/removal, loan maturity window from GRANITE) so contact intel resurfaces as a sourcing signal.

## **4.3 Unit economics**

<table>
<colgroup>
<col style="width: 20%" />
<col style="width: 22%" />
<col style="width: 16%" />
<col style="width: 18%" />
<col style="width: 23%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Stage</strong></th>
<th><strong>Vendor</strong></th>
<th><strong>Unit cost</strong></th>
<th><strong>Expected volume/deal</strong></th>
<th><strong>Cost/resolved owner</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td>Assessor/registry anchor</td>
<td>Municipal + VA SCC (free)</td>
<td>$0.00</td>
<td>1–3 queries</td>
<td>$0.00</td>
</tr>
<tr class="even">
<td>SOS resolution (expansion mkts)</td>
<td>Cobalt / OpenSOSData</td>
<td>$0.03–$2.00</td>
<td>1–4 lookups</td>
<td>$0.10–$4.00</td>
</tr>
<tr class="odd">
<td>Bulk append (tier 1)</td>
<td>Datazapp</td>
<td>$0.01–$0.03</td>
<td>1</td>
<td>$0.02</td>
</tr>
<tr class="even">
<td>Skip trace (tier 2)</td>
<td>BatchData API</td>
<td>$0.07–$0.18/match</td>
<td>1–2</td>
<td>$0.07–$0.36</td>
</tr>
<tr class="odd">
<td>Deep trace (tier 3)</td>
<td>Enformion/Endato</td>
<td>$0.01–$0.25/match</td>
<td>0.3 (fallback only)</td>
<td>$0.01–$0.08</td>
</tr>
<tr class="even">
<td>Validation + litigator</td>
<td>Trestle</td>
<td>$0.02–$0.04/contact</td>
<td>3–6 contacts</td>
<td>$0.06–$0.24</td>
</tr>
<tr class="odd">
<td>TOTAL</td>
<td>—</td>
<td>—</td>
<td>—</td>
<td><p>~$0.15–$0.75 typical;</p>
<p>&lt;$5.00 worst case (multi-layer LLC, expansion market)</p></td>
</tr>
</tbody>
</table>

At retail, competitors charge \$0.10–\$0.17/record for raw tracing with no validation, no LLC recursion, and no compliance stamp (REISkip, DirectSkip), or bundle it into \$400–\$500/mo seats (Reonomy). A metered price of \$0.50–\$1.00 per fully resolved, compliance-stamped owner (bundled allowances per tier, then per-pull credits) is both cheaper than any comparable assembly and gross-margin positive at every tier. BatchData subscription tiers (\$2K/mo → 100K traces) become economic at multi-tenant scale.

## **4.4 Compliance gate (built-in — no competitor ships this)**

Legal state of play as of July 2026, encoded as pipeline rules. This is defensive architecture: federal TCPA exposure is \$500–\$1,500 per call/text, uncapped; state mini-TCPAs (FL, TX, OK, MD, CT, NY, CA) add up to \$5K–\$11K per violation. The gate makes the compliant path the only path the software allows.

- C1 DNC SCRUB — scrub against the National DNC Registry ≤31 days before any campaign use; auto re-scrub on expiry. Separate scrubs for the six state registries (IN, LA, MO, PA, TX, WY). Internal do-not-call ledger retained 5 years. BatchData returns DNC flags inline; Trestle/DNC.com provide re-scrub APIs.

- C2 LITIGATOR SCRUB — professional-plaintiff suppression via Trestle inline litigator flag (+\$0.005/query) at validation time; escalate to TCPA Litigator List API (\$299–\$499/mo tiers) when outbound volume justifies it.

- C3 CHANNEL RULES — live manual/agent-initiated calls to skip-traced numbers: permitted with DNC scrub. Prerecorded/AI-generated voice (including voice-clone voicemail drops of the Terrakotta style) and ringless voicemail to cell phones: require prior express written consent — the platform hard-blocks these to non-consented cells rather than replicating Terrakotta’s gray-zone default. SMS: treated as calls; consent ledger required.

- C4 TIME & FREQUENCY — quiet hours 8:00 AM–9:00 PM called-party local time (derived from number geography + address), plus stricter state overlays (e.g., Oregon 3 calls/consumer/day from Jan 2026). Frequency caps enforced per person across all campaign types.

- C5 REVOCATION LEDGER — any opt-out via any channel honored within 10 business days across all channels; cross-channel "revoke-all" readiness by the FCC’s extended January 31, 2027 effective date. Opt-outs propagate to the tenant’s internal DNC list immediately.

- C6 FCRA FIREWALL — skip-trace data is non-FCRA and lawful for acquisition marketing/owner location. It must never feed tenant screening, credit, or employment decisions. Enforced structurally: poc_records live in the acquisition schema with no read path from any future leasing/screening module; screening (if ever built) uses a registered CRA. Vendor contracts prohibit FCRA use; the platform enforces it in architecture, not policy.

- C7 LICENSING & REGISTRATION — tracing for the tenant’s own acquisition purposes is the product pattern (generally exempt from state PI licensing); a managed "we trace for you" concierge is out of scope pending state-by-state PI review. Monitor state data-broker registration laws (CA Delete Act/DROP 2026 phase-in, TX, OR, VT) before any resale of raw contact data — the product sells resolution-as-a-feature, not data files.

## **4.5 Data contract (poc_record, abridged)**

poc_record { id, tenant_id, property_id, portfolio_id, role: owner\|principal\|pm\|lender\|agent\|prior_owner, person { full_name, age_band, deceased: bool }, entity_chain: \[{ entity_name, jurisdiction, filing_id, officers\[\], confidence 0–1 }\], phones: \[{ e164, line_type, grade A\|B\|F, name_match, litigator: bool, dnc: { federal: bool, state: \[..\], scrubbed_at }, callable: bool, reason }\], emails: \[{ address, deliverability, grade }\], addresses: \[{ formatted, kind: current\|prior\|mailing, first_seen, last_seen }\], relatives: \[{ name, relation, phones_ref }\], other_properties: \[property_ref\], provenance: \[{ field, vendor, query_id, cost_usd, retrieved_at }\], compliance: { stamped_at, expires_at, revocations: \[..\] }, created_at, updated_at } — SQLite table set in v5.0 single-tenant; maps 1:1 onto the Postgres row-level-security schema at SaaS migration.

## **4.6 Acceptance criteria**

- AC-A1 ≥80% of a 100-property Hampton Roads Class-C validation set resolves to a named human owner with ≥1 grade-A phone (benchmark: Reonomy’s aspirational 80%, weakest exactly on this asset class — meeting it here beats them where it matters).

- AC-A2 Median end-to-end resolution latency ≤60s single property; ≤30 min for a 1,000-property batch.

- AC-A3 Zero callable=true contacts without a valid, unexpired compliance stamp — enforced by test suite; the dialer/export path physically cannot emit an unstamped number.

- AC-A4 Cost telemetry: per-tenant spend dashboard accurate to the cent against vendor invoices; hard budget stop verified.

# **5. Module B — Compliant Outreach Engine (new)**

Purpose: convert resolved POCs into conversations — surpassing Terrakotta’s workflow speed while inverting its compliance posture. Terrakotta ships velocity and leaves legality to the user; the Workbench ships velocity inside a gate that makes the legal path the default.

## **5.1 Capabilities**

- B1 Click-to-call power dialing from any POC list (grade-A, callable numbers only), with local-presence caller ID, automatic call logging, and AI post-call summaries written to the deal record. Target throughput ≥80 contacts/hr/user (Terrakotta claims ~100 with voicemail automation; we trade a small throughput delta for the compliance hard-block on prerecorded drops).

- B2 AI-personalized artifacts per POC, grounded in Workbench data no dialer can see: the owner’s own deed chain ("you bought in 2014 at \$1.1M"), loan maturity from GRANITE ("your HUD loan matures March 2027"), assessed-value trend, and portfolio context. Channels: live-call talking points, direct mail (letter PDF batch with per-piece QR/phone tracking), and email sequences (consent-aware).

- B3 Voicemail: live-agent-initiated voicemail permitted; AI voice-clone drops only to numbers with recorded prior express written consent (consent ledger, C5). This is a deliberate product stance — documented in-app so users understand why the unsafe button does not exist.

- B4 Cadence orchestration: multi-touch sequences (call → letter → call → email) with per-person frequency caps (C4), state-rule awareness, and automatic pause on any inbound opt-out or deal-stage change.

- B5 Relationship graph: every touch, response, and referral edge accumulates into the tenant’s private contact graph — answering "which of my 23 lenders actually close in Hampton Roads?" (Inbox → Deal Engine, Module D, feeds this same graph.)

## **5.2 Acceptance criteria**

- AC-B1 A GRANITE maturing-loan hit can go from radar row → resolved owner → compliant dial in ≤3 clicks and ≤5 minutes.

- AC-B2 100% of outbound touches logged with channel, timestamp, rule-evaluation trace (which compliance checks passed), and outcome — audit-exportable.

- AC-B3 Direct-mail batch of 500 letters generated, deduplicated, and export-ready (or lob-style API handoff) in ≤10 minutes.

# **7. Phase 0 — Current Data-Set Analysis & ALN De-Identification**

Directive: remove every reference to ALN and replace ALN identifiers, fields, and derived values with Eight Rock’s own data and taxonomy, such that nothing ALN-sourced remains discernible anywhere in the platform — front end, back end, database, exports, prompts, and documentation. Strategy selected: build the replacement data spine first, prove parity, then cut over and purge (no capability gap). This work is Phase 0 — it precedes and de-risks everything else in v5.0, and Modules A and F build on the clean spine from day one.

## **7.1 Current data-set inventory (read-only scan of v2.4.1, July 21, 2026)**

Findings from a full scan of the live workbench (python_workbench/, data/workbench.db, deal folders, and repo root) on the production machine:

<table>
<colgroup>
<col style="width: 18%" />
<col style="width: 9%" />
<col style="width: 35%" />
<col style="width: 36%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Store / table</strong></th>
<th><strong>Rows</strong></th>
<th><strong>Provenance</strong></th>
<th><strong>ALN exposure</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td>properties</td>
<td>13,658</td>
<td>ALN exports (9 state files: VA, GA incl. Atlanta, NC, SC, TN; March 2026 vintage)</td>
<td><p>CONTAMINATED — 13,655 rows carry aln_id;</p>
<p>raw_row holds the full original ALN JSON per row</p></td>
</tr>
<tr class="even">
<td>muni_records</td>
<td>3,902,336</td>
<td>Self-sourced municipal pulls (assessor/deed portals)</td>
<td>CLEAN</td>
</tr>
<tr class="odd">
<td>property_loans</td>
<td>12,090</td>
<td>HUD/FHFA public feeds (GRANITE)</td>
<td>CLEAN</td>
</tr>
<tr class="even">
<td>emails</td>
<td>600</td>
<td>O365 intake</td>
<td>CLEAN</td>
</tr>
<tr class="odd">
<td>calibration_current / _history</td>
<td>20 / 280</td>
<td>FRED/ALN/assessor blend</td>
<td>PARTIAL — ALN-fed inputs must be re-derived</td>
</tr>
</tbody>
</table>

ALN-derived columns in properties (47 total columns): direct identifiers (property_id = ALN API UUID, aln_id, aln_pull_date, source_file naming ALN exports, raw_row); survey values (occupancy_pct, avg_rent, avg_sqft, rent_per_sqft, asset_class = ALN price class, property_type = ALN building style, market/submarket = ALN market taxonomy); and contact fields (owner, owner_address, owner_phone, owner_fax, manager, area_supervisor, management_company, pm_software) — the last group is exactly what Module A replaces with skip-traced, self-resolved data.

### **Code footprint (≈400 references across ~30 files)**

- Heavy: ui/inventory.py (138 refs — the browse/filter UI is built around ALN fields), data/aln_loader.py (66 — entire module), core/calibration.py (28), data/property_io.py (26), data/db.py (26), ui/comps.py (17), ui/listings_panel.py (12).

- Embedded in UX and semantics: core/provenance.py defines "aln" as a trust tier with its own legend entry and color; config.py carries src_aln color tokens and a yellow "ALN" chip; the Excel export’s DATA SOURCE KEY tab and core/artifact_prompts.py (LLM prompts) name ALN explicitly; help_page, sidebar, property_detail, seller_floor, portfolio_risk, and tests reference it.

- File-system artifacts: the 9 source ALN .xlsx exports; deal folders containing ALN files (e.g., Properties/Crossroads-Townhomes-26-Norfolk/2026-04-03-Cleghorn-Capital-Portfolio-ALN.xlsx and its \_archive copy); data/\_SAFETY_properties_dump\_\*.json; workbench.db.bak-\*; .bak code files; \_\_pycache\_\_ compiled copies of aln_loader; git history.

- Outside the repo: the Cowork project description, the underwrite-deal agent skill, and prior strategy documents all reference "ALN Virginia Property Export — March 10th, 2026" — these must be updated in the same pass or the taxonomy leaks back in through AI-assisted workflows.

## **7.2 Replacement taxonomy (Eight Rock native)**

- Property ID: 8R-{FIPS}-{parcel-hash} — deterministic: 5-digit county FIPS + first 12 hex of SHA-256 over the normalized APN (uppercase, punctuation stripped). Regenerable from public records alone, provably non-ALN, stable across refreshes. Properties without an APN yet (new/expansion markets) get 8R-{FIPS}-X{geohash9} until parcel match, then migrate with an alias record.

- Classification: asset_class (ALN price class) → 8r_class, computed by Eight Rock’s own codified criteria (vintage band, rent position vs. submarket FMR percentile, condition signals from permits/assessor) — this converts a licensing liability into buy-box IP. Building style → 8r_form (garden/townhome/mid-rise/…) re-derived from assessor use codes + unit counts.

- Market taxonomy: ALN market/submarket strings → 8r_market/8r_submarket keyed to Census CBSA + Eight Rock-defined submarket polygons (already implicit in the comp engine’s radius logic).

- Survey values: occupancy/avg_rent/avg_sqft re-sourced from the public spine — listing scrapes (unit-level rents on \<50-unit stock), HUD FMR/BAH context, assessor square footage, and Module A-resolved management data. Field names drop any ALN semantics; provenance key "aln" is retired and replaced by "8r" (self-sourced spine) in provenance.py, legend, colors, and the Excel DATA SOURCE KEY.

## **7.3 Build-then-cutover plan**

| **Step**           | **Work**                                                                                                                                                                                                                                                                                       | **Gate to advance**                                                                                                |
|--------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| P0-1 Spine build   | Populate properties_8r from muni_records (3.9M rows already in hand), assessor rolls, deeds, SOS entities, listings, HUD/FMR — Hampton Roads first, then the other ALN-covered states as needed                                                                                                | properties_8r covers ≥95% of HR multifamily parcels ≥10 units                                                      |
| P0-2 Shadow parity | Dual-run: every comp pull, radar sweep, and underwrite executes against both spines; log deltas. ALN remains live (licensed internal use) but frozen — no new imports                                                                                                                          | Comp-set overlap ≥90% and avg-rent delta ≤5% on a 50-deal replay; calibration re-derived ALN-free within tolerance |
| P0-3 Cutover       | Flip reads to properties_8r; migrate deal references via crosswalk (aln UUID → 8R id); update UI, prompts, provenance, help, tests, Excel export key                                                                                                                                           | Full regression suite green; template-true export diff clean                                                       |
| P0-4 Purge         | Drop old table + raw_row; delete ALN xlsx exports, SAFETY dumps, .bak files, \_\_pycache\_\_, deal-folder ALN files (quarantine copy to offline legal hold if counsel advises retention); scrub git history (git-filter-repo); destroy crosswalk after 30-day soak; update project docs/skills | Verification sweep passes (7.4)                                                                                    |

## **7.4 Acceptance criteria — "not discernible" defined**

- AC-P0-1 Case-insensitive search for "aln" across the repo, database schema + contents, UI strings, prompts, exports, help, tests, and git history returns zero hits (excluding this spec and legal-hold quarantine).

- AC-P0-2 No field in any live store is derivable only from ALN: every value either regenerates from a named public source or is user/document-entered, proven by a full spine rebuild from scratch on a clean machine.

- AC-P0-3 Generated artifacts (Excel workbook, exec memos, comp maps) contain no ALN identifiers, source labels, or legend entries — verified by artifact diff on the 50-deal replay set.

- AC-P0-4 Comp finder, radar, and calibration outputs on the clean spine meet the P0-2 parity tolerances — quality holds, provenance changes.

- AC-P0-5 Counsel sign-off recorded: license-required deletion obligations met; quarantine/retention decision documented.

# **6. Modules C–G — Completing the Loop**

These modules extend shipped v2.4.1 capability. Each is specified to the level an AI engineering agent can execute; deeper sub-specs are generated per-module at build time.

## **6.1 Module C — Forced-Seller Radar v2 + GRANITE Tabs 2–5**

- Single 0–100 distress score per property fusing: loan-maturity proximity (GRANITE HUD feed + FHFA PUDB, shipped), tax delinquency (municipal portals, extend sale-history scrapers), permit decay (Census BPS + city permit feeds), ownership tenure (deed chain, shipped), listing appearance/removal (scraper), and — new in v5.0 — POC signals from Module A (deceased owner flag, out-of-state mailing-address change, entity dissolution filing). Evidence panel behind every score; continuous monitoring with alert routing to the Outreach Engine.

- GRANITE Tabs 2–5: borrower intelligence (entity → Module A resolution), lender database (from loan records + relationship graph), loan comps, saved-search alerts. The loan module becomes the daily-return hook: radar hit → pierce → dial in one surface.

- Acceptance: backtest the score against the 7-city deed chains — properties that actually traded in the last 24 months must show a materially higher trailing score than matched non-traded controls (target: top-decile score captures ≥3x base-rate of subsequent sales).

## **6.2 Module D — Inbox → Deal Engine**

- Connect Outlook/Gmail (Graph API — the identity layer is already Microsoft-first). Classify inbound broker/lender/attorney email; auto-extract OMs and attachments into the document-AI pipeline; auto-create/update pipeline records, contact CRM entries, and term-sheet history with zero manual entry. Confidence-gated: below-threshold extractions queue for one-click human confirm rather than silently writing.

- This was flagged in the July 2026 roadmap as the single highest-leverage feature in modern PropTech; ingestion backend is partly built. It feeds the same relationship graph as Module B — inbound reality calibrates outbound targeting.

## **6.3 Module E — Document AI & Underwriting Engine hardening**

- Extraction QA to Primer’s published bar and past it: deterministic validation rules per document type (rent roll unit-count ties to OM; T-12 totals tie to statement; GPR cross-check), per-field confidence with low-confidence flagging for review, and the RECONCILE tab tie-out already shipped in the Excel export. Add: anomaly detection on rent rolls (below-comp units, duplicate units, expiration clusters, RUBS-as-rent) — spec’d since June, now scheduled.

- Named stress-test overlays (2008-style, COVID-style, insurance shock) wired into the sensitivity grids; bidirectional diligence-to-verdict tightening (a DD finding downgrade re-runs sensitivity and can move GO → WATCH automatically).

## **6.4 Module F — Data Independence (executes the Phase 0 spine, Section 7)**

- Hard commercial prerequisite from both June documents, now elevated to Phase 0 (Section 7): all ALN data is replaced by the self-sourced property spine — assessor/parcel records (already 8 cities), Census/BLS/FRED/HUD (shipped ETL), state corporate registries (Module A), municipal permits, and public listing scrapes for rent signal on \<50-unit stock — the exact segment where Matrix is blind and HelloData is listing-derived anyway. Cutover follows the build-then-purge plan in 7.3; counsel review of each public source’s terms remains a gating item before external sale.

- Coverage sequencing: Hampton Roads (deep, shipped) → adjacent Virginia metros (Richmond, Charlottesville shipped for deeds) → 2–3 Sunbelt metros, per the Phase-3 roadmap. Every expansion market ships with the full stack: parcels, deeds, SOS, permits, rent signal.

## **6.5 Module G — Platform & Commercialization Infrastructure**

- Sequence per the July roadmap: (1) identity — Entra ID shipped foundation, add Google/Apple/email via unified IdP with SAML/SCIM path; (2) multi-tenant isolation — pooled Postgres with row-level security on tenant_id, per-tenant encryption keys on document storage (cryptoshred offboarding), append-only audit log; (3) saved multi-model library formalizing versioned snapshots; (4) reliability — dual-region, PITR backups, status page; (5) SOC 2 program via trust platform; (6) Stripe billing with usage meters for AI and data pulls (skip-trace credits are the flagship meter); (7) Next.js/React front end over FastAPI service layer, preserving the Python underwriting/ETL/AI engine.

# **8. System Architecture (v5.0 target)**

- Core: Python engine (underwriting, ETL, document AI, calibration) behind FastAPI; Next.js/React UI; SQLite → Postgres (RLS) at tenancy cutover; object storage per-tenant-encrypted; Redis queue for pipeline stages (S1–S7 above run as resumable jobs).

- AI layer: Claude-driven extraction and drafting with model routing + prompt caching for margin (per July infra requirements); deterministic validators wrap every model output; all agent actions logged with inputs/outputs for audit.

- Vendor abstraction: every external data call goes through a provider interface (SkipTraceProvider, SOSProvider, ValidationProvider, DNCProvider) with per-vendor adapters, cost accounting, retry/fallback waterfall config in data — vendors are swappable without code changes when pricing or quality shifts.

- Security posture: secrets in managed vault; encryption in transit/at rest; the FCRA firewall (C6) as a schema boundary; "not investment advice" disclaimer surface; ToS/privacy as launch gates. Full security requirements in 8.1.

## **8.1 Security Requirements — Fifth Dimension parity benchmark (adopted July 21, 2026)**

Directive: the security posture published by Fifth Dimension (fifthdimensionai.com/security) — the segment’s credibility leader with institutional clients — is adopted as the Workbench’s implementation target, scheduled after core v5.0 functionality is working (build phase V5-P6). Their page establishes three pillars; each is mapped below to a concrete Eight Rock implementation, and where their public page is silent (encryption specifics, pen testing, incident response), this spec goes further — matching the benchmark where it is strong and exceeding it where it is vague.

### **SR-1 Certification & Compliance**

- SR-1.1 SOC 2 Type II across all five trust criteria (security, availability, processing integrity, confidentiality, privacy). Sequence: trust program stood up during V5-P5 (Vanta or equivalent, per Module G) → SOC 2 Type I report before the first paying customer → Type II after the 3–12 month observation window. FD parity: "SOC 2 Type II compliant… meets institutional requirements."

- SR-1.2 ISO 27001 certification on the same ISMS scope — pursued after Type II (shared control set makes the increment small). FD parity: certified.

- SR-1.3 Privacy regime: CCPA/CPRA and Virginia CDPA compliance at launch (US customer base); GDPR-readiness documented for the day an EU LP or customer appears — data-rights handling, lawful-basis records, privacy-by-design evidence. FD parity: full GDPR compliance.

- SR-1.4 Additional to benchmark: the telemarketing/skip-trace compliance gate (Section 4.4) and FCRA firewall (C6) are treated as first-class compliance controls inside the same ISMS — an obligation FD does not carry and no competitor documents.

### **SR-2 Architecture & Data Protection**

- SR-2.1 Customer isolation: FD runs "a completely isolated instance" per customer. Workbench baseline: pooled Postgres with row-level security keyed to tenant_id + per-tenant customer-managed encryption keys on document storage (cryptoshred on offboarding) — the Notion/Linear/Vanta pattern already in Module G. Firm/enterprise tier adds the FD-equivalent option: dedicated single-tenant instance (own database + storage namespace) at premium pricing, and a self-hosted/on-prem path on the long-range roadmap. Acceptance: cross-tenant read attempts fail at the database layer, proven by adversarial tests in CI.

- SR-2.2 Zero data training: customer deal data, documents, and POC records are never used to train or fine-tune models — enforced architecturally, not as an opt-out: all LLM calls route through no-training API endpoints (already Anthropic-API based), no customer-data fine-tuning pipeline exists, and the commitment is contractual in the ToS. FD parity: "Your data never trains our models. Not for improvement, not for benchmarking, not ever."

- SR-2.3 Data residency: all customer data pinned to US data centers; no cross-border transfer without explicit written approval. Region-pinning architecture (per-tenant region tag on storage and compute) built so EU residency can be added without re-architecture. FD parity: EEA/US regional residency.

- SR-2.4 Exceeding the benchmark (FD’s page is silent here): AES-256 encryption at rest, TLS 1.2+ in transit, managed KMS with annual key rotation; annual third-party penetration test + remediation SLA (critical ≤7 days, high ≤30); continuous dependency/vulnerability scanning in CI; documented incident-response plan with customer notification ≤72 hours; immutable backups, point-in-time restore, dual-region DR (Module G) with annual restore drills; published subprocessor register with flow-down data-protection terms (skip-trace and data vendors included).

### **SR-3 Access Control & Audit**

- SR-3.1 Audit trails: every operation logged — who accessed what, when, and why — covering queries, document processing, AI/agent actions, skip-trace pulls, outreach touches, and export generation; append-only store (Module G), tenant-exportable for their own audits. FD parity: "Every operation is logged"; Workbench extends it to compliance-rule evaluation traces (AC-B2), which FD does not claim.

- SR-3.2 Access control: least-privilege RBAC on the shipped Entra ID foundation (internal staff vs. LP scoping already coded); SSO via SAML/SCIM against customer IdPs (Okta/Azure AD, per Module G); MFA optional on entry tiers, enforced on premium and for all Eight Rock staff; quarterly access reviews; employee/contractor access restricted to documented business need.

- SR-3.3 Privacy by design: data minimization defaults (only fields needed per module), retention schedules per data class (POC compliance stamps 31-day/5-year rules from 4.4; deal documents tenant-controlled), and deletion on offboarding via cryptoshred with written confirmation.

# **9. Pilot Deployment, Multi-User Operation & Authentication**

This section specifies moving the Workbench off localhost onto a dedicated office server with a reserved IP, so external pilot users can log in from anywhere. It answers four things directly: (A) exactly what to install and run, (B) a hard rule about OneDrive and the live database, (C) what happens when two people edit the same property at once and the functionality that fixes it, and (D) a cheap login layer plus an admin page to manage users. This is a real, buildable pilot stack — Streamlit behind a reverse proxy is appropriate for a pilot, and every piece here (Postgres, auth, concurrency control) carries forward into the Module G SaaS architecture rather than being thrown away.

## **9.1 Deployment reference — dedicated office server**

Recommended target is a small Linux box (cheapest, standard for this stack); Windows Server is fully supported if you prefer to stay in the Microsoft world. Install the following:

| **Component**       | **Recommended choice**                           | **Purpose & where to get it**                                                                                                  |
|---------------------|--------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| Operating system    | Ubuntu Server 24.04 LTS (or Windows Server 2022) | Host OS. ubuntu.com/download/server. Windows works with NSSM + Caddy for Windows.                                              |
| Python runtime      | Python 3.12 + uv                                 | Runs the existing app; uv already used (uv.lock present). python.org · astral.sh/uv                                            |
| Application         | The Workbench Streamlit app (existing)           | Run bound to localhost: streamlit run app.py --server.address 127.0.0.1 --server.port 8501. The proxy handles the public side. |
| Database            | PostgreSQL 16                                    | Replaces SQLite for multi-user (see 9.2–9.3). True concurrent writes. postgresql.org                                           |
| Reverse proxy + TLS | Caddy                                            | Public HTTPS with automatic Let’s Encrypt certificates; sits in front of Streamlit. caddyserver.com (alt: nginx + certbot)     |
| Process manager     | systemd (Linux) / NSSM (Windows)                 | Keeps the app running, auto-restarts on crash/reboot. nssm.cc                                                                  |
| Domain name         | e.g. workbench.eight-rock.com                    | A-record pointed at the reserved IP. Required — Let’s Encrypt will not issue a certificate for a bare IP address.              |
| Network / firewall  | Router port-forward + UFW/Windows Firewall       | Forward public 80 + 443 to the server; keep 8501 bound to localhost only; allow just 80/443 (+ SSH/RDP from your IP).          |
| Backups             | Nightly pg_dump                                  | Scheduled dump to a second disk and to OneDrive as a backup target (a closed dump file is safe to sync).                       |
| Secrets             | .streamlit/secrets.toml or env vars              | DB credentials, identity-provider client secret, and a strong cookie_secret. Never commit to git or OneDrive.                  |

**Setup sequence:** provision the box → install Python + uv → place the app code on local disk at e.g. /opt/8rw (not OneDrive) → create the Postgres database and migrate the schema → set secrets → run Streamlit as a service on 127.0.0.1:8501 → put Caddy in front pointed at your domain → point DNS A-record at the reserved IP and port-forward 80/443 → test an external login. Reserve a static internal IP for the box on your router in addition to the public reserved IP so port-forwarding stays stable.

## **9.2 File storage & the OneDrive rule (read this first)**

**Hard rule:** never run the live database or the app’s working directory out of a OneDrive-synced folder. OneDrive continuously syncs and locks files; a database file that is open and being written while OneDrive tries to sync it can corrupt, and OneDrive cannot merge two users’ concurrent writes to a binary database file — it will simply create conflict copies and one user’s data is lost. This is the single most likely way to lose data in this deployment.

Where each thing belongs:

- **Live database →** server local disk only (Postgres default data directory). Never inside a synced folder.

- **App code →** local disk (/opt/8rw or C:\8rw). Distribute updates via git pull, not OneDrive sync.

- **Runtime temp / generated files →** local disk.

- **OneDrive is fine for →** document inputs treated as read-only ingest (copy in, don’t run from it), nightly database backups (a dump is written once, closed, then synced — safe), and delivered artifacts.

**Migration note:** the current data/workbench.db and the 3.9M-row municipal store move into Postgres during pilot setup — the same cutover as the Phase-0 spine work (Section 7). After migration the SQLite file is no longer the live store.

## **9.3 Multi-user concurrency control (the fix for simultaneous edits)**

**Will two users editing the same property at the same time cause problems? Yes — today it would, on two levels, and the spec below adds the functionality to fix it.**

- **Engine level:** SQLite allows only one writer at a time (whole-file lock). Two simultaneous saves throw “database is locked,” and with OneDrive in the path risk corruption. Moving to Postgres (9.1) removes this limit — it supports true concurrent writes with row-level locking.

- **Application level (the one that actually bites):** even on Postgres, a naive save is “last write wins” — if User A saves, then User B saves the copy they opened earlier, A’s edits are silently overwritten and lost. The database is happy; the user is not. This requires explicit concurrency functionality:

- **FR-9.3.1 Optimistic concurrency.** Every editable record (property, deal, underwriting model, POC) carries a row_version integer, loaded with the record. Saves run a compare-and-set — UPDATE … WHERE id = ? AND row_version = ? and increment — so if the record changed underneath the user, zero rows update and the conflict is detected instead of silently applied.

- **FR-9.3.2 Conflict-resolution UI.** On a version mismatch, block the silent overwrite and show a dialog naming who changed the record and when, with Reload / Review changes / Overwrite anyway options, and field-level merge where practical.

- **FR-9.3.3 Presence & soft locks.** When a user opens a property for editing, show others a live banner — “🔒 Jane Doe is editing (since 2:14 PM)” — backed by a short-lived advisory lock (auto-expiring TTL ~5 min, heartbeat-refreshed) that discourages collisions without hard-blocking. Read-only viewing is never blocked.

- **FR-9.3.4 Autosave + audit recovery.** Edits checkpoint to the append-only audit log (Section 8.1, SR-3), so even an accepted overwrite is recoverable.

- **FR-9.3.5 Transactional writes.** Multi-table saves (deal + underwrite + waterfall) run inside a single database transaction, so a partial failure can never leave a half-written deal.

- **AC-9.3** In a two-browser test, simultaneous saves to the same property never silently lose data — the second saver receives the conflict dialog; the presence banner appears within 3 seconds; and the advisory lock frees automatically if a user walks away.

## **9.4 Authentication & user administration**

Recommendation: use Streamlit’s built-in OpenID Connect login (st.login / st.user / st.logout, native since Streamlit 1.42, Feb 2025) as the integration layer, with a hosted identity provider behind it. This gives Google, Microsoft, and email/password sign-in without you writing any session-management code. Configuration is a small \[auth\] block in .streamlit/secrets.toml (redirect_uri, cookie_secret, and the provider’s client_id / client_secret / metadata URL).

| **Provider**                      | **Free tier & methods**                                                                                                                                                                                                     | **Best when**                                                                                                                |
|-----------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| Auth0 (recommended for the pilot) | Free up to 25,000 monthly active users; password + up to 5 social logins (Google, Microsoft); branded hosted login; built-in user-management dashboard. Excludes MFA/RBAC on free — do roles in the app. Paid from \$35/mo. | You want Google + Microsoft + email/password in one place now, with a hosted admin console out of the box. auth0.com/pricing |
| Microsoft Entra External ID       | Large free MAU allowance (verify at aka.ms/ExternalIDPricing); Google social + email/password; admin via the Azure portal.                                                                                                  | You prefer to stay in your Microsoft/O365 tenant and reuse the MSAL sign-in already scaffolded in auth.py.                   |
| Google OAuth alone                | Free; Google accounts only.                                                                                                                                                                                                 | Too narrow for “users from all over” — no Microsoft or email/password. Not recommended as the only method.                   |

**Verdict:** Auth0’s free tier for breadth and the easiest hosted admin today; Microsoft Entra External ID as the Microsoft-native alternative that reuses your existing MSAL scaffold. Both speak OIDC to Streamlit identically, so the choice is reversible later at near-zero cost.

Identity lives in the provider; authorization lives in the Workbench. You asked for an admin page — here is its spec:

- **FR-9.4.1 Users table.** A local users table keyed to the provider’s subject id (sub), storing email, display name, role (admin \| internal \| lp \| trial), status (invited \| active \| suspended), scope (which deals/markets an lp or trial user may see), created_at, and last_login.

- **FR-9.4.2 Admin page (role = admin only).** Invite/approve users, assign and change roles, scope lp/trial users to specific deals (reusing the shipped Entra LP-scoping logic), suspend/reactivate, and view each user’s audit trail and last login.

- **FR-9.4.3 Safe-by-default onboarding.** The first authenticated user (you) is auto-promoted to admin; everyone after lands as trial/pending and sees only a “pending approval” screen until an admin approves them — so a random signup can never see deal data.

- **FR-9.4.4 Server-side enforcement.** RBAC is enforced on every page and action server-side, not by hiding UI elements, consistent with SR-3.2.

- **AC-9.4** An external user can sign in with Google, Microsoft, or email/password; a brand-new signup sees only the pending-approval screen until approved; role/scope changes take effect on the next action; every login is written to the audit log.

*This 9.4 admin page is the pilot-scale identity surface; the full organization model, the role-preset library, and the permission matrix it draws on are specified in Section 10.*

# **10. Multi-Tenancy, Organizations & the Role Model**

This section specifies the true multi-user environment and is written to be handed to Claude Code as a build unit: data contracts, a curated role-preset library grounded in how real multifamily firms are staffed, the permission model, and the point-and-click admin functionality. Design decisions confirmed with the principal: (1) a shared, public-sourced reference layer under org-private deal data; (2) all internal members of an org see all of that org’s deals, with role presets controlling the lens and the write rights on top; (3) a two-tier structure (platform over organizations); (4) simplicity is a hard requirement — no IT staff, no per-user permission wiring, and no institutional knowledge that walks out when an employee leaves.

## **10.1 Tenancy model — shared reference layer, org-private deals**

- **Shared reference layer (global, read-only to orgs):** the 8R property spine (Phase 0), comps, GRANITE loan feed, municipal sale-history, and public market-data ETL. One copy, maintained by the platform, benefits every org, and carries no org_id. When User 1 in Org A and User 2 in Org B both look up Property A’s public record, they see the same reference data.

- **Org-private deal layer (isolated per org):** underwrites, saved models, POC/skip-trace results, notes, pipeline, documents, actuals, LP/capital data. Every row carries org_id and is invisible to any other org. So if User 1 (Org A) underwrites Property A and User 2 (Org B) also works Property A, they each build private analyses on the same shared public record and never see each other’s numbers — exactly the requirement.

- **Enforcement:** Postgres row-level security keyed to org_id on every org-private table; reference tables globally readable, never writable by an org. Cross-org read is impossible at the database layer, not just hidden in the UI (ties to SR-2.1).

- **Affiliated entities:** vertically-integrated firms run a property-management arm and a construction arm as separately-staffed entities (e.g., FPA → Trinity + Redwood; Bainbridge). Model this with an optional parent_org_id so a construction-arm user is scoped to their unit and cannot see the parent fund’s LP returns, while the principal sees across units.

## **10.2 Organization & user taxonomy (data model)**

- organizations { id, name, type: sponsor \| pm_arm \| construction_arm, parent_org_id (nullable), plan_tier, buy_box_config, created_at } — buy-box thresholds, KPI targets, and saved views live here at the org level (see 10.5).

- users { id, idp_sub (from Auth0/Entra), email, display_name, status, created_at, last_login } — one identity per human, independent of org.

- memberships { id, user_id, org_id, role_preset, scope (org_all \| portfolio:\[ids\] \| deal:\[ids\] \| own_only \| single_deal:id), status: invited \| active \| suspended, invited_by, created_at } — a user may belong to several orgs with a different preset in each. This row, not the user, carries the permissions.

- role_presets { key, label, module_grants\[\], field_mask\[\], action_grants\[\], default_dashboard, kpi_set } — platform-maintained; the org admin only ever picks a key.

- audit_log { id, org_id, actor_user_id, action, target, before, after, reason, ts } — append-only (SR-3.1); every membership and permission change is logged.

## **10.3 The role-preset library (why setup is point-and-click)**

Instead of configuring permissions per user (which requires IT and creates knowledge that leaves with the person who set it up), an admin assigns each user one preset from a curated library. Every preset ships with its permissions, default dashboard, and KPI set pre-wired. The library, grounded in the surveyed firms, covers the full org from owner to maintenance tech to external LP:

| **Role preset**                    | **Maps to real titles**                   | **Key permissions (view / edit)**                                                               | **Blocked from**                                                                                        | **Default scope**          |
|------------------------------------|-------------------------------------------|-------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|----------------------------|
| Principal / Owner                  | Principal, Managing Partner, CEO, GP      | Everything; the only preset that can commit GO / NO-GO; sets buy-box hurdles; manages org users | Nothing (org super-admin)                                                                               | org_all                    |
| President / COO                    | President, COO, Chief of Staff            | All deals + ops; advance deal stages (GO co-gated with Principal)                               | Final GO commit alone; LP legal edits                                                                   | org_all                    |
| Head of Acquisitions               | CIO, VP/Director Acquisitions             | Full underwriting edit; comps; pipeline; recommend “IC-ready”                                   | Final GO commit; edit actuals; edit waterfall                                                           | org_all                    |
| Analyst / Associate                | Acquisitions Analyst/Associate            | Build & version underwrites (draft); comps; sensitivity; ingest docs                            | Commit GO; edit waterfall; edit actuals; LP PII                                                         | org_all or deal:\[…\]      |
| Capital Markets / Debt             | Capital Markets, VP Corp Finance          | Debt module, sources & uses, JV terms; returns                                                  | Operating actuals; property-ops data                                                                    | org_all                    |
| Asset Manager                      | Dir/VP Asset Mgmt, Portfolio Mgr (fund)   | Business-plan assumptions, budgets, actuals, reno tracking, distributions (view)                | Acquisition GO; LP legal docs                                                                           | portfolio:\[…\]            |
| Regional / Ops Manager             | Regional Manager, VP Operations           | Multi-property ops rollup, site P&L, budgets (draft), staffing                                  | Acquisition underwriting; other deals’ price; waterfall                                                 | portfolio:\[…\]            |
| Property Manager                   | Property Manager, Assistant PM            | Their site: rent roll, leases, work orders, site budget vs actual                               | Purchase price; deal IRR/returns; LP data; other properties                                             | deal:\[site\]              |
| Leasing Agent                      | Leasing Consultant/Agent                  | Unit availability, asking & comp rents, applications, lease drafts                              | NOI/financials; purchase price; budgets; investment data                                                | deal:\[site\]              |
| Maintenance                        | Maint. Supervisor, Technician, Custodian  | Work orders, unit make-ready status, PM schedules; (supervisor) maint. budget                   | ALL financials — purchase price, rent economics, returns, LP data                                       | deal:\[site\]              |
| Construction / CapEx               | Pres./VP Construction, Reno PM, Estimator | Capex budgets, reno schedule, per-unit cost, draws, value-add section                           | Acquisition price negotiation; waterfall; operating rent roll (view-only)                               | portfolio or deal:\[…\]    |
| Controller / CFO                   | CFO, Controller, CAO, Treasury            | All financials incl. waterfall, capital accounts; edit actuals, distributions, waterfall calc   | Acquisition GO commit (advisory); underwriting assumptions                                              | org_all                    |
| Bookkeeper / AP-AR                 | Property/Staff Accountant, AP Clerk       | GL, AP/AR, bank recs, property P&L, budgets; post journal entries                               | LP waterfall & investor capital accounts; distribution approval; purchase-price negotiation; user admin | org_all (finance)          |
| Investor Relations                 | Director Investor Relations               | All LP capital accounts, distributions, waterfall (view); IR CRM edit                           | Edit waterfall calc; underwriting; acquisition GO                                                       | org_all (LP data)          |
| Executive Assistant                | EA, Office Manager, Admin Assistant       | Deal metadata, documents, contacts, calendars, tasks                                            | Financial detail — returns, price, LP financials, waterfall                                             | org_all (non-financial)    |
| Platform / IT Admin                | CTO, IT, IT-delegate                      | Manage users, integrations, audit logs — data-blind by default (separation of duty)             | Viewing deal financials & LP data unless separately granted a data role                                 | org_all (access only)      |
| LP Investor (external)             | Limited Partner                           | Strictly own capital account, own distributions, own K-1s, sanitized updates for their deals    | Every other LP; GP waterfall/promote math; price; operations; deals they’re not in                      | own_only                   |
| Broker / Vendor / Guest (external) | Broker, GC, appraiser, lender             | One invited deal/task; upload OM/bid/comps                                                      | All internal underwriting, returns, price strategy, LP data, other deals                                | single_deal:id, time-boxed |

**Title aliases, not title strings:** permissions key off the functional preset, never the free-text job title — “Portfolio Manager” means fund asset-manager at one firm and regional-ops at another, and “President” spans a two-person shop and a construction subsidiary. Each preset carries a list of aliases so assignment feels natural, but the permission object is what governs.

## **10.4 Permission model & the three explicit answers**

Presets compose from four primitives so the library is maintainable and an org can later clone-and-tweak a preset without bespoke code:

- Module grants — which modules the preset can open: underwriting, comps, granite, ops, rent_roll, capex, accounting, waterfall, lp_portal, documents, outreach, skip_trace, admin.

- Field masks — sensitive fields stripped server-side before serialization: purchase_price, returns_irr, waterfall_promote, lp_pii, debt_terms. A masked field never leaves the API, so a maintenance tech’s browser never receives the purchase price at all.

- Action grants — gated verbs: advance_stage, commit_go_nogo, edit_underwriting, edit_actuals, edit_waterfall, approve_distribution, manage_users, invite_guest, run_skiptrace, send_outreach. Enforced server-side on every request.

- Scope — row visibility: org_all \| portfolio:\[ids\] \| deal:\[ids\] \| own_only \| single_deal:id.

**The three answers the model enforces by construction:** (1) a Maintenance preset has no purchase_price/returns field access and no financial module grant — it cannot see the purchase price; (2) a Bookkeeper preset has the accounting module but not the waterfall module or lp_pii mask clearance — it cannot see the LP waterfall or investor distributions; (3) commit_go_nogo is granted only to the Principal preset — an Analyst can build and recommend but a “move to GO” request returns 403. These are separation-of-duty rules: the GO gate and the waterfall-edit right cannot be self-granted.

## **10.5 Admin & lifecycle — simple to set up, nothing lost when people leave**

- **Point-and-click onboarding:** an org admin creates the org, invites a person by email, and picks a role preset from the library — three clicks, no permission configuration, no IT. The first authenticated user in a new org is auto-promoted to Org Admin; the platform super-admin (Eight Rock) provisions orgs and maintains the preset library and shared reference data.

- **Org-owned configuration (the anti-knowledge-loss design):** buy-box thresholds, KPI targets, saved views, dashboards, and templates live on the organization and on the role presets — never in an individual’s private account. What a departing employee “knew how to set up” is already institutional configuration, visible and editable by the Org Admin.

- **One-click offboarding & handoff:** deactivating a user immediately revokes their sessions, and in the same action reassigns their open deals/tasks to a named successor or the org pool. All of their underwrites, notes, models, POC results, and documents are org-owned and remain fully visible — nothing is stored in a way only that person could reach. A replacement is onboarded by assigning the same preset.

- **Sophistication without complexity:** the default path is pick-a-preset; an Org Admin may optionally clone a preset and adjust module/field/action grants for an unusual hire, and may narrow any member’s scope (e.g., a deal-scoped analyst). Advanced when needed, invisible when not.

## **10.6 Acceptance criteria**

- AC-10.1 A user in Org A cannot read, list, or reference any Org B deal, document, or LP record — verified by an automated cross-org RLS test suite; reference (public) data is readable by both.

- AC-10.2 Field masking holds at the API layer: a Maintenance or Leasing preset never receives purchase_price or returns in any response; a Bookkeeper never receives waterfall/promote or lp_pii — proven by serialization tests, not UI inspection.

- AC-10.3 Only the Principal preset can commit GO/NO-GO; an Analyst attempt returns 403 and is logged. Only Controller/CFO can edit the waterfall. Neither right is self-grantable.

- AC-10.4 An LP sees only their own capital account and distributions and no other LP; a Broker/Guest sees only the single invited deal, with access auto-expiring.

- AC-10.5 Setup test: a non-technical admin creates an org, invites a user, and assigns a working role in under two minutes with zero permission configuration. Offboarding test: deactivating a user revokes sessions, reassigns their work, and leaves all their content org-visible.

# **11. Deterministic Core vs. AI-Augmented Layer (LLM-Optional Architecture)**

Product principle, confirmed with the principal: the Workbench must work as a complete standalone application without ever touching an LLM. The entire underwriting engine, data layer, comps, multi-user collaboration, and admin are deterministic and run with AI turned off. Features that are inherently generative — reading a T-12 PDF into structured data, assembling a memo on the Documents Tab — are isolated as optional AI add-ons, each with a manual or template fallback, so nothing the business depends on is ever gated on an LLM being present, available, or correct. This is the crawl-walk-run structure the firm will build and market against.

## **11.1 Module classification**

| **Layer**                                              | **Modules**                                                                                                                                                                                                                                                                                                                                                                                                                                 | **LLM?**       |
|--------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| Deterministic core (always on, LLM-free)               | Underwriting math (calc, IRR, waterfall, sensitivity, Monte Carlo, exit-cap, refinance, seller-floor, verdict, targets, calibration); comps (Haversine); property spine + municipal sale-history + GRANITE loans + market-data ETL; distress-radar scoring (rules); skip-trace resolution pipeline S1–S5 (deterministic vendor/API calls + validation); compliance gate; Excel/DOCX export; pipeline/CRM; multi-tenancy, roles, admin, auth | No             |
| AI-augmented layer (optional add-ons, feature-flagged) | Document ingestion (T-12/RR/OM parsing, T-12 classifier) — the Documents Tab; memo & artifact assembly (exec summary, IC memo drafting, sponsor-voice); IC-memo narrative validation; acquisition-checklist auto-fill / co-pilot; Inbox→Deal email classification; skip-trace result summarization; white-label brand extraction                                                                                                            | Yes (optional) |

**Note on skip tracing:** Module A’s owner resolution is deterministic — registry lookups, vendor APIs, and validation, not an LLM. Only the optional plain-English summary of the findings is AI. The contact data itself is produced with AI off.

## **11.2 The boundary — how AI plugs in without the core depending on it**

- One-way dependency: AI modules read the structured deal data and write back through the same validated write path the deterministic core uses; the core never imports, calls, or requires an AI module. Delete every AI module and the Workbench still underwrites, comps, exports, and collaborates — the acceptance test literally runs the full suite with the AI layer removed.

- Validated writes only: no raw LLM output is ever persisted unchecked. Every AI extraction or draft passes through the deterministic validators (rent-roll ties to OM, T-12 totals tie to statement, numeric sanity bands) with per-field confidence and low-confidence flags queued for one-click human confirm (ties to SR-3 and Module E).

- Per-org, per-feature flags: ai_enabled and per-capability toggles live on the organization. With AI off, generative surfaces are hidden and the manual/template path is shown instead.

- Manual / template fallbacks for every generative feature: document ingestion ↔ manual entry into the underwriting inputs (you can always type the T-12); exec-summary/IC-memo drafting ↔ a structured template pre-filled with the computed numbers and the deterministic GO/WATCH/NO-GO scorecard, which you complete in your own words; memo validation ↔ the numeric sanity bands still run without AI, only the prose review is skipped.

## **11.3 Crawl · Walk · Run — the build and marketing spine**

| **Stage**            | **What it is**                                             | **Contents**                                                                                                                                      | **LLM**  |
|----------------------|------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| CRAWL — “The Engine” | A stable, standalone, enterprise-grade underwriting engine | Deterministic underwriting, comps, data spine, distress radar, one-click Excel/DOCX export — single or multi-user                                 | None     |
| WALK — “The Team”    | The engine becomes a true multi-user SaaS                  | Organizations, role presets, admin, concurrency control, dedicated-server/cloud deploy, auth (Sections 9–10)                                      | None     |
| RUN — “The Co-Pilot” | AI layered on the stable base as an accelerator            | Document ingestion, memo/artifact assembly, skip-trace synthesis, sponsor-voice, Inbox→Deal, acquisition co-pilot — each independently toggleable | Optional |

## **11.4 Marketing positioning — the category claim**

No other CRE platform separates a fully-functional deterministic underwriting engine from its AI. Incumbents either carry no AI (Yardi/CoStar are data), bolt an AI chatbot onto operations (AppFolio Realm-X, RealPage Lumina, Yardi Virtuoso — all leasing/maintenance/AP), or are the AI with no deterministic guarantee (Primer, Archer — “completely accurate” is an unverifiable marketing claim). The Eight Rock engine produces the same numbers every time with the AI turned off; AI only accelerates input and narrative, never the math. That is trustworthy-by-construction — exactly what an institutional buyer’s security and audit review wants to hear (it is the same story as the deterministic validators in Section 8.1), and it is a claim no competitor can make. The one-line version for the market: “the only multifamily platform whose entire underwriting engine runs with the AI turned off — AI is our accelerator, not our crutch.”

## **11.5 Acceptance criteria**

- AC-11.1 The full deterministic test suite passes with the entire AI layer removed from the build — the Workbench underwrites a deal, pulls comps, scores distress, resolves owner contacts, and exports the workbook end-to-end with no LLM call.

- AC-11.2 With ai_enabled off for an org, no code path issues an LLM request; every generative surface offers its manual/template fallback instead.

- AC-11.3 No AI output reaches persistent deal data without passing the deterministic validators; low-confidence fields are flagged, never silently written.

# **12. Pricing & Positioning**

Anchored to the June 2026 three-tier model, validated against July 2026 verified competitor prices. The wedge: a two-seat small shop today pays \$400–\$500/mo (Reonomy) + \$149–\$249/seat (Terrakotta) + PM software just to approximate half the loop — \$700–\$1,000+/mo across three logins with zero underwriting. The Workbench prices the whole loop inside that envelope.

| **Tier**      | **Price (target)**   | **Includes**                                                                                  | **Displaces**                                       |
|---------------|----------------------|-----------------------------------------------------------------------------------------------|-----------------------------------------------------|
| Solo          | \$99–\$149/mo        | Full underwrite + exec memo + comps + 1 market’s public-data pulls + 25 skip-trace credits/mo | A.CRE templates + manual research                   |
| Operator      | \$299–\$399/mo       | Adds GRANITE radar, pipeline/CRM, Inbox→Deal, outreach engine, 100 credits/mo                 | Reonomy seat + Terrakotta seat (\$550–\$750/mo)     |
| Firm          | \$750–\$1,250/mo     | 3–5 seats, multi-market, full POC intelligence, LP portal, priority support, 400 credits/mo   | The 4-login stack (\$20K–\$40K+/yr)                 |
| Metered layer | \$0.50–\$1.00/credit | Per resolved+stamped owner; per municipal pull; volume packs                                  | Batch skip vendors (\$0.10–\$0.17 raw, unvalidated) |

Defensible because: (a) transparent pricing is itself rare — only Reonomy and Terrakotta publish numbers in the top ten; (b) the metered credit is priced above blended vendor cost (~\$0.15–\$0.75) with room for the compliance/validation value-add; (c) incumbents cannot follow without cannibalizing \$40K/yr contracts (CoStar) or violating their consent decree constraints (RealPage).

# **13. Build Sequence & Verification**

| **Phase**                      | **Scope**                                                                                                                                                                           | **Exit criteria**                                                                          |
|--------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| V5-P0 (weeks 1–6, first)       | Phase 0 data independence (Section 7): 8R spine build → shadow parity → cutover → ALN purge                                                                                         | AC-P0-1 through P0-5 pass; zero-ALN verification sweep                                     |
| V5-P0.5 Pilot Enablement (now) | Section 9: dedicated-server deploy, SQLite→Postgres migration, concurrency control (9.3), Caddy/TLS, Auth0/Entra login + admin page                                                 | AC-9.3 and AC-9.4 pass; external users can log in over HTTPS                               |
| V5-Walk Multi-tenancy & roles  | Section 10: org/data isolation (RLS), role-preset library, permission model, point-and-click admin & offboarding                                                                    | AC-10.1 through AC-10.5 pass                                                               |
| V5-P1 (weeks 1–4)              | Module A pipeline S1–S5 for Hampton Roads (VA SCC + BatchData + Trestle), poc_record store, cost telemetry — runs against the 8R spine as P0 lands                                  | AC-A1/A2/A4 pass on 100-property validation set                                            |
| V5-P2 (weeks 3–6)              | Compliance gate C1–C7, consent/revocation ledger, callable-state enforcement                                                                                                        | AC-A3 pass; audit export reviewed                                                          |
| V5-P3 (weeks 5–9)              | Outreach Engine B1–B5; GRANITE→pierce→dial flow; radar v2 scoring                                                                                                                   | AC-B1–B3 pass; backtest target met                                                         |
| V5-P4 (weeks 8–12)             | Inbox→Deal MVP; extraction QA hardening; anomaly detection                                                                                                                          | Confidence-gated ingest live on Eight Rock mailbox                                         |
| V5-P5 (parallel)               | Data independence track + infra (identity, tenancy, billing) per Module G sequence                                                                                                  | Pre-revenue checklist items from July roadmap closed                                       |
| V5-P6 (post-functionality)     | Security hardening to the Fifth Dimension benchmark (8.1): isolation tests, zero-training enforcement, audit coverage, pen test, trust program → SOC 2 Type I → Type II → ISO 27001 | SR-1 through SR-3 controls implemented; Type I report in hand before first paying customer |

Verification discipline (per project workflow rules): no module marked complete without its acceptance tests passing against real Hampton Roads data; every export diffed against the template baseline to prove formatting/formula preservation; compliance gate covered by an adversarial test suite that attempts to dial unstamped numbers and must fail.

# **14. Completion & Remediation Plan (added 2026-08-03)**

Status audit of Section 13 as of V5.14.7.2.0. Everything below is either a
measured gap or an unbuilt feature; each row states the fix and exactly what
is needed to make it happen. Items not listed here are code-complete with
passing acceptance tests.

## **14.1 Blocked on data or gates (code done, numbers not met)**

| Item | Gap | The fix | What is needed |
|---|---|---|---|
| P0-2 comp overlap | 66.8% vs ≥90% gate | Resume anchor tuning against the nightly parity report once rent signal improves (rent drives comp selection; tuning before rents land re-fits noise) | Owner go-signal (tuning currently parked); no external inputs |
| P0-2 rent delta | 26.9% vs ≤5% gate | Scraped listings rents on backbone rows. The ingest funnel now reports where scrapes die; the binding constraint is city coverage below | More starred favourites in covered cities (owner, in-app); Hampton/Portsmouth/Suffolk backbone rows (14.2) |
| Hampton / Portsmouth / Suffolk backbone | 0 multifamily backbone rows — assessor feeds carry no usable MF data | Hampton: empirical use-code learning + discovery size scoring (shipped, needs a networked discovery run). Portsmouth: numeric use-code learner (shipped, code 18). Suffolk: no public feed found — acquire the parcel roll by FOIA/records request or county GIS contact | A discovery run on the office host (network access to city portals); for Suffolk, one records request — owner signature |
| Flip day (P0-3→P0-4) | Waits on both gates | `core.cutover.migrate_deal_references` against pilot Postgres → `SPINE_READ_SOURCE="8r"` → full regression → purge legacy tables → AC-P0-5 sweep incl. git history | Both gates green; ~half a day of supervised runtime |
| Radar v2 backtest (§6.1) | Backtest code shipped; never run on real deed chains | Run `radar_v2.backtest` over the 7-city deed history as an autopilot step; tune weights until top-decile ≥3× base-rate | Host run only (deed data is already local there) |
| Module A live verification (AC-A1) | Pipeline verified on mocks only | Run `diagnose-skiptrace.bat` on the host; fix `core/skiptrace/live.py` field mapping against real BatchData responses; verify the VA SCC scraper from a non-firewalled network; then the 100-property validation run | Owner double-click on the host; BatchData key already entered |

## **14.2 Blocked on owner accounts (code shipped, wiring pending)**

| Item | The fix | What is needed |
|---|---|---|
| OIDC login live (AC-9.4) | Fill `.streamlit/secrets.toml` `[auth]` from an Auth0 (or Entra) app registration; verify external login + pending-approval end to end | Owner creates the Auth0 app (free tier); 15 minutes |
| Public HTTPS (§9) | Port-forward TCP 80/443 on the Cox gateway → rerun `install-caddy.ps1` (its MISMATCH check is the verification) | Owner router step (runbook: `deploy/windows/README.md`) |
| Inbox→Deal live (§6.2) | Connect the Eight Rock mailbox via Microsoft Graph consent; engine and confidence gate are built | Owner grants Graph consent to the app registration |

## **14.3 Not yet constructed (ordered by leverage)**

| Item | The fix | What is needed |
|---|---|---|
| B1/B3 telephony (§5.1) | Twilio-family integration behind a `DialerProvider` interface (same vendor-abstraction pattern as skip trace): click-to-call, local-presence caller ID, call logging, compliance hard-block on prerecorded drops | Build (~1 week) + a Twilio account + counsel sign-off on TCPA flow before first real dial |
| AC-B3 mail batch | Batch letter PDF generation (500 in ≤10 min) with per-piece QR/phone tracking; lob.com API handoff optional | Build (~3 days); Lob account only if API fulfillment wanted |
| User-added properties + verified badge | Section 16 (new) | Build starts now |
| 50-metro expansion | Section 15 (new) | Per-metro host runs; no new vendors |
| Module G commercialization (§6.5) | Stripe billing + usage meters, per-tenant encryption keys/cryptoshred, SAML/SCIM, dual-region/PITR/status page, Next.js+FastAPI re-platform | Post-pilot program, sequenced only after first external users |
| §8.1 attestations | Pen test → SOC 2 Type I → Type II → ISO 27001 via a trust platform (Vanta/Drata-class) | Budget + vendor selection; Type I gates first paying customer |

# **15. Geographic Expansion — the 50-Metro Rollout (added 2026-08-03)**

Ordering principle: **thesis-first, data-quality tie-broken.** Markets where
Eight Rock would actually buy (Mid-Atlantic/Southeast value-add, then Sunbelt)
come first, so every new metro is immediately huntable; within a wave, cities
with strong open parcel data (ArcGIS/Socrata) ship before difficult ones. The
50 metros, in deployment order:

| Wave | Metros | Target cadence |
|---|---|---|
| **1 — Virginia adjacency** | 1 Richmond · 2 Charlottesville · 3 Roanoke · 4 Lynchburg · 5 Fredericksburg | 2–3/week |
| **2 — Carolinas + DMV** | 6 Raleigh–Durham · 7 Charlotte · 8 Greensboro/Winston-Salem · 9 Fayetteville NC · 10 Wilmington NC · 11 Columbia · 12 Charleston · 13 Greenville–Spartanburg · 14 Washington DC (NoVA) · 15 Baltimore | 3–5/week |
| **3 — Southeast** | 16 Atlanta · 17 Jacksonville · 18 Orlando · 19 Tampa–St. Pete · 20 Savannah · 21 Augusta · 22 Birmingham · 23 Huntsville · 24 Nashville · 25 Knoxville · 26 Chattanooga · 27 Memphis · 28 Louisville · 29 Lexington | 5/week |
| **4 — Texas + heartland** | 30 Dallas–Fort Worth · 31 Houston · 32 San Antonio · 33 Austin · 34 Oklahoma City · 35 Tulsa · 36 Little Rock · 37 Kansas City · 38 St. Louis · 39 Indianapolis · 40 Columbus OH · 41 Cincinnati | 5–8/week |
| **5 — Growth West + fill** | 42 Phoenix · 43 Las Vegas · 44 Denver · 45 Salt Lake City · 46 Boise · 47 Albuquerque · 48 Tucson · 49 Colorado Springs · 50 Pittsburgh | 8–10/week |

**First non-Hampton-Roads metro: Richmond**, target **live within 2 weeks of
the go-signal** — week 1: automated feed discovery + pull + spine build on the
host (deed chains for Richmond are already shipped); week 2: parity tuning
from nightly reports. Charlottesville follows immediately (deeds also
shipped).

**Why the cadence ramps instead of starting at 10/week:** the Hampton Roads
scaling playbook (CLAUDE.md) exists because every city broke the pipeline
differently — geometry formats, use-code vocabularies, split addresses,
wrong-city layers. Wave 1 exercises those rules on friendly data; each wave
hardens the pipeline for the next. The end-state goal stands: a new metro
onboards hands-free from `discover → pull → phase0 → validate` with no
human round-trips.

**Per-metro definition of done** (the P0-1 gate, generalized): parcel +
geometry coverage ≥95% of the city's stated parcel count; multifamily
classification via token rules with the top use codes printed for review;
unit counts from explicit fields or address-point multiplicity; deeds where
the county publishes them; rent signal from HUD FMR at minimum; the metro
registered in the Section 16 validation-capability table.

**Gating item carried forward from §6.4:** counsel review of each public
source's terms remains required before any metro's data is sold externally —
internal underwriting use proceeds without waiting.

# **16. User-Added Properties & the Verified Badge (added 2026-08-03)**

The backbone will never be complete — new construction, conversions, and the
sub-10-unit tail. Users must be able to add a property in under a minute, and
everyone who later sees that property must know whether its core facts were
independently confirmed. The badge is the product: a **blue check** in the
Meta/X sense — visible, binary, earned by verification against records the
user does not control.

## **16.1 Add flow**

- **"+ Add property"** on the Properties surface: community name, street
  address, city (required); unit count (required); parcel/tax ID and property
  website (optional but accelerate verification).
- The property receives a provisional 8R id (`8R-{FIPS}-u{hash}`) and is
  **immediately usable by the submitting org** — full Property Card,
  underwriting, documents — wearing a grey **Unverified** badge.
- It joins the **shared reference layer and comp sets only after
  verification** (§10.1 tenancy rule: user-supplied rows must not pollute
  other tenants' comps unverified).

## **16.2 Core data elements & the bar**

| Element | Verified how | Bar |
|---|---|---|
| Address | Geocode + normalized match against municipal parcel/address-point records | **Exact** (normalized) |
| Parcel / tax ID | Match in the municipality's assessor roll; where the user omits it, reverse-lookup by address | **Exact** |
| Unit count | Assessor unit field, or address-point multiplicity per parcel (the §7 rule), or licensed-unit registries where published | **±10%** or multiplicity-confirmed |
| Community name | Soft match against the property's own website/listing page (municipal rolls do not carry marketing names) | **Soft** — mismatch flags, does not fail |

**Badge = address AND parcel exact, AND units within tolerance.** Name
mismatches annotate the record. A hard mismatch on any exact element fails
verification **with the reason shown to the submitter** (e.g. "parcel
12345-67 is a 6-unit per Norfolk assessor; you entered 48").

## **16.3 Municipality-specific validation (the capability table)**

Validation strength is a property of the municipality, not the platform, so
each city registers what it can prove: `parcel_roll` (full assessor match),
`address_points` (unit multiplicity), `none` (no public feed — e.g. Suffolk
today). A city at `none` can still verify address via geocoding but cannot
award the badge; those submissions queue as **Pending — awaiting municipal
data**, and the badge lands automatically when the city's feed does (the
nightly cycle re-validates the queue). Where no API/feed exists, fallbacks in
order: municipal assessor web lookup (browser automation on the host),
property-site scrape for name/unit corroboration, manual review.

## **16.4 Badge states & lifecycle**

| State | Render | Meaning |
|---|---|---|
| Unverified | grey outline check | Submitted; validation not yet run or city at `none` |
| Pending | grey pulsing | In the nightly validation queue |
| **Verified** | **blue filled check** | Core elements confirmed against municipal records; timestamp + source shown on hover |
| Failed | red outline + reason | A core element contradicts the municipal record |

Re-validation runs on every municipal data refresh; a verified property whose
core elements drift (parcel retired, unit count changes) drops to Failed with
the diff shown — the badge is a living claim, not a one-time stamp.

## **16.5 Acceptance criteria**

- **AC-16.1** A user can add a property and use it for underwriting in ≤60
  seconds, and its badge state is visible on every surface where the
  property appears (card, inventory, comps, exports).
- **AC-16.2** No user-added property enters another org's comp set without a
  blue check (adversarial test: attempt it; must fail).
- **AC-16.3** Every badge decision stores the evidence — source, matched
  values, timestamp — and renders it on demand (the audit answer to "why is
  this verified?").
- **AC-16.4** A seeded wrong-unit-count submission in a `parcel_roll` city
  fails with the municipal count named; the same submission in a `none` city
  parks as Pending, never Verified.

# **Appendix A — Source Notes**

Security benchmark (8.1): fifthdimensionai.com/security, reviewed July 21, 2026 — SOC 2 Type II, ISO 27001, GDPR; per-customer isolated instances; zero-data-training policy; EEA/US data residency; full operation-level audit logging.

Role taxonomy (Section 10): synthesized July 21, 2026 from the public About/Team/Careers pages of small-to-mid multifamily firms — including eight-rock.com, theblvdgrp.com, fpamf.com, bainbridgecompanies.com, m2regroup.com, rise48equity.com, wildhorncap.com, and peers — to ground the role presets in how these businesses are actually staffed (owner through maintenance tech and external LP).

Deployment & auth (Section 9): Streamlit native OIDC auth (st.login, native since Streamlit 1.42, Feb 2025) — docs.streamlit.io; Auth0 free tier 25,000 MAU with Google/Microsoft/email-password and hosted user management — auth0.com/pricing; Microsoft Entra External ID free MAU allowance — aka.ms/ExternalIDPricing; Caddy automatic HTTPS — caddyserver.com; PostgreSQL — postgresql.org. Reviewed July 21, 2026.

Phase 0 inventory: read-only scan of the production workbench (python_workbench, data/workbench.db, deal folders) executed July 21, 2026 via the connected device session; row counts and code-reference counts in Section 7.1 are measured, not estimated.

Primary (verified): Reonomy pricing email to Eight Rock, Altus Group, July 18, 2026 (\$500/mo monthly, \$400/mo annual, +\$75/mo 1K exports). Yardi Matrix AE correspondence (Paul Serra, June–July 2026) confirming 50+ unit coverage floor. Terrakotta trial signup + demo email (July 17–20, 2026). Eight Rock internal: Strategy Brief 6/3/2026; Strategic Summary 6/2026; Progress & Roadmap 7/13/2026 (v2.4.1).

Secondary (web research, July 20, 2026): yardi.com (Virtuoso release 6/15/2026; Matrix product), yardibreeze.com pricing, costar.com product pages, propertyscout360.com CoStar cost analysis, Bisnow/BergerMontague CoStar class-action coverage (4/2026), appfolio.com pricing + Realm-X releases, checkthat.ai AppFolio review aggregation, realpage.com Lumina releases, justice.gov RealPage consent decree (11/24/2025), Multifamily Dive/Inman Cherre acquisition (7/14/2026), buildium.com pricing, credaily.com reviews (Terrakotta, Reonomy, data sources), terrakotta.ai + YC profile, eliseai.com Series E (8/2025), rediq.com + Radix acquisition release (8/26/2024), proprise.ai/primer, dealpath.com plans + AI Studio (10/14/2025), gracehill.com HelloData acquisition (5/6/2025), batchdata.io pricing/benchmarks, dealmachine.com pricing, trestleiq.com pricing, endato.com→Enformion pricing, cobaltintelligence.com, opensosdata.com, opencorporates.com pricing, tcpalitigatorlist.com packages, FCC/Goodwin/Hunton TCPA 2025–2026 rule tracking (one-to-one consent vacatur, revoke-all extension to 1/31/2027), skipreach.com FCRA guidance, A.CRE Summer 2026 AI tools survey.

*This document contains competitive intelligence and forward-looking product plans. Internal use only — Eight Rock Capital Partners. Data-licensing, telemarketing-compliance, and PI-licensing items require counsel review before commercial launch; nothing here is legal advice.*
