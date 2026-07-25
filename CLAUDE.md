# CLAUDE.md — Eight Rock Workbench v5.0

Project memory for Claude sessions. Loaded automatically on startup. Read this
first; it captures what this project is, how the owner works, and where the
build currently stands so you can continue without re-asking.

## What this is

Eight Rock Workbench **v5.0** — a full-loop multifamily acquisition platform
(skip-trace/POC intelligence, compliant outreach, underwriting, multi-tenant
SaaS). It is the productization of the working **v2.4.1** internal underwriting
engine (Python/Streamlit), which this repo was seeded from.

- **Authoritative spec:** `docs/spec/workbench-v5.0-spec.md` (full document).
  **Never** build from a summary `.md` — it lacks the detail.
- **Build order & how to work:** `docs/spec/BUILD-ORDER.md`. Build **one phase
  at a time** in Section 13 order; honor each phase's **acceptance criteria** as
  the definition of done; keep the **deterministic core LLM-free** (Section 11).

## Hard rules

- **This is a separate repo from GRANITE.** Never build v5.0 work into GRANITE.
  Repo: `github.com/EightRockDev/WORKBENCH_v5`, default branch `main`.
- **PowerShell scripts must be pure ASCII.** Windows PowerShell 5.1 reads `.ps1`
  as ANSI; em/en-dashes and smart quotes corrupt and break parsing. No `—`, `–`,
  `“`, `”` in any `.ps1`.
- **Never commit secrets.** `.env` (DB connection) and `.streamlit/secrets.toml`
  are gitignored and must stay that way.

## Deployment target & owner context

- **Pilot host:** an always-on **Windows** server. The app lives at
  **`C:\WORKBENCH_V5`** (local disk — never OneDrive, per spec §9.2).
- **Owner is non-technical on infra.** Give exact, copy-pasteable, one-thing-at-
  a-time steps. Known traps to pre-empt every time:
  - After any installer, **open a NEW PowerShell window** before using the tool
    (PATH isn't refreshed in the existing window).
  - Files sent via chat file-cards do **not** reliably download for them. The
    working delivery channel is **git**: push to the repo, they run
    `git pull` in `C:\WORKBENCH_V5`.
- **Toolchain installed on the host:** Python 3.12, `uv`, PostgreSQL 16 (Windows
  service `postgresql-x64-16`). `winget` is available. Caddy + NSSM are the
  planned public-serving layer (not installed yet).

## Database

- PostgreSQL 16, database **`workbench`**, login role **`workbench`**.
- Schema: `db/pilot_schema.sql` (idempotent). Applied via
  `deploy/windows/setup-db.ps1` (Windows) or `deploy/install.sh` (Linux).
- Connection string is in `C:\WORKBENCH_V5\.env` as `DATABASE_URL` (never
  commit it). Verify health: `role_presets` should have **18** rows.
- Tenancy: org-private tables use **row-level security** on `org_id`; set it per
  request with `SELECT set_config('app.current_org_id', <uuid>, false)` — see
  `data/pg.py` `org_connection`.

## Running tests

```
uv sync
uv run pytest tests/test_pilot_admin.py -v   # Postgres-backed; needs DATABASE_URL (.env auto-loaded via conftest)
```
The Postgres-backed tests auto-skip when `DATABASE_URL` is unset.

## Owner requests to honor (standing)

- **End-user ACCESS INSTRUCTIONS** — DONE (first pass): `docs/ACCESS.md` covers
  dev-mode launch (no login), real Auth0/Entra sign-in setup (Google/Microsoft/
  email), first-login-becomes-admin, and the approve-users flow. Finalize the
  public URL section once the domain + Auth0 are actually live.

## Current status (update this as you go)

**Done**
- New repo established, seeded from v2.4.1.
- Section 9 deploy stack: Linux (`deploy/`) + Windows (`deploy/windows/`) — Caddy
  config, systemd/NSSM services, install + DB-setup + backup scripts.
- Pilot Postgres schema live (tenancy, users/admin, 18-role library, RLS,
  optimistic-concurrency + soft locks, `poc_records`, audit log).
- §9.3/§9.4 app layer: `core/user_admin.py`, `core/oidc.py`, `ui/admin.py`,
  `data/concurrency.py` — **8/8 acceptance tests pass** (AC-9.3, AC-9.4).
- Host bring-up: Python/uv/PostgreSQL installed on `C:\WORKBENCH_V5`; schema
  loaded; `.env` written; app deps synced; 8/8 pilot tests green on the host.
- **App boots + is login-gated.** `core/session.py` resolves the user (legacy
  ungated / OIDC gate / `ER_DEV_LOGIN` dev bypass); `app.py` renders the account
  chip and routes admins to the admin panel. `data/db.py` boots with an empty
  inventory when no ALN data is present. Verified via Streamlit AppTest in all
  three modes; 397 tests pass (2 pre-existing legacy aln_loader failures;
  test_listings.py needs an absent `pullers` module — both predate this work).

- **V5-Walk multi-tenancy & roles (§10) — core done (V5.1.x).** Permission model
  (`core/permissions.py`), org/membership lifecycle (`core/orgs.py`), org context
  in session, and the point-and-click **Organization & roles** admin tab
  (`ui/admin.py`). 16/16 pilot + multi-tenancy tests pass (AC-10.1..10.5).

- **§10.4 enforced in the UI (V5.1.2.0.0).** `ui/authz.py` gates every deal tab
  by module grant (lock notice otherwise); mask/scrub helpers; admin sidebar
  "👁 Preview as role" shows the app as any preset. 21/21 tests green.

- **Module A skip-trace (§4) — DONE (V5.2.0.0.0).** `core/skiptrace/` pipeline
  S1–S7 + provider abstraction (mock adapters; `ER_SKIPTRACE_PROVIDERS=live` for
  real vendors). Compliance-gated `callable` (AC-A3), cost telemetry + budget cap
  (AC-A4). Owner Intelligence panel on the Diligence tab (FR-A1). 12 tests green.

**Next (per BUILD-ORDER.md)**
1. **Module E — Doc AI / underwriting hardening (§6.3)**: deterministic
   validation rules per doc type, per-field confidence, rent-roll anomaly
   detection, named stress-test overlays.
2. **Live vendor verification**: owner has a BatchData key; run
   `diagnose-skiptrace.bat` on the server and tune `core/skiptrace/live.py` field
   mapping against the real response. VA SCC CIS scraper still unverified (build
   env is firewalled from cis.scc.virginia.gov).
3. Public serving: Caddy + NSSM, DNS, port-forward, Auth0/Entra OIDC.
4. Phase 0 execution: build `properties_8r` from muni_records using `core/spine.py`,
   shadow-parity, cutover, purge (§7.3).

## Built so far (phases complete)
- V5-P0.5 pilot (auth, admin, concurrency), V5-Walk §10 multi-tenancy + §10.4 UI
  enforcement, Module A §4 skip trace (+ live adapters), **V5-P2 §4.4 compliance
  gate C1-C7**, **V5-P3 Module B outreach B1-B5**, **Module C radar v2 §6.1**,
  **Phase 0 spine §7.2**, **V5-P4 Module D inbox->deal §6.2**. 142 module tests green; full suite 531 passed with 4
  pre-existing data-dependent failures (aln_loader x2, test_db + test_property_io
  smoke tests that need the real ALN-populated DB / deal folders).

## Module D privacy invariant (do not regress)
Raw mail is **per-user private**: `inbox_messages` / `mailbox_connections` carry
per-user RLS requiring both `app.current_org_id` and `app.current_user_id`.
Always reach them via `data.pg.user_connection(org_id, user_id)` — never
`org_connection`. Deals/term_sheets/crm_contacts stay org-scoped on purpose.
Mailbox OAuth tokens are Fernet-encrypted with `ER_TOKEN_KEY`; never store or log
plaintext. Setup steps live in `docs/INBOX-SETUP.md`.

## Recurring gotchas (owner is non-technical on infra)
- **Always `cd C:\WORKBENCH_V5` first** in any new PowerShell window.
- Schema drift is now **self-healing**: `data/migrate.py` runs on app startup,
  detects a stale schema and applies `db/pilot_schema.sql` automatically. When a
  migration adds a column the code reads, ALSO add it to `REQUIRED_COLUMNS`
  there. `migrate-db.ps1` remains available for a manual run.
- If the app errors with a stale-module `AttributeError`, the working copy is out
  of sync → `git fetch origin && git reset --hard origin/main` (safe; `.env` is
  gitignored). Legacy features needing external setup (doc ingestion → API key,
  ETL refresh) should degrade to a NOTICE, never a red crash.

**How to launch locally (host):** `uv run streamlit run app.py` → http://localhost:8501.
Set `$env:ER_DEV_LOGIN=1` first to exercise the admin panel before OIDC is wired.

## Versioning (owner directive — do this on EVERY change)

- Version scheme **`V5.PHASE.FEATURE.PATCH.BUILD`** (5 marks the v5.0 line).
  Source of truth: `WORKBENCH_VERSION` in `config.py`; shown in the app's
  top-bar pill.
- **Every change**: bump `WORKBENCH_VERSION` (BUILD for routine changes; higher
  segments at feature/phase milestones) **and** add a dated entry to
  `CHANGELOG.md`. The owner explicitly asked that this never be skipped.

## Commit conventions

- Small, focused commits; reference the spec section/AC in the message.
- Footer on every commit:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
