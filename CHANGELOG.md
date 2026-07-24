# Changelog — Eight Rock Workbench v5.0

Version scheme: **`V5.PHASE.FEATURE.PATCH.BUILD`**
- leading **5** = the Workbench v5.0 product line (fixed)
- **PHASE** = build-sequence milestone (0 = P0/P0.5 pilot, 1 = Walk/multi-tenancy, …)
- **FEATURE** = notable capability added within the phase
- **PATCH** = fixes / refinements
- **BUILD** = increments on every change

The displayed version lives in `config.py` (`WORKBENCH_VERSION`) and shows in the
app's top-bar pill. **Bump it and add an entry here on every change.**

---

## V5.3.2.0.0 — 2026-07-24  ·  Module C: Forced-Seller Radar v2 (§6.1)
- **`core/radar_v2.py`**: one explainable 0-100 distress score fusing six
  weighted signals — loan-maturity proximity (GRANITE), tax delinquency, permit
  decay, ownership tenure, listing appearance/removal, and the **new v5.0 POC
  signals** from Module A (deceased-owner flag, out-of-state mailing address,
  portfolio size, entity dissolution). Every component carries its evidence.
- Weights are tuned so **no single signal alone reaches the ACT band** — distress
  is a fusion judgement, proven by test.
- **Backtest harness** (`backtest()`): top-decile trailing score vs. population
  base rate. Synthetic 600-property run yields **3.08x lift** (base 10.8% ->
  top-decile 33.3%), clearing the spec's >=3x acceptance bar.
- **`ui/radar_panel.py`** on the Subject tab: score badge + per-component
  contribution bars + the full evidence list; signal inputs are overridable
  until Phase 0 / GRANITE wire them automatically.
- Tests (`tests/test_radar_v2.py`): 18 passing.

## V5.3.1.0.0 — 2026-07-24  ·  V5-P3: Module B Outreach Engine B1-B5 (§5)
- **B2 artifacts** (`core/outreach/artifacts.py`): letters + call talking points
  grounded in deed chain / GRANITE loan maturity / assessed-value trend /
  portfolio context. Deterministic templates (no LLM); `validate_polish()`
  rejects any AI rewrite that adds or drops a grounded number (§11 AC-11.3).
- **AC-B3 direct mail**: `build_letter_batch` dedupes by normalized
  (owner, address) and exports print-ready HTML + mail-merge CSV. Verified:
  500 letters + 25 dupes removed in well under the 10-minute bar.
- **B4 cadence** (`core/outreach/cadence.py`): call -> letter -> call -> email
  with dated steps, campaign lifecycle, and automatic pause on opt-out or a
  terminal deal stage.
- **B1/B5 UI** (`ui/outreach_panel.py`, on Due Diligence + the CRM module):
  call list of ONLY callable numbers with a live gate pre-check per row, blocked
  numbers shown with their reason, letter-batch builder, AC-B2 audit log with
  CSV export, and opt-out capture. No prerecorded/AI-voice button exists — the
  UI explains the §4.4 C3 stance instead.
- Tests (`tests/test_outreach.py`): 11. Fixed a real bug the tests caught —
  `.capitalize()` was destroying "HUD"/"March" in talking points.

## V5.3.0.0.0 — 2026-07-24  ·  V5-P2: Compliance gate C1-C7 + outreach chokepoint (§4.4/§5)
- **Schema**: `consent_records`, `revocations`, `internal_dnc`, `dnc_scrubs`,
  `outreach_touches` (append-only, with `rule_trace`), `campaigns`,
  `relationship_edges` — all RLS org-private.
- **`core/compliance/rules.py`** — the gate. C1 DNC (internal list + 31-day scrub
  freshness + federal + the six state registries), C2 litigator, C3 channel rules
  (prerecorded/AI-voice/RVM/SMS to a cell HARD-BLOCKED without prior express
  written consent), C4 quiet hours 8:00-21:00 in the *called-party's* local time
  (area-code -> state -> conservative all-zone fallback) + per-person frequency
  caps incl. the Oregon 3/day overlay, C5 revocation honored across ALL channels,
  C6 FCRA firewall (non-acquisition purposes refused outright), C7 licensing.
  Returns a full **rule trace**.
- **`core/compliance/ledger.py`** — consent / revocation / internal-DNC / scrub
  ledgers; an opt-out immediately revokes consents and adds to internal DNC.
- **`core/outreach/engine.py`** — every touch routes through `attempt_touch`,
  which evaluates the gate, logs the attempt WITH its trace (allowed or blocked),
  and dispatches only if allowed. Dial list exposes only `callable` numbers (B1).
  Audit export (`export_touches_csv`) satisfies AC-B2. Relationship edges (B5).
- **Tests** (`tests/test_compliance.py`): 21 ADVERSARIAL tests per the §13
  verification rule — unstamped/expired/federal-DNC/state-DNC/litigator/internal-
  DNC/prerecorded-to-cell/SMS/quiet-hours/unknown-geography/revoked/FCRA/managed-
  service/frequency-cap all refused; blocked touches still logged and never
  dispatched; touch log proven append-only.

## V5.2.1.3.0 — 2026-07-24  ·  Robust BatchData key setter (clean PS script)
- `set-skiptrace-key.bat` now calls `deploy/windows/set-skiptrace-key.ps1` (a
  clean, reliable script instead of an inline one-liner). Writes the key to .env
  idempotently, prints a masked confirmation, and reminds you to RESTART the app
  (the provider status is read at startup).

## V5.2.1.2.0 — 2026-07-24  ·  Double-click helpers for skip-trace setup
- `set-skiptrace-key.bat`: prompts for the BatchData key and writes it to .env
  correctly (idempotent, enables live mode). `diagnose-skiptrace.bat`: prompts
  for owner name + address and runs the diagnostic. Both cd into the project, so
  no wrong-directory / "type it as a command" mistakes.

## V5.2.1.1.0 — 2026-07-24  ·  Skip-trace live diagnostic + VA-SCC verification path
- `scripts/diagnose_skiptrace.py`: run ON THE SERVER (real internet + keys) to
  dump raw + parsed vendor responses per provider — used to verify/tune the live
  adapters (esp. the VA SCC CIS scraper, which the firewalled build env can't hit).
- `.env.example`: documents the public market-data key names (Census/FRED/BLS/HUD)
  for the Phase 0 spine — names only, never values.

## V5.2.1.0.0 — 2026-07-24  ·  Module A live vendor adapters (BatchData/Trestle/VA-SCC)
- `core/skiptrace/live.py`: real HTTP adapters behind the §8 provider interfaces —
  **BatchData** (skip trace: phones/emails/DNC), **Trestle** (validation +
  litigator/name-match grading), **VA SCC** (entity -> officers piercing).
- `get_registry()` "live" mode mixes live+mock **per provider** by env key, so you
  go live one vendor at a time (add BATCHDATA_API_KEY -> skip trace is real while
  SOS/validation stay mock). Panel shows a live/mock status line per provider.
- `.env.example`: documents ER_SKIPTRACE_PROVIDERS + the three vendor keys.
- Tests (`tests/test_skiptrace_live.py`): 7 — HTTP-mocked payload parsing
  (BatchData/Trestle), E.164 normalization, DNC->non-callable, registry mixing,
  and a full pipeline run on live-mocked BatchData. 19/19 skiptrace tests green.
- Real vendor calls cost money per request; the FR-A5 budget cap still guards spend.

## V5.2.0.1.0 — 2026-07-24  ·  Double-click launcher + updater (no more cd trap)
- `start-workbench.bat` (launch) and `update-workbench.bat` (git sync + uv sync +
  migrate) — both `cd /d` into the project first, so they work from any folder.
  Eliminates the recurring "wrong directory" failures on Windows.

## V5.2.0.0.0 — 2026-07-24  ·  Module A — Skip Trace & POC Intelligence (Section 4)
- **Deterministic resolution pipeline S1–S7** (`core/skiptrace/pipeline.py`):
  entity anchor -> portfolio chain -> LLC piercing (registry, recurse to depth 4)
  -> person skip-trace waterfall (cheapest tier first, stop on grade-A) ->
  validation & A/B/F grading -> compliance stamp -> persist. No LLM (Section 11).
- **Vendor abstraction** (`core/skiptrace/providers.py`, spec §8): SOS /
  SkipTrace / Validation provider interfaces with deterministic MOCK adapters
  (zero spend, repeatable). Swap to real BatchData/Trestle/VA-SCC via
  `ER_SKIPTRACE_PROVIDERS=live` — pipeline code unchanged.
- **Compliance gate (AC-A3)**: `callable` is computed in exactly one place from a
  valid, unexpired DNC stamp + litigator/federal-DNC check; nothing else can mark
  a number callable. **Cost telemetry + hard monthly budget cap (FR-A5/AC-A4)**.
- **Owner Intelligence panel** (`ui/skiptrace_panel.py`, on the Diligence tab):
  one-click Resolve Contacts (FR-A1), entity-chain display, graded phones with
  red-lock reasons for blocked numbers, provenance + spend. Gated by the
  `skip_trace` module grant / `run_skiptrace` action (§10.4).
- Tests (`tests/test_skiptrace.py`): 12 — AC-A1 (>=80% resolve to a named human
  with a grade-A phone), AC-A3 (callable invariant), AC-A4 (spend + budget cap),
  LLC piercing, waterfall stop, idempotent persist, §4.5 contract shape.
- **Fixes**: "Preview as role" now degrades gracefully instead of white-screening
  on a stale module (defensive `apply_preview`); the comps "Refresh All" ETL
  control shows a notice instead of a red error (the standalone ETL script isn't
  part of the v5 layout — refresh lands with the Phase 0 spine).

## V5.1.2.0.0 — 2026-07-24  ·  §10.4 enforced in the deal screens + "Preview as role"
- **Module gating live in the UI** (`ui/authz.py` + `app.py`): Underwriting,
  Due Diligence, Returns & Waterfall, Investors, Exec Summary, Market, Pipeline,
  and Portfolio each render only for roles whose preset carries the module
  grant — everyone else gets a lock notice. This is the spec's rule made real:
  *a Maintenance preset cannot see the purchase price* — the financial
  renderers are never invoked for that role.
- **Field masking helpers** (`authz.mask`/`authz.scrub`) for shared surfaces.
- **👁 Preview as role** (admin sidebar): see the workbench exactly as any of
  the 18 presets sees it — pick "Maintenance" and watch Underwriting lock.
  Real permissions untouched; admin panel stays reachable.
- Tests: 5 new DB-free unit tests (`tests/test_authz_ui.py`); verified via
  AppTest that Principal sees the purchase-price input and a Maintenance
  preview gets 6 lock notices and no purchase price anywhere. 21/21 green.

## V5.1.1.3.0 — 2026-07-24  ·  Remove legacy "Try V2.0" theme switcher
- Removed the floating "Try V2.0" pill (and its unused import). "V2.0" was an old
  internal UI-theme codename ("Quiet Operator"), not a product version, and read
  as a downgrade offer in the v5 product. Users no longer switch themes.

## V5.1.1.2.0 — 2026-07-24  ·  Fix migrate-db.ps1 arg parsing (Windows psql)
- Windows `psql` stops parsing options after a bare connection URL, so `-f` was
  dropped and the script fell into an interactive prompt. Pass the URL via `-d`
  so all options parse. Verified against PostgreSQL 16.

## V5.1.1.1.0 — 2026-07-24  ·  DB migration script + graceful schema-drift handling
- **Fix**: after V5.1.0 moved `memberships` out of RLS, a DB that wasn't
  re-migrated raised `InsufficientPrivilege` on org creation. Added
  `deploy/windows/migrate-db.ps1` — applies `db/pilot_schema.sql` using the
  existing `.env` credentials (no admin, no superuser password); idempotent.
- `core/session.py` / `app.py`: `resolve_org_context` now catches DB errors and
  the app shows a soft "run migrate-db.ps1" banner instead of white-screening.
- Verified: reproduces the error on the old schema; migration disables the stale
  RLS; org creation then succeeds.

## V5.1.1.0.0 — 2026-07-24  ·  Org context + point-and-click org admin
- `core/session.py`: `resolve_org_context` — resolves the logged-in user's
  active org + effective permissions; admins auto-bootstrap a default org (become
  Principal) so the org/role model is live with zero setup.
- `app.py`: stores `org_id` + `perms` in session; passes org to the admin page.
- `ui/admin.py`: new **Organization & roles** tab — list members, assign a role
  preset from the library (point-and-click), offboard/reactivate, add existing
  users to the org. Every change audit-logged.
- `core/orgs.py`: `list_presets`, `ensure_default_org` helpers.
- Verified: admin panel renders both tabs; normal load clean; 16/16 tests pass.

## V5.1.0.0.0 — 2026-07-24  ·  Walk: Multi-tenancy & Roles (Section 10)
- **Permission model** (`core/permissions.py`): effective permissions from a
  role preset — module grants, server-side field masking (`mask`/`scrub` so a
  masked field never reaches the client), action gating (`require` → 403), and
  scope parsing.
- **Org & membership lifecycle** (`core/orgs.py`): create org (creator becomes
  Principal/org-admin), invite/assign by picking a preset key, change preset/
  scope/status, one-click offboarding, `user_orgs` login resolution, and
  `get_permissions(user, org)`.
- **Schema**: `memberships` moved out of org RLS to control-plane (fixes the
  login-time "which org?" bootstrap); org-private DATA tables stay RLS-forced.
- **Tests** (`tests/test_multitenancy.py`): 8 new — AC-10.2 (field masking),
  AC-10.3 (separation of duty: GO gate + waterfall-edit not self-grantable),
  AC-10.4 (LP own_only / guest single_deal), AC-10.5 (setup + offboarding).

## V5.0.2.0.0 — 2026-07-24
- Adopt the `V5.x.x.x.x` version scheme; version pill now reads `V5.0.2.0.0`.
- Add this changelog; record the "bump version + changelog every change" rule in
  `CLAUDE.md`.

## V5.0.1.0.0 — 2026-07-24
- **App boots + is login-gated.** `core/session.py` resolves the user (legacy
  ungated / OIDC / `ER_DEV_LOGIN` dev bypass); `app.py` renders the account chip
  and routes admins to the admin panel.
- `data/db.py`: boot with an empty schema-only inventory when no ALN data exists
  (instead of crashing).
- **`ui/admin.py`** admin page + **`core/oidc.py`** native-OIDC login gate
  (Section 9.4); **`data/concurrency.py`** optimistic-save + soft locks (§9.3).
- `scripts/seed_demo_properties.py`: 12 Hampton Roads demo properties so a fresh
  deploy is testable before Phase 0.
- `docs/ACCESS.md`: how to log in (dev mode + real Auth0/Entra sign-in).
- Fixed a pre-existing crash in the V1 no-property path (deleted-tab reference).

## V5.0.0.0.0 — 2026-07-23
- Repo established (`EightRockDev/WORKBENCH_v5`), seeded from the v2.4.1
  underwriting engine — separate from GRANITE.
- **Section 9 deployment stack**: Linux (`deploy/`) + Windows (`deploy/windows/`)
  — Caddy config, systemd/NSSM services, install + DB-setup + backup scripts.
- **`db/pilot_schema.sql`** PostgreSQL 16 pilot schema: organizations, users,
  memberships, 18-preset role library (§10), row-level-security tenant isolation
  (§10.1), optimistic-concurrency `row_version` + `edit_locks` (§9.3),
  `poc_records` (§4.5), `skiptrace_spend`, append-only `audit_log` (§8.1).
- `data/pg.py` Postgres connection helper with per-tenant RLS context.
- Build packet: `docs/spec/workbench-v5.0-spec.md` (full spec) +
  `docs/spec/BUILD-ORDER.md` (phase-by-phase build order).
- `CLAUDE.md` project memory.
- Pilot acceptance tests (`tests/test_pilot_admin.py`) — 8/8 passing (AC-9.3, AC-9.4).
