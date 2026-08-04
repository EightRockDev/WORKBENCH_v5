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
  **`C:\WORKBENCH_V5`** (local disk — never OneDrive, per spec §9.2). Since
  V5.6.2.1.0 the folder is movable: launchers use `%~dp0`, and
  `ER_PROPERTIES_ROOT` can point the deal store anywhere. After a move,
  delete `.venv` (uv rebuilds it) and keep `Properties/` a sibling unless
  the override is set.
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

## How the owner wants to be worked with (standing, 2026-07-25)

- **Short answers, not build reports.** Make the fix, then say what to test.
  Skip root-cause essays unless asked. Detail belongs in `CHANGELOG.md`.
- **Do the work yourself.** Test your own changes (pytest + Streamlit AppTest);
  don't hand the owner steps you could have run. Only ask them to do something
  that genuinely requires their machine, their credentials, or their decision.
- A browser is available in the build environment - use it rather than asking.
- Keep the action list to **3-5 items, max**.

## Owner requests to honor (standing)
- **Continuous learning discipline (owner directive 2026-07-29)**: every
  mistake, surprise, or discovered constraint gets written to THIS FILE in
  the SAME commit as its fix - never batched, never deferred to
  end-of-session, never waiting to be told. A lesson that exists only in
  a conversation is considered lost. Before starting any task, re-read
  the relevant sections here; after any failure, the first question is
  "which recorded rule did this violate, or which new rule does it
  create?"
- **AUTOPILOT is the loop (V5.9.0.0.0)**: a nightly scheduled task on the
  host runs update -> discover -> pull -> phase0 -> publish. Read
  reports/ from the repo each session; push fixes; the next cycle applies
  them. Never ask the owner to run .bats for tuning again -
  `install-autopilot.bat` was their last required double-click.
- **Terminology: say "backbone", never "spine"** in anything the owner or an
  investor sees - reports, .bat text, UI copy, slides, docs (owner directive
  2026-07-29). Internal identifiers (core/spine.py, build_spine,
  properties_8r docstrings) may keep "spine"; do not churn code names.

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
  inventory when no property record data is present. Verified via Streamlit AppTest in all
  three modes; 397 tests pass (2 pre-existing legacy legacy_loader failures;
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
0. §9 serving step 2: Caddy + domain + Auth0/Entra OIDC for true public HTTPS
   — INCLUDES zero-downtime deploys (owner commitment 2026-07-29): before
   ~25 concurrent users, run two app instances behind Caddy and blue-green
   swap on code updates; sessions live in OIDC cookies + Postgres so
   restarts never log anyone out. Data updates are already zero-impact
   (WAL). Autopilot keeps deploying code at 3 AM until blue-green lands.
   (step 1 — NSSM service + LAN + passcode gate — shipped in V5.7.0.0.0).
1. **Live vendor verification**: owner has a BatchData key; run
   `diagnose-skiptrace.bat` on the server and tune `core/skiptrace/live.py` field
   mapping against the real response. VA SCC CIS scraper still unverified (build
   env is firewalled from cis.scc.virginia.gov).
2. Public serving: Caddy + NSSM, DNS, port-forward, Auth0/Entra OIDC.
3. Phase 0 execution (§7.3): **P0-1 spine builder SHIPPED (V5.8.0.0.0)** —
   `core/phase0.py` + `run-phase0.bat` (needs the v2.4.1 workbench.db with the
   3.9M muni_records rows copied to `data\` on the host, or `ER_WORKBENCH_DB`).
   `phase0-sweep.bat` = AC-P0-1 vendor-reference sweep.
   P0-2 shadow parity SHIPPED + 6 tuning rounds from real host runs (V5.8.1-V5.8.7).
   Round 6 fixed the two structural bugs: substring MF-code matching (VB zoning
   R-40 contains "r-4" -> 116K SFH parcels misclassified) and returnGeometry=false
   (no coordinates stored -> Portsmouth 0/45). Coordinates now pulled as WGS84
   centroids; wrong-city-named layers rejected; transient 5xx retried.
   First CLEAN hands-free cycle 2026-07-30 (comp overlap 66.8% vs 90% gate;
   covered-city match 86.8%). **P0-3 foundations SHIPPED (V5.10.0.0.0)**:
   rent signal v1 (`core/rent_signal.py`, HUD-FMR blend -> `est_avg_rent`,
   makes the rent-delta gate real), persisted `property_crosswalk`, and the
   `config.SPINE_READ_SOURCE` read seam in `data/db.py` (default "legacy").
   P0-3 round 2 SHIPPED (V5.10.1.0.0): listings->backbone rent ingest via
   crosswalk (`apply_listings_rents`), `core/cutover.py` deal-reference
   migration (never guesses, idempotent, dry-run), "8r" provenance key +
   comp-badge fix (missing legacy_id no longer means "User input").
   Remaining before the flip (then P0-4 purge):
   - comp overlap to >= 90% (nightly tuning loop drives this)
   - rent delta to <= 5%: needs the listings scraper RUN on the host
     (pull_listings writes rent_listings; ingest is wired and waiting) -
     FMR alone will not hit 5%
   - flip day: run `core.cutover.migrate_deal_references` against the
     pilot Postgres, set SPINE_READ_SOURCE="8r", full regression suite
   - cosmetic: inventory "matched to property records" counters + property_detail
     "db"->src_8r color read wrong after flip (dual-run only today)

## Scaling playbook — top-50 US metros (owner directive 2026-07-29)

Owner will ask for all 50 metro regions pulled + reconciled **in one turn,
under 4 hours**. Hampton Roads took ~6 screenshot round-trips; that pace is
unacceptable. Every lesson below was paid for — do not relearn any of them.

### Why Hampton Roads was slow (root causes, not symptoms)
1. **Tuning against invisible data.** The DB lives on the host; the build env
   is firewalled from city portals AND from the DB. Each fix required an
   owner round-trip. PARTIALLY SOLVED in V5.8.8.0.0: the host .bats now
   tee full output to reports/*-latest.txt and auto-push to GitHub - after
   the owner double-clicks, `git pull` here and READ reports/ instead of
   asking for screenshots. Check reports/ at the start of every session. At 50-metro scale there are NO round-trips: the pipeline
   must be right-by-construction, self-diagnosing, and self-healing.
2. **Defaults that silently lose data.** `returnGeometry=false` threw away
   every ArcGIS coordinate; substring matching on short codes ("r-4" in
   "R-40") manufactured 116K fake multifamily parcels. Both worked "fine" on
   the first two cities and failed catastrophically on the next five.
3. **Serial everything.** One feed at a time, 0.2s sleeps, national registry
   pulled when only 7 cities were needed, whole-DB rebuilds each run.

### Non-negotiable design rules for the 50-metro build
- **Geometry always**: request WGS84 centroids (probe returnCentroid ->
  returnGeometry -> legacy per layer, first page only). Convert Web Mercator
  (abs>180 -> degrees), DROP state-plane feet (converted lng falls outside
  the US box). A missing coordinate matches by address; a wrong one matches
  the wrong parcel. (`etl_munidata.ArcGISPuller`, `phase0.sanitize_latlng`.)
- **Token matching for short codes**: use-code fragments < ~5 chars must
  match whole tokens (split on whitespace/commas/slashes, KEEP hyphens).
  Long words may substring. (`phase0._MF_USE_TOKENS`.)
- **Trust nothing about a layer but its fields + a sample**: score fields
  against the alias vocabulary, geo-verify 5 sampled records against the
  metro bbox, AND reject layers named for a different city in the same
  region (bboxes overlap at borders; VB's org hosts
  Chesapeake_Norfolk_Streets_Parcels). Guard at discovery AND at pull time
  so stale feed files cannot poison. (`etl_munidata.named_for_other_city`.)
- **Retry transient 5xx** (3x, backoff); fail fast on 4xx. Gov ArcGIS
  servers 502 mid-pagination routinely.
- **Address-point feeds are unit counters**: one row per apartment sharing a
  parcel id -> units = row multiplicity per (parcel, feed), max across
  feeds, never overriding an explicit unit field.
- **Split addresses are the norm, not the exception**: number / number-suffix
  / direction / name / type across 2-5 fields (Norfolk: five). Assemble
  generically from aliases; range addresses ("700-780") key on first number.
- **Condo/complex fragmentation**: one 258-unit community = dozens of
  1-unit parcels at the same situs. Aggregate by normalized (address, city),
  then proximity-cluster, then dual-radius footprint totals for unit
  reconciliation.
- **Self-diagnosing reports**: every run prints per-city unmapped keys, top
  MF-driving use codes, and a w/-coords column. One report must contain a
  COMPLETE tuning round.
- **Allowlist beats blocklist for data guards**: no blocklist enumerates
  every single-family spelling ('1 FAM RES', 'R-1', numeric class '101').
  Guard by what you affirmatively accept, not what you reject.
- **One shared predicate per business rule**: the gate and the comp pool
  diverged silently on "what is multifamily" - `is_mf_ten_plus` is now the
  single source. Known unit counts beat labels (VB calls duplexes
  "Multi Family").
- **Recompute derived columns after merges settle**: r8_form desynced from
  (use_code, units) under COALESCE upserts. Any column that is a function
  of other columns gets recomputed in a final pass, never maintained
  incrementally.
- **Deterministic merge order**: rowid order shifts on every re-pull;
  ORDER BY source_url or the winning feed changes per host/history.
- **Adversarially review merge-semantics diffs before pushing**: a 16-agent
  review of round 7 confirmed 3 high-severity defects including a
  pre-existing stories/year_built parameter swap that misclassified nearly
  every parcel's building form. Tests alone missed all three.

### Architecture for the 4-hour, 50-metro turn
1. **Config-driven metros, zero per-city code**: a `metros.json` with
   {metro, cities[], FIPS map, bbox, known ArcGIS/Socrata roots}. The
   HR-only `--hr` flag generalizes to `--metros <list>`. Never pull outside
   scope.
2. **Parallel pulls**: ThreadPool bounded per HOST (2-3 concurrent hosts,
   1 request in flight per host, keep the politeness sleep). 50 metros x
   ~3 feeds sequentially would blow the window; parallel across hosts it
   fits. SQLite writes stay single-writer: pull workers feed a single
   writer thread (or write per-metro staging DBs, then ATTACH+merge).
3. **Kill the lock problem for good**: `PRAGMA journal_mode=WAL` on
   workbench.db (readers never block the pull) or staging-DB swap. Never
   again "database is locked" because the app was open.
4. **Discovery at scale**: AGOL public search + state open-data portals per
   metro, same scoring (apn required, units heavily weighted), auto-write
   top-2 per city. Budget: discovery for all 50 metros must itself be
   parallel (it is pure HTTP metadata).
5. **Alias vocabulary before, not during**: the ~90-alias table in
   `core/phase0.py` now covers Socrata flat, ArcGIS nested, VA schemas.
   Before a 50-metro run, sweep each discovered layer's FIELD LIST (free
   metadata) against the vocabulary and extend aliases for the top unmapped
   keys UP FRONT - field lists are visible without pulling a single record.
6. **Order of operations in the turn**: discover all -> verify all ->
   extend aliases from field metadata -> pull all (parallel) -> build spine
   (single pass, batched inserts) -> QA report per metro. Each stage fans
   out; no stage waits on a human.
7. **Estimate before running**: HR = ~1M records ~= 30 min serial. 50 metros
   ~= 30-60M records. At that volume: batched executemany (10k rows),
   json.dumps once, indexes created AFTER load, and per-metro progress
   lines so a stall is visible immediately.

### Operator-loop lessons (2026-07-29 marathon - owner directive: NEVER repeat)
The data pipeline converged in ~3 rounds; the OPERATOR LOOP burned ~10 more
human turns on delivery-channel failures discovered serially. At 50-metro
scale this is intolerable. Non-negotiable rules:

1. **The delivery channel IS the product.** Before shipping anything over a
   channel (report publishing, host self-update), prove the channel with
   integration tests that simulate the target end-to-end (local bare git
   remote, fake portals, wedged states). The publish path's 4 integration
   tests should have been written BEFORE the first report was ever asked
   for - they were written tenth, and each missing one cost a human turn.
2. **Windows delivery rules, always**: .gitattributes `*.bat/*.ps1 eol=crlf`
   from day one; batch files are THIN LAUNCHERS ONLY (a few stable lines);
   all logic in Python; never caret line-continuations; never assume the
   file survives its own self-update (stable-updater/evolving-payload
   split: autopilot.py boring, autopilot_run.py evolving).
3. **Assume nothing about host state - set it in code**: repo-local git
   user.name/email, safe.directory, credential.helper, remote URL - every
   run, idempotently. Multi-account Windows hosts guarantee at least one
   account is missing each of these.
4. **Wedged states accumulate and mutate**: each failed git op leaves
   debris (stale rebase-merge, abandoned cherry-pick, detached HEAD) that
   breaks the NEXT op differently. Recovery clears ALL states every time,
   not the one last seen.
5. **Interactive auth is a designed one-time event**: scheduled tasks run
   "when user is logged on" so the single credential prompt can appear
   once; everything else is non-interactive forever.
6. **Serial failure discovery is the real cost.** Six delivery bugs found
   one-per-human-turn = six turns. The same six found by one local
   simulation = zero turns. When a channel fails once in the field, STOP
   and simulate the whole path locally before shipping the next
   single-bug fix.
7. **Delivery early-returns must prove the remote is caught up.** The
   first autopilot cycle stranded its committed-but-unpushed reports
   behind a "nothing new to commit" early return. Always push; an
   up-to-date push is free.
8. **Automation-first deployment (the 50-metro order of operations)**:
   step 1 is deploying and PROVING the autopilot loop (self-update ->
   run -> publish -> read), with zero data. Only then does data work
   begin, because from that moment every fix flows without a human. Never
   again tune a pipeline through an unproven operator loop.
9. **Never assume always-on.** The "always-on" pilot host slept through
   the first 3 AM run and Windows skipped the task without a trace — a
   schedule alone is not execution. Every scheduled dependency must be
   registered with wake (`-WakeToRun`) AND missed-run catch-up
   (`-StartWhenAvailable`), and the loop must independently verify that
   the run actually happened (reports on the remote), never infer it
   from the schedule existing. Corollary: scheduling fixes are
   chicken-and-egg — new task settings only apply after the host executes
   something once, so the recovery path must ride on the next thing that
   ALREADY runs (or one manual double-click), not on the broken schedule.
10. **Never git-track a file a live process holds open.** The .bat
   appends every stage's output to reports/autopilot.log via `>>` - the
   handle stays open for the stage's whole duration, Windows locks the
   file, and any git operation that must rewrite that path (rebase,
   stash pop, checkout) fails. Symptom chain observed 2026-07-30:
   Claude pushed code mid-cycle -> host's pull-rebase couldn't rewrite
   the locked tracked log -> branch stayed behind -> EVERY publish
   rejected non-fast-forward -> next stage-1 checkout refused on the
   dirty file -> host wedged on old code (a remote-only unfixable state;
   update-workbench.bat's reset --hard is the rescue). Design rule:
   live/locked files stay untracked+gitignored; publish a COPY. Also:
   stage-1 must force-checkout - a boring `checkout -B` can still be
   vetoed by one dirty tracked file.
11. **"Done" means data on the owner's screen — nothing else.** GRANITE
   Loans shipped as "done" and rendered three empty tabs on the host
   (the ETL db isn't there; the alert sweep hadn't run a cycle yet).
   Code merged + tests green = "built, awaiting data" AT BEST. Before
   claiming a feature: name what the owner will SEE, on their machine,
   and when. If a data dependency is missing on the host, say so in the
   same sentence.
12. **Borrow data already in hand before hunting new feeds.** Norfolk's
   assessor feed has no geometry and weeks could have gone into finding a
   coordinate-bearing replacement layer - but the permits feed for the
   same city (124K rows, already ingested) carries coordinates for the
   same street addresses. When one feed class lacks a field, check the
   OTHER feed classes for the same market first; an address join against
   data you already pulled ships in one commit and works for every city.
13. **The manual updater must survive a running cycle.** With cycles
   chaining back-to-back all day, "run update-workbench.bat" is almost
   always issued MID-CYCLE - and its `reset --hard` tried to rewrite
   `reports\pull-latest.txt` while the pull step held it open, so the
   Windows lock aborted the whole sync and the owner stayed on old code
   (2026-07-31). Lesson 10's rule covered tracked-but-locked LIVE logs;
   the step reports are tracked by design and locked only while their
   step runs, so the updater must simply not touch them: sync code with
   `checkout -f origin/main -- . ":(exclude)reports"` + soft reset, and
   set GIT_ASK_YESNO=false so git fails fast instead of prompting y/n.
   Any recovery tool the owner runs by hand must assume the system is
   busy at that exact moment.
14. **Never claim a UI change without rendering it yourself.** The V2
   default "fix" (V5.13.1.0.0) patched a duplicate gate in
   ui/components.py while app.py renders through
   ui/v2_theme_05292026.is_v2() - so the owner updated, saw the new
   version pill AND the old layout, and rightly called it a false
   truth (2026-07-31). Two rules from it: (a) when a gate/constant
   exists in two places, grep for ALL copies and collapse to one
   source of truth before claiming anything; (b) for any user-visible
   change, launch the app headless in the container and SCREENSHOT it
   (streamlit run + Playwright against /opt/pw-browsers/chromium) -
   the screenshot, not the diff or the tests, is the proof. Corollary:
   every version indicator shown to the owner must read the real
   WORKBENCH_VERSION (the topbar pill's hard-coded "v2.1.4" nearly
   sent the debug down another wrong path).
15. **Secrets must be loaded in the context that USES them.** The app
   loads .env at startup, so HUD_API_TOKEN "worked" in every app-side
   mental test - but autopilot steps run as bare scripts where nothing
   had loaded .env, so the token the owner added (exactly as
   instructed) was invisible to every cycle and the HUD pull skipped
   forever (2026-07-31). Any module a headless step imports must load
   .env itself (dotenv, never overriding real env). And when a gate
   skips, the report line must say WHY ("no token visible" vs
   "already pulled") - a bare "skipping" hides the variable you're
   actually debugging.

### Comp-overlap: ceiling declared 2026-07-30 (BACKLOGGED, owner call)
Measured on live host cycles: centroid 66.9% / largest-parcel 66.4% /
address-parcel 66.4%. Coordinates are a live lever but no anchor beats
the centroid; the residual ~23 points vs the 90% gate are methodology
(assessor pools rank differently than the vendor's curated survey).
Do NOT resume anchor tuning; revisit via replay-methodology review
when cutover becomes the priority again. Rent gate path: listings
scraper (26.4% FMR baseline measured).

### Public-data self-feeding — SHIPPED V5.12.0.0.0
`core/public_data.py` + autopilot step `publicdata`: HMDA + HUD FMR
pull in-workbench, freshness-gated, LEI names cached in `lei_names`.
HUD FMR live-pull awaits owner adding free HUD_API_TOKEN to .env.
Listings scraper (rent-gate path) remains the next data build.

### Module C (spec 6.1) — COMPLETE as of V5.12.2.0.0
Radar v2 scoring + GRANITE Loans surface (Tabs 2-5) + continuous alert
sweep + alert->outreach routing (GRANITE Alerts "To Outreach" ->
outreach_queue -> Outreach panel Sweep queue). Remaining polish lives
in the UI queue, not Module C scope.

### Rent-gate data path — LIVE as of V5.12.5.0.0
etl_listings/ (ported scrapers) + core/listings_pull.py + autopilot
step `listings`: favorites' asking rents -> rent_listings -> crosswalk
ingest -> est_avg_rent (beats FMR). Needs favorites marked in the app
(Properties/_favorites.json) + optionally manual URLs in
_favorite_listings.json to skip bot-blocked search. Watch
reports/listings-latest.txt for scrape statuses; blocked sources want
manual URLs, not code fixes.

### Section 9 step 2 (public serving) — blue-green machinery SHIPPED V5.13.0.0.0
Caddyfile has the 8501/8502 health-checked pair; deploy-swap.ps1 does
one-at-a-time restarts. NOT LIVE until the host installs Caddy + two
NSSM services (WorkbenchBlue/WorkbenchGreen via install-lan-service.ps1)
+ domain + OIDC. Say "built, awaiting host install" - not done.

### Known open items that block 50-metro readiness
- Hampton + Suffolk still have no unit-bearing parcel layer discovered
  (portals hide them); the discovery probe list needs state-portal fallbacks
  (data.virginia.gov-style) before assuming a metro is covered.
- Near-duplicate layers (Portsmouth x3, ~36K each) waste pull time: dedupe
  candidate layers by field-signature hash + record count at discovery.
- `pull-muni.bat` currently requires the app closed; WAL (item 3) removes
  that operator step entirely.

## Built so far (phases complete)
- V5-P0.5 pilot (auth, admin, concurrency), V5-Walk §10 multi-tenancy + §10.4 UI
  enforcement, Module A §4 skip trace (+ live adapters), **V5-P2 §4.4 compliance
  gate C1-C7**, **V5-P3 Module B outreach B1-B5**, **Module C radar v2 §6.1**,
  **Phase 0 spine §7.2**, **V5-P4 Module D inbox->deal §6.2**, **Module E
  Doc AI/underwriting hardening §6.3** (V5.6.0.0.0: extraction QA
  `core/extraction_qa.py`, anomalies `core/rent_roll_anomalies.py`, named
  stress overlays `core/stress_overlays.py`, DD->verdict
  `core/verdict_tightening.py` — all deterministic; wired into Exec
  Summary, rent-roll views, doc ingest). Full suite 608 passed with 4
  pre-existing data-dependent failures (legacy_loader x2, test_db +
  test_property_io smoke tests that need the real property record data/deal folders).

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
  there (and correctness-enforcing indexes to `REQUIRED_INDEXES`).
  `migrate-db.ps1` remains available for a manual run.
- **Migrations run with NO tenant context.** Any `DELETE`/`UPDATE` in
  `pilot_schema.sql` against a table with `FORCE ROW LEVEL SECURITY` matches
  **zero rows** and silently no-ops (`current_org_id()` is NULL) — while
  `CREATE INDEX`/constraint checks are *not* RLS-filtered and still see every
  row. That mismatch caused the `ux_term_sheets_message` duplicate-key failure.
  Toggle `NO FORCE` / `FORCE` around such DML inside one `DO` block so a failure
  rolls the whole thing back and never leaves RLS off.
- If the app errors with a stale-module `AttributeError`, the working copy is out
  of sync → `git fetch origin && git reset --hard origin/main` (safe; `.env` is
  gitignored). Legacy features needing external setup (doc ingestion → API key,
  ETL refresh) should degrade to a NOTICE, never a red crash.

**How to launch locally (host):** `uv run streamlit run app.py` → http://localhost:8501.
Set `$env:ER_DEV_LOGIN=1` first to exercise the admin panel before OIDC is wired.

## Lesson — verify WHICH repo the owner is actually running (2026-07-31)

A full ALN sweep, product rename and theme panel were built and pushed to
`eightrockdev/granite` before a screenshot showed the owner's app still
unchanged: they run `C:\WORKBENCH_V5` = `eightrockdev/workbench_v5`. The two
repos share file names (`ui/property_detail.py`, `data/db.py`, config.COLORS),
so nothing about the work *looked* wrong — it was just landing somewhere the
owner never opens. The tell was in the screenshot all along: the topbar version
pill read `V5.13.1.4.0`, which only exists in workbench_v5.

Before touching UI the owner has described, confirm the repo by matching
something they can see — the version pill, a section heading, a label — against
the code. "The Forced-Seller Radar isn't on the Subject tab" should have been
read as "I am looking at the wrong tree", not "the owner misremembered": in
workbench_v5 `app.py` rendered it there, first, exactly as described.

Corollary: this repo already had a specified vocabulary for the ALN work
(spec §7.3 — `8r_class`, `8r_form`, provenance `"aln"` -> `"8r"`). Read the spec
before inventing replacement names; the GRANITE pass had invented "survey",
which would have been wrong here.

## Lesson — `CREATE TABLE IF NOT EXISTS` is not a migration (2026-08-01)

The overnight listings step died with `table rent_listings has no column named
name`, losing the only two successful scrapes of the cycle — and listings are
the one source that moves the rent-delta gate. The table had been created by an
older build; every column added to `_ROW_COLS` since then was simply absent,
and `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists.

This is the second instance in two days. The first was `properties`, whose
`aln_id`/`aln_pull_date` columns only got renamed on a full loader rebuild that
usually does not run. Any table written by long-lived installs needs a
reconcile-on-every-run step, not a create-if-absent. `ALTER TABLE ADD COLUMN`
is cheap and idempotent; call it unconditionally.

Related: the fix's tests first went into `tests/test_listings.py`, which skips
wholesale when `hampton-roads-etl` is not checked out beside the workbench —
so they never ran. **A test filed next to a heavy dependency inherits its skip.**
Schema tests need nothing but the module under test; they live in
`tests/test_listings_schema.py` and run everywhere.

## Lesson — a report must never contradict itself (2026-08-01)

The alert sweep printed "0 new multifamily" and then listed 25 `[new_mf]`
entries. Both numbers were correct: the counter tallies rows INSERTED this
cycle (`INSERT OR IGNORE` returns rowcount 0 for ones already there), while the
list shows every OPEN alert. Nothing in the report said they measured different
things, so it read as a bug and cost time to re-derive.

The list was also capped at 25 with no total, so a backlog of 200 looked
identical to a backlog of 25. Any capped list needs its true total beside it —
"showing the 25 most recent of 41" — or the cap silently becomes the finding.

## Lesson — normalize an identifier in ONE place, or not at all (2026-08-01)

The Phase-0 rename changed synthesized property ids from `aln-<n>` to
`legacy-<n>`. `property_io.is_favorite` was taught to normalize the prefix so
old `_favorites.json` entries kept working — and the UI duly kept showing
those properties starred. `listings_pull.favorite_universe()` was not, so the
rent scraper silently skipped every favorite saved by an older build.

The failure mode is the dangerous kind: **the star still rendered.** The owner
had every reason to believe those properties were being scraped, and the
listings report showed "not_found" — indistinguishable from a property the
scraper genuinely could not locate. A partial normalization is worse than none,
because it removes the symptom that would have led to the cause.

When an id format changes, grep for every consumer of that id in the same
commit. `_fav_key` is now shared by both call sites.

## Lesson — "verified" in a doc is not a test (2026-08-01)

BUILD-ORDER recorded AC-10.1 — cross-org RLS isolation — as verified. Nothing
tested it. The spec asks for the suite by name ("verified by an automated
cross-org RLS test suite"), and every tenant guarantee in the pilot rests on
those policies rather than WHERE clauses: `list_messages` has no filter at all.

Writing it surfaced three things a hand-written test would have missed, all
found by making the sweep GENERIC over `pg_class.relrowsecurity` and asserting
that every protected table was actually exercised:

- `outreach_touches` is append-only by trigger (AC-B2) — an exact-row-count
  assertion was wrong, not the schema. The property that matters is "every
  row I can see is mine", not "I see exactly one".
- `inbox_messages` / `mailbox_connections` isolate per USER as well as per
  org, so an org-only connection correctly sees nothing there.
- `revocations` has a table-level `e164 OR email IS NOT NULL` check that no
  column-level metadata reveals.

The completeness test is the important one: a table that cannot be seeded is
invisible to the sweep, so it asserts coverage explicitly rather than quietly
reporting green over eight of fifteen tables. Mutation-checked — an
over-permissive `USING (true)` policy fails six of the seven tests.

## Lesson — a skip condition must match what the test needs (2026-08-01)

Ten Postgres suites gated on `pg.is_configured()`, which only checks that a
URL exists. On any machine with a URL pointing at a stopped server that meant
**76 ERRORS**, not 76 skips. Four of them sat red across an entire session and
were repeatedly explained away as "environmental" — which was true, and
exactly why it was dangerous: a genuine regression appearing in that block
would have been read the same way and ignored.

They now gate on `pg.is_reachable()`, which connects once and caches. A run
with no database is `820 passed, 88 skipped` — zero red — so anything red is
real. Gate on the resource being USABLE, not merely named.

## Lesson — a column nobody reads is not a feature (2026-08-01)

`organizations.ai_enabled` had been in `db/pilot_schema.sql` since the pilot
schema was written, commented "Section 11 per-org LLM flag". No code read it.
An org could set it and every generative surface would carry on calling the
model — AC-11.2 was not merely untested, it was unimplemented, and the schema
made it look done.

Grepping the SPEC against the TESTS found this in one pass: 18 acceptance
criteria, 4 with no test reference, and of those the one whose supporting
column already existed was the most misleading. Worth repeating periodically
rather than trusting a status doc, which is how AC-10.1 was also recorded as
verified with no suite behind it.

The gate is placed on the line that CONSTRUCTS the client, not at the top of
each function. A surface that forgets a check at the top still gets a client;
a surface that forgets the check that *is* the client cannot.

## Lesson — calibrate a threshold against the measurement, not the goal (2026-08-01)

The AC-A2 latency guard was first written with a 50 ms per-property budget,
reasoned from the spec's 60-second SLA. Measured cost is 0.36 ms, so the
threshold permitted a **139x regression** before firing. It read as a strict
test and could not fail — the same as not existing.

Only a mutation check found it: injecting a slowdown produced MISSED. Then a
second lesson immediately on top — the first injection was 2 ms against a 5 ms
threshold, so the MISS was the test being *correct*, not loose. Both the
threshold and the mutation have to be sized against the real number.

Rule for any performance guard: measure first, set the bound at a small
multiple of what was measured, then prove it fires by breaking the thing on
purpose. A bound derived from the requirement rather than the measurement is
decoration.

## Lesson — `2>$null` on a native command is fatal under EAP=Stop (2026-08-02)

Both installers died on their own idempotent cleanup step:

    & $nssm stop $svc 2>$null | Out-Null     # "Can't open service!"

`nssm stop` on a service that does not exist writes to stderr. In Windows
PowerShell 5.1, redirecting a NATIVE command's stderr wraps that output in a
`NativeCommandError` record, and `$ErrorActionPreference = "Stop"` makes it
terminating. So the cleanup that exists to make a re-run safe is precisely
what broke the FIRST run, on every machine where the service was not already
there — the only machines that matter for an installer.

`2>&1` is fine. It is the discard-to-`$null` form that manufactures the error.

Second-order lesson from the fix: wrapping the call in a PowerShell FUNCTION
introduced a new hazard, because a function binds tokens beginning with `-` as
its own parameters, where `& $exe` passes them straight through. `-m` and
`--server.port` were one binding rule away from vanishing. Every call now
passes a single explicit `@(...)` array.

Both are guarded in `tests/test_deploy_scripts.py`. Neither is detectable by
reading the script — only by running it on a clean machine, which is the one
thing the owner should not be doing to find bugs.

## Lesson — a freshness gate must expire for the right reasons (2026-08-02)

The listings step reported `fresh (pulled within 7 days) - skipping` on a day
when its previous run had **crashed**. `is_fresh` reads the stamp written by
the last SUCCESSFUL pull, and a crash writes no stamp — so the step coasted on
an older success and skipped itself. Three things followed from one gate:

1. the failure was invisible for a week;
2. the schema fix shipped for that failure could not run;
3. the owner starred new favourites and nothing scraped them.

A cache key has to include everything that would change the answer. Time was
the only input, so neither "the code changed" nor "the inputs changed" nor
"last time this blew up" could invalidate it. Freshness is now keyed to the
favourite set as well as the clock, and a failed attempt clears its own claim.

Adjacent: `run_listings.main` said "never fails the cycle" and returned 0 —
but the exception escaped before the return, so it failed the cycle anyway.
A comment describing intent is not a mechanism. It now catches, prints, and
invalidates.

## Lesson — a proxied DNS record silently blocks ACME (2026-08-02)

Both of the owner's domains sit behind Cloudflare with the root and www A
records **Proxied** (orange cloud). Caddy proves domain control with an
HTTP-01 challenge on port 80; a proxied record means Cloudflare answers that
request instead of the host, so the challenge never arrives and no
certificate is ever issued. There is no error — just retries in
`caddy-err.log` and a site that never comes up on HTTPS.

`install-caddy.ps1` now resolves the domain before registering anything and
names the problem outright if the address is in Cloudflare's published
ranges, with the fix ("switch Proxy status to DNS only"). The `workbench`
record must be grey-cloud. (DNS-01 would work behind the proxy but needs the
caddy-dns/cloudflare plugin, which the stock binary does not carry.)

Second lesson, from the same screenshot: the owner holds BOTH eight-rock.com
and eightrockcapital.com. Seeing the latter, I rewrote every script's default
domain — then the next screenshot showed eight-rock.com is the live one (real
host, SPF/DKIM/DMARC, M365 mail) and eightrockcapital.com still points at the
RFC 5737 placeholder 192.0.2.1. Reverted. **One screenshot is a fact about
one page, not about the whole configuration** — the same mistake shape as the
wrong-repo day, and the fix is the same: confirm before rewriting.

## Lesson — the installer knows the answer the operator is guessing (2026-08-02)

Step 4 of go-live is "forward TCP 80/443 to the workbench." The router shows a
DHCP table of unlabelled hostnames, and I was about to work out which entry was
the workbench from a router screenshot. But the machine running
`install-caddy.ps1` **is** the forward target — the answer is available on the
box, exactly, at the moment the question gets asked.

Generalized: when a setup step asks the owner for a fact the installer can
read off the machine, the installer should print it rather than the assistant
inferring it from a photo. The same step now also compares the public IP
(ipify) against what the domain resolves to, and warns about the DHCP lease
that will move the LAN address and break the forward silently. Confirmed by
the owner: `192.168.0.45` / `DESKTOP-RINL8AD` is the workbench desktop, public
IP `98.190.60.27` (Cox Business).

## Lesson — a blind adapter + silent mock fallback hides its own bugs (2026-08-03)

The Apollo adapter was written without hitting the live API and had three
wrong things (POST not GET, `/api/v1` not `/v1`, body not query param). Each
would 404/error, and the registry's per-provider mock fallback would swallow
it — so the status line reads "live (apollo)" while every call returns
nothing or mock. **When an adapter is built blind against a vendor doc,
verify the method/path/param/auth against the reference before shipping, and
add a test that pins those exact wire details** — the fallback that makes the
system robust also makes a broken adapter invisible. Same shape applies to
the BatchData/Cobalt/Trestle adapters: their field-parse is still
host-verified, and a silent all-mock result is the tell that a live adapter's
wire contract is off.

## Lesson — v5 UI still had a live import of the v2.4.1 ETL (2026-08-03)

The "Scrape this property now" button imported `hampton-roads-etl/pullers` —
the frozen predecessor's ETL package, absent from the v5 tree — so it always
died "No module named 'pullers'". v5 has its own scraper stack
(`core.listings_pull` + `etl_listings`) that the autopilot already uses; the
button just never got moved onto it. **After a rewrite, grep the whole tree
for imports of the OLD system's packages — a UI path that isn't exercised by
tests can keep importing the dead module long after the engine moved.** Fix
routed the button through the same code as the nightly pull (one path, one
row shape), with a test that fails if `pullers` is imported on that path
again.

## Lesson — a non-result must not wear a result's label (2026-08-03)

Skip trace pierced 100 PRINCE AVENUE LLC and, finding no member on the GA
record, set the "principal" to the LLC name itself and labeled it "Principal
(LLC-pierced)" with empty contacts. That reads as a resolved decision-maker
with no phone — indistinguishable from a data gap. **When a resolution step
fails to produce the thing it's for, say it failed; do not fall back to
echoing the input as if it were the output.** New `entity_unpierced` role
states it plainly and routes to the real contact. Also, the honest scope
line for the owner: skip trace finds INDIVIDUALS — a single-purpose
institutional LLC with no published member has no individual to find, and no
amount of parsing invents one; the reachable contact is the manager/sponsor,
which is a firmographic lookup, not skip trace. Don't imply a capability the
data can't support.

## Lesson — half-live provider chains produce confident garbage (2026-08-03)

Skip trace pierces an LLC to a person (SOS/S3), then skip-traces that person
(BatchData/S4). Turning ONE stage live (BatchData) while the upstream stage
(SOS) is still mock is worse than all-mock: the pierce invents a principal,
and the now-real skip trace attaches genuine phone numbers to that invented
name — confident, real-looking, and wrong. **When stages compose, a
downstream stage going live can amplify an upstream mock into a
false-positive; gate the output on the provenance of the WEAKEST stage in
the chain, not the strongest.** The fix marks any LLC-pierced contact
non-callable while its SOS is mock. General rule for the vendor waterfalls:
before presenting a result as actionable, check that every stage it depended
on was real. Also: not every data source has an API — VA SCC's CIS portal is
search/filing only, no token; don't send the owner hunting for one that
doesn't exist, route to a vendor that does (Cobalt).

## Lesson — a feed's identity is (market, kind, url), not url alone (2026-08-04)

The VGIN statewide fallback (spec 15) serves Hampton, Suffolk, Richmond and
Portsmouth from ONE `VA_Parcels` url, differing only by the locality `where`.
But `muni_records` deduped on `source_url` alone: `run_feed`'s DELETE wiped
every market's rows under that url and `_feed_fresh` let one market's
freshness skip the rest — so Suffolk/Richmond came back empty while Hampton
kept the shared rows, and comp overlap dropped from ~67% to 50.5% on the
churn. The code comment even said "(market, kind, url)" while the SQL used
only url. **When one physical source is reused across logical partitions,
the dedupe/refresh key must include the partition** — fixed to
`(source_url, market, kind)` in `_feed_key`; it self-heals next cycle (each
market re-pulls its own slice). Watch comp overlap recover.

## Lesson — a reused element slot ghosts across reruns; key it (2026-08-04)

The sticky-tab fix (keyed segmented_control + `if active_tab ==` into one
container) cured the st.tabs bounce but introduced a worse symptom: on a slow
rerun the shared slot painted the PREVIOUS tab's content, faded, until the
run finished — "the underwriting tab keeps fading and shows other tabs'
data." Streamlit diffs elements by position across reruns; when the content
at a position changes shape (tab switch) it shows the stale subtree during
the fade. **Any place you render different content into the same slot across
reruns needs a stable per-state `key`** — `st.container(key=f"...{state}")`
makes Streamlit unmount the old and mount the new instead of ghosting. st.tabs
never had this because each pane is its own keyed container; the conditional-
render pattern must recreate that explicitly.

## Lesson — st.tabs and query-param auth both lose state on navigation (2026-08-03)

Two same-day UI bugs, one root shape: state that doesn't survive a
navigation/rerun. (1) A passcode token stamped into the URL dropped the
moment a property link navigated to `?prop=<id>` — re-prompting every pull.
Fix: a real browser COOKIE, not a query param, for anything that must
outlive navigation. (2) `st.tabs` resets to the first tab on ANY in-tab
widget rerun (Resolve on Diligence → bounced to Subject). Fix: a KEYED
widget (`segmented_control`) whose value lives in session_state survives the
rerun; st.tabs selection does not. General rule: if a piece of state must
survive a click that reruns the script, it needs a keyed widget
(within-session) or a cookie (across tabs/devices) — never a query param the
next navigation can overwrite, and never st.tabs. Both fixes are layered so
a failure degrades to prior behavior, and both carry AppTests that reproduce
the exact regression.

## Lesson — a batch pipeline sharing a box with the app IS an app feature (2026-08-03)

"The entire site is running exceptionally slow. It's showing lots of
information twice." The twice was Streamlit stale-element ghosting — faded
previous-render copies visible because reruns took so long. The cause was
not in the UI at all: continuously-chained autopilot cycles re-downloaded
~1M muni records and rebuilt the spine from IDENTICAL inputs every cycle on
the same machine serving Streamlit. **When the app and the pipeline share a
host, every wasted pipeline cycle is a UI latency bug.** Fix shape is the
listings lesson generalized: every expensive step skips against an INPUT
fingerprint (+ a code generation so fixes always apply), never against the
clock alone — `_feed_fresh` (muni), `spine_input_fingerprint` (phase0), each
with a force env. Also: `st.tabs` renders EVERY tab body on every rerun —
anything heavy behind a tab needs `st.cache_data`.

## Lesson — absence of data is not evidence; never score a default as a fact (2026-08-04)

Owner asked "is any of this accurate?" of a Forced-Seller score. It was ~38 and
almost entirely fabricated: `ui/radar_panel.py` fed hardcoded widget DEFAULTS to
the scorer (a pre-checked "HUD loan matures Mar 2027", "taxes current" from an
input defaulting to 0), and `core/radar_v2.py` emitted distress points for
MISSING data ("no permit history" → 75, "no deed record" → 30). So every empty
property scored the same confident number off invented inputs.

Rules:
- A component with no underlying data must be UNKNOWN (contributes nothing),
  never a default score. Absence of a permit record is not "no reinvestment";
  absence of a deed is not a tenure; an unchecked input is not "current". Model
  it as `known=False` and exclude it from the total — don't let it add OR
  dilute.
- The UI must not manufacture inputs. Default every not-yet-wired signal to "not
  on file" and make the user opt in to entering one; a widget's default value is
  not data.
- Distinguish "checked and found nothing" (known, low) from "never checked"
  (unknown). A 0 that means "no data" must never render as an all-clear — show
  NO DATA and say what feed would fill it.
- When a score aggregates several signals, show coverage (how many had real
  data). A confident number built on 1 real signal and 5 defaults is a lie of
  omission.

## Lesson — prune the known, never the unknown (2026-08-03)

Owner directive: only 10+ unit properties matter. The safe implementation
deletes rows whose unit count is KNOWN to be under 10 — and nothing else. A
units-NULL row is not "probably a house": Portsmouth's whole roll is
units-NULL and those rows are the learner's anchors and next cycle's
classification targets; pruning the unknown freezes every blind city at
zero forever. Second half: before pruning, snapshot the full roll into a
compact `parcel_index`, because two consumers need every parcel — the
verified badge's power to REFUTE (the roll row that says 4 units when a
user claims 48 is exactly the row the prune removes) and the learner's
citywide denominators. Filtering a dataset that downstream guards use as a
DENOMINATOR silently weakens those guards — audit every aggregate consumer
before shrinking a table. The prune stays non-destructive because
muni_records remains the rebuild source.

## Lesson — a size gate is not a semantics gate (2026-08-03)

Richmond's first discovered "roll" was `Undeveloped_Parcels_Richmond_
Virginia`: 6,570 records (over the 5,000 plausible-roll floor), correct
fields, geo-verified in-city — and by definition containing zero apartments.
Every numeric guard passed; the layer's NAME was the only tell. **Validate
the name against subset vocabulary (undeveloped/vacant/study/CZM/flood...)
as its own check** — count, fields, and location all measure whether data is
real, none measure whether it is the POPULATION you need. Sibling lesson
from the same cycle: Portsmouth's real 36K roll carries no geometry, which
silently capped crosswalk matching at address-only and starved the use-code
learner (7 anchors over 5 codes — never lower the evidence bar to fix that;
fix the anchors). The statewide VGIN layer now doubles as a geometry
supplement for coordinate-less rolls: same APNs, coords merge on. And in
locality filters, FIPS beats names — Virginia has both a Richmond City and
a Richmond County.

## Lesson — a test fixture that mirrors the code inherits its bugs (2026-08-03)

The Portsmouth use-code learner never ran in production: it queried crosswalk
columns `legacy_id`/`r8_id` where the real table has `legacy_property_id`/
`r8_property_id`, and a bare `except sqlite3.Error` renamed the column error
to "no crosswalk yet". The end-to-end test PASSED the whole time — its
fixture hand-built a crosswalk table with the code's wrong column names.
**When a test needs a table another module owns, create it through that
module's own writer** (`persist_crosswalk`), never by copying the consumer's
expectations into a CREATE TABLE. And when catching a broad exception class,
print the exception — "no crosswalk yet" was a guess wearing the costume of
a diagnosis. Same failure family as the freshness stamp: the system reported
a plausible benign state while silently doing nothing, and only a
falsifiable end-to-end path (fixture through the real writer) exposes it.

## Lesson — killing a supervised process is not stopping it (2026-08-02)

The updater killed the PIDs on 8501/8502 before syncing. Under NSSM that
does the opposite of stopping: the supervisor restarts the service within
seconds, so it came back MID-SYNC still running the old code, and nothing
restarted it after — the stale-version failure the kill was added to fix,
recreated by the supervisor the next milestone introduced. **When a process
manager owns the process, go through the manager** (`sc stop` /
`Restart-Service`), and re-audit every raw `taskkill`/`kill` whenever
supervision is added around something that used to run bare. The updater now
detects service mode and ends with `deploy-swap.ps1` instead of starting
with a kill.

## Lesson — a rename into a dict literal can silently delete a key (2026-08-02)

The de-id sweep renamed `src_aln` → `src_8r` inside `config.COLORS` — which
already had an `src_8r`. Duplicate keys in a dict literal are legal Python;
the first value vanishes with no warning, no linter complaint, and the UI
happily rendered the wrong provenance color for weeks. **When a sweep renames
an identifier INTO an existing namespace (a dict literal, a table, an enum),
grep for the target name first — a rename can be a collision.** Permanent
guard: `test_no_dict_literal_in_config_has_a_duplicate_key` walks config.py's
AST and fails on any duplicate literal key. Same-day sibling fix: the record
badge color now resolves through `config.spine_provenance_color()` (grey
pre-flip, teal post-flip), and the inventory cross-ref index limit was raised
to clear the 19K-row backbone (a limit sized to the old table undercounts
"matched" silently after the flip).

## Lesson — a forced re-run needs a budget and a resume point (2026-08-02)

The generation-token fix (below) forces a full re-scrape — which at 4 sources
× every favourite × ~3s politeness throttle is HOURS, inside an hourly
autopilot cycle that has phase0/alerts/preflight queued behind it. Fixing
"the work never runs" by scheduling unbounded work just moves the failure.
**Whenever a fix triggers a large catch-up job, ship the pacing with the
fix**: a wall-clock budget, per-item commits so a kill keeps what was paid
for, a per-item done-marker (tagged with the generation) so the next cycle
resumes instead of restarting, and the freshness stamp withheld until the
whole set is covered. `core/listings_pull.py` is the pattern:
`TIME_BUDGET_S` / `_recently_attempted` / stamp-on-completion-only.

## Lesson — a cache key must name the code, not just the inputs (2026-08-02)

The rent gate sat at 1 of 18,928 for a month. The favourites-key fix shipped
on 2026-08-01, the next hourly pull stamped itself fresh, and every cycle
since printed `[listings] fresh ... skipping`. The stamp meant "something was
pulled recently over this same favourite set" — never "pulled by this code."
The fix was live in the tree and could not run.

**Any freshness/skip key must include a generation token that the author bumps
when the step's output changes.** `PULL_GENERATION` in `core/listings_pull.py`
is that token; fold the same idea into any new caching step. Same family as
"create-if-absent is not a migration": shipping the code is not the same as
the code taking effect, and only the second one moves a gate.

Second half of the same failure: the skip line said "fresh," which reads as
health regardless of whether the table holds 18,000 rows or one. **A line that
justifies skipping work must state what it is protecting** — the count is now
in the message, so a stuck pull is visible in the daily report instead of
needing a query to find.

## Lesson — don't ACTIVE-health-check an upstream that's usually down (2026-08-04)

The Windows Caddyfile active-health-checked both blue-green upstreams every 3s,
but the green slot (8502) is normally not running. Result: caddy-err.log got a
"connection refused" line every 3 seconds, which buried the certificate and
error lines exactly when we needed to read them during go-live. Use PASSIVE
health (`lb_try_duration` + `fail_duration`) for an upstream expected to be
absent much of the time; reserve active polling for upstreams that should always
be up. A log you can't read is nearly as bad as no log — noisy INFO drowns the
ERROR you're hunting.

## Lesson — ingestion needs dedup, temperature=0, and a leaf-based count (2026-08-04)

Owner uploaded the same T-12 several times and saw "6 fields" then "9 fields",
plus duplicate history rows. Three separate causes, worth remembering:
- No content-hash dedup meant every re-upload re-ran (and, for PDFs, re-billed
  an API call) and appended a row even when 0 new fields were written. Fix:
  `file_content_hash` + `find_prior_ingestion`, skip unless Overwrite.
- The extraction LLM call left `temperature` at the API default, so the same
  PDF could extract slightly different values run-to-run. Always pin
  `temperature=0` for extraction/classification — anything meant to be a pure
  function of its input.
- "fields written" counted top-level keys, so a whole nested block counted as
  1 and a null counted as written. Count leaves, skip nulls.
- The headline "6 vs 9" was mostly a red herring: the SAME file had been run
  under different TYPES (t12 vs om), and each type is a different extractor
  writing a different key set. When a count looks wrong, check whether the
  inputs (here, the document TYPE) were actually the same first.

## Lesson — a port-forward target must be RFC1918, not just "not loopback" (2026-08-04)

`install-caddy.ps1` picks the machine's LAN IP to tell the operator what to
forward 80/443 to. It excluded `127.*` and `169.254.*`, then took the lowest-
metric interface. On Brian's box that surfaced the **Tailscale** address
(100.113.210.35, in the 100.64.0.0/10 CGNAT range) instead of the real LAN IP
(192.168.0.120). A router cannot forward to a Tailscale/CGNAT virtual
interface, so the printed instruction was a dead end that looks like "the site
never comes up".

Rules:
- When you need "the address a router forwards to", filter to the RFC1918
  private ranges explicitly (10/8, 172.16/12, 192.168/16). Excluding
  loopback/link-local is not enough — 100.64.0.0/10 (Tailscale, cellular
  CGNAT) and public addresses also pass that weaker test.
- On any box that might run a VPN/mesh (Tailscale, ZeroTier, WireGuard),
  interface-metric ordering can rank the virtual NIC first. Never trust
  "first interface" for a physical-LAN answer.
- When auto-detecting a value the operator will act on blindly, print the
  alternatives too, so a wrong pick is visible instead of silently followed.
- Tailscale is present on this deployment (100.113.210.35). It's the simpler
  remote-access path for Brian/Peter (no port-forward, no cert); the public
  domain + Caddy path is only needed for outside users.

## Lesson — an auto-save that rebuilds a model can loop forever (2026-08-04)

The Underwriting tab faded in/out on its own at 4% CPU / 0% disk — not
performance, not the file watcher, but an infinite rerun loop in the render.
The pattern to recognize (and never write):

    new = Model.model_validate({...SOME fields...})   # loses the others
    if new != loaded:
        save(new); st.rerun()

`_render_dials` rebuilt the deal from ONLY the dial widgets, but `DealState`
also has `selected_levers` + concurrency metadata (`row_version`, `updated_by`,
`updated_at`). model_validate reset those to defaults, so a once-saved deal was
never equal to the rebuild → auto-save + `st.rerun()` fired every render →
row_version kept climbing, levers kept getting wiped, the page never settled.

Rules paid for here:
- To diff "did the user change anything?", build the candidate with
  `loaded.model_copy(update={...only the edited fields...})`, NEVER a fresh
  `model_validate` of a subset. A partial rebuild silently resets every field
  you didn't list, and any of them (especially auto-incremented metadata like
  row_version) makes the equality always-unequal.
- An auto-save must be idempotent: saving with no user change, then reloading,
  must produce an EQUAL object. If it doesn't, and you `st.rerun()` on
  inequality, you have an infinite loop. Test the no-op: rebuild-from-own-values
  == original.
- Gate any post-save `st.rerun()` to the narrow reason it exists (here: a
  brand-new folder). A widget edit already triggers its own rerun, so an
  unconditional rerun-on-save only adds loop risk.
- Diagnostic order for "fades on its own": (1) is it re-running? low CPU +
  visible cadence = yes; (2) external trigger (file watcher / autorefresh) or
  in-script (`st.rerun` reachable every run)? Grep the active tab's render for
  `st.rerun` and for `!= ... : save; rerun` shapes before blaming CSS.

## Lesson — "fading with nobody touching it" is a rerun from OUTSIDE the browser (2026-08-04)

Chased a "tab keeps fading / shows Photo Upload from another tab" report as a
tab-switch ghost twice (keyed container v5.19.1, then discriminating CSS
v5.20.2) — both missed. The owner's third clue settled it: "just keep coming
and going without me touching anything." A pane that fades and repaints with no
user input is not a rendering bug — it is the app being RE-RUN repeatedly. In
Streamlit a rerun happens only from (a) a widget interaction, (b) `st.rerun`,
(c) `st_autorefresh`/`run_every`/a component returning a changing value, or
(d) the file watcher when `runOnSave` is on. There was none of (a)-(c) in the
tree — grep confirmed no `run_every`, no `setComponentValue`, no fragments — so
it was (d).

Root cause: `.streamlit/config.toml` had `runOnSave = true` while the daily
autopilot runs IN THE SAME DIRECTORY, writing the DB (+ WAL), `reports/`, and
`sources.json` every cycle. Streamlit's watcher read that disk churn as a
source change and auto-reran the UI endlessly. Fix: `runOnSave = false` +
`fileWatcherType = "none"`.

General rules paid for here:
- Before theorizing about stale DOM / ghosting, ask FIRST whether the pane is
  re-running on its own. "Comes and goes untouched" = spontaneous rerun; find
  the trigger, don't restyle the symptom.
- On any always-on box that also runs a background writer (autopilot, ETL),
  Streamlit's file watcher MUST be off. `runOnSave` is a dev-only convenience.
- config.toml is read at STARTUP. A config fix does nothing until the server is
  restarted — say so in the handoff, or the owner "still sees it."
- A keyed-container switch DOES cleanly unmount the old section (proven by an
  AppTest: switch tabs → the old tab's button is gone). So a persistent ghost
  is not the switch; look for what repaints the old DOM — here, the auto-rerun.

## Lesson — a keyed container does not stop the stale-DOM ghost (2026-08-04)

The property sub-tabs ghosted: switching to Underwriting showed the Subject
header's "Photo Upload"/"Open Folder" bleeding in, faded. v5.19.1 wrapped the
dispatch in `st.container(key=f"ptab_section_{active_tab}")` on the theory that
a per-tab key makes Streamlit unmount the old section cleanly. It did NOT — the
owner reported the exact same ghost after. The key changes React identity, but
that is irrelevant to the symptom: on a switch Streamlit does a server round
trip and keeps the PREVIOUS run's DOM on screen marked `data-stale="true"`,
painted faded, until the new render lands. The ghost is the outgoing section's
own leftover, shown during the round trip — a keyed container cannot make it
vanish faster because the new content simply isn't there yet.

Two things this cost:
- **Don't reach for `st.tabs` as the fix.** It has no round-trip ghost (switch
  is client-side CSS), but it snaps back to the first tab on ANY in-tab widget
  rerun — dragging an Underwriting slider bounces you to Subject. That is why
  the sticky `segmented_control` exists; reverting to `st.tabs` trades one
  owner complaint for a worse one.
- **The real fix targets the stale marker, discriminated by the active key.**
  `_inject_ghost_kill_css(active_tab)` injects, per run, CSS that hides
  `[data-stale="true"]` elements inside any `st-key-ptab_section_*` wrapper
  EXCEPT `:not(.st-key-ptab_section_<active>)`. The outgoing section's faded
  leftovers disappear; the active section is spared so a same-tab rerun keeps
  its normal in-place fade and never strobes its own widgets. Prove it by
  mutation: drop the `:not()` and the active-section-spared test must fail.

General rule: when Streamlit "shows data from another tab", the mechanism is
`data-stale` DOM lingering through a rerun round trip, not element identity.
Fix it at the stale marker, and always exempt the element that is legitimately
re-rendering in place — otherwise the cure strobes the thing the user is using.

## Lesson — the installer knows the answer the operator is guessing (2026-08-02)

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
