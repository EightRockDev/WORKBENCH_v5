# Eight Rock Workbench v5.0 — Build Order & How to Prompt Claude Code

**Read this before starting any build session.** It is the cover page for
[`workbench-v5.0-spec.md`](workbench-v5.0-spec.md) — the full, authoritative
specification.

---

## Ground rules

1. **Single source of truth = `workbench-v5.0-spec.md`** (the *complete* spec:
   data contracts, module classifications, the permission matrix, vendor
   selections, and acceptance criteria). Do **not** build from any summary/
   memory `.md` — those are compressed notes for humans and lack the detail
   needed to write correct code.

2. **One phase per work order.** Do **not** hand the whole 31-page spec to a
   single build session — that produces a sprawling, half-finished mess. Work
   the phases below **in order**, one at a time. Finish and verify a phase
   before opening the next.

3. **Honor the acceptance criteria — they are the definition of done.** No
   module is "complete" until its ACs pass. Per Section 13's verification
   discipline: test against **real Hampton Roads data**, diff every Excel
   export against the template baseline to prove formatting/formula
   preservation, and cover the compliance gate with an adversarial test suite
   that *attempts* to dial unstamped numbers and must fail.

4. **Deterministic core first, AI last (Section 11).** The underwriting engine,
   data layer, comps, collaboration, and admin must run with the AI layer fully
   removed (AC-11.1). Build/keep them LLM-free. Generative features are the RUN
   stage: each is feature-flagged (`ai_enabled`), passes deterministic
   validators before any write (AC-11.3), and ships a manual/template fallback.

5. **Data independence is a hard prerequisite (Section 7).** Nothing ALN-sourced
   may remain discernible. Phase 0 runs first and everything after builds on the
   clean 8R spine.

6. **When a phase says "load Sections X, Y":** read exactly those sections of
   `workbench-v5.0-spec.md` plus this page — not the whole document.

---

## Build sequence (from Section 13)

Work top-to-bottom. Each row is one work order. "Load" = the spec sections to
put in Claude Code's context for that order.

| Order | Phase | Load these sections | Deliverable | Done when (acceptance) |
|---|---|---|---|---|
| 1 | **V5-P0 — Data independence** | §7 (all), §6.4 (Module F) | 8R property spine build → shadow parity → cutover → ALN purge | **AC-P0-1 … AC-P0-5**; case-insensitive `aln` sweep across repo/db/UI/prompts/exports/git-history returns zero hits |
| 2 | **V5-P0.5 — Pilot enablement** ⬅ *in progress* | §9 (all), §8 (arch) | Dedicated-server deploy, SQLite→Postgres migration, concurrency control, Caddy/TLS, Auth0/Entra login + admin page | **AC-9.3** (two-browser conflict test) and **AC-9.4** (external login + pending-approval gate) |
| 3 | **V5-Walk — Multi-tenancy & roles** | §10 (all) | Org/data isolation (RLS), role-preset library, permission model, point-and-click admin & offboarding | **AC-10.1 … AC-10.5** (cross-org RLS, field-mask serialization tests, GO/waterfall separation of duty) |
| 4 | **V5-P1 — Module A: Skip Trace S1–S5** | §4.1–4.3, §4.5, §4.6 | Resolution pipeline S1–S5 for Hampton Roads (VA SCC + BatchData + Trestle), `poc_record` store, cost telemetry | **AC-A1, AC-A2, AC-A4** on the 100-property validation set |
| 5 | **V5-P2 — Module A: Compliance gate** | §4.4, §4.6 | Compliance gate C1–C7, consent/revocation ledger, callable-state enforcement | **AC-A3** (zero callable numbers without a valid stamp; adversarial dialer test) |
| 6 | **V5-P3 — Module B + Radar v2** | §5 (all), §6.1 | Outreach Engine B1–B5; GRANITE→pierce→dial flow; distress-radar v2 scoring | **AC-B1 … AC-B3**; radar backtest target (top-decile ≥3× base-rate) |
| 7 | **V5-P4 — Inbox→Deal + Doc AI** | §6.2, §6.3 | Inbox→Deal MVP; extraction QA hardening; rent-roll anomaly detection | Confidence-gated ingest live on the Eight Rock mailbox |
| 8 | **V5-P5 — Platform infra** (parallel) | §6.5 (Module G) | Identity/IdP, tenancy isolation, saved-model library, reliability, billing/usage meters | Module G sequence items closed |
| 9 | **V5-P6 — Security hardening** (post-functionality) | §8.1 (all) | Isolation tests, zero-training enforcement, audit coverage, pen test, SOC 2 Type I→II, ISO 27001 | **SR-1 … SR-3** controls; Type I report before first paying customer |

**Cross-cutting (every phase):** honor Section 11 (LLM-optional) — keep the core
deterministic; and Section 12 pricing/tiering informs `plan_tier` gating.

---

## What is already done (start Phase 0.5 from here, don't redo)

The pilot **deployment stack and tenancy spine** are already built and verified
in this repo — the infrastructure half of orders 2–3:

- `deploy/install.sh`, `deploy/Caddyfile`, `deploy/workbench.service`,
  `deploy/backup.sh` — the Section 9.1 install/serve/backup stack.
- `db/pilot_schema.sql` — Postgres schema with: `row_version` +
  `bump_row_version()` and `edit_locks` (§9.3); `users`/`organizations`/
  `memberships`/`role_presets` (§9.4, §10.2); **18-preset role library seeded**
  (§10.3); **row-level-security org isolation** (§10.1, AC-10.1 verified);
  `poc_records` (§4.5); `skiptrace_spend` telemetry; append-only `audit_log`
  (§8.1). Applies clean on PostgreSQL 16.
- `data/pg.py` — connection helper that sets the per-request `app.current_org_id`
  RLS context.
- `.streamlit/secrets.toml.example`, `docs/SETUP.md` — OIDC config + runbook.

**Remaining for order 2 (V5-P0.5):** the SQLite→Postgres migration of the
existing v2.4.1 app tables, the FR-9.3.1/9.3.2 optimistic-concurrency *save
path + conflict UI* wired into the app, and the FR-9.4.1–9.4.4 **admin page**
(users table UI, invite/approve, role assignment, pending-approval gate)
against the schema above.

> **Note on build order vs. the spec's own tension:** Section 13 lists P0 (data
> independence) first, but the office-server pilot work (P0.5) is what's active
> right now. Both are near the front and P0.5's infra doesn't depend on the ALN
> purge, so building the pilot stack first is fine — just don't mark the app
> "cut over" until Phase 0's clean spine lands underneath it.

---

## How to open a work order (template)

> Build **[Phase name]** from the Eight Rock Workbench v5.0 spec.
> Source of truth: `docs/spec/workbench-v5.0-spec.md`, Sections **[X, Y]** only.
> Deliverable: **[from the table above]**.
> Definition of done: **[acceptance criteria IDs]** — write the tests and make
> them pass against real Hampton Roads data before marking complete.
> Constraints: keep the deterministic core LLM-free (§11); preserve template-true
> Excel export and diff it; do not touch ALN data paths except per §7.
> Do not start any later phase in this session.
