# CLAUDE.md â€” Eight Rock Workbench v5.0

Project memory for Claude sessions. Loaded automatically on startup. Read this
first; it captures what this project is, how the owner works, and where the
build currently stands so you can continue without re-asking.

> **FIRST ACTION every session: read `Feedback.md` (repo root) and apply it
> before doing any work.** It is the durable record of the owner's
> corrections, preferences, and process lessons (owner directive 2026-08-11).
> Update it the moment a correction lands and at every session wrapup â€”
> cross-reference to avoid duplicates, date-stamp new entries, commit and
> push in the same session. A lesson that isn't pushed does not exist.

## What this is

Eight Rock Workbench **v5.0** â€” a full-loop multifamily acquisition platform
(skip-trace/POC intelligence, compliant outreach, underwriting, multi-tenant
SaaS). It is the productization of the working **v2.4.1** internal underwriting
engine (Python/Streamlit), which this repo was seeded from.

- **Authoritative spec:** `docs/spec/workbench-v5.0-spec.md` (full document).
  **Never** build from a summary `.md` â€” it lacks the detail.
- **Build order & how to work:** `docs/spec/BUILD-ORDER.md`. Build **one phase
  at a time** in Section 13 order; honor each phase's **acceptance criteria** as
  the definition of done; keep the **deterministic core LLM-free** (Section 11).

## Hard rules

- **This is a separate repo from GRANITE.** Never build v5.0 work into GRANITE.
  Repo: `github.com/EightRockDev/WORKBENCH_v5`, default branch `main`.
- **PowerShell scripts must be pure ASCII.** Windows PowerShell 5.1 reads `.ps1`
  as ANSI; em/en-dashes and smart quotes corrupt and break parsing. No `â€”`, `â€“`,
  `â€œ`, `â€` in any `.ps1`.
- **Never commit secrets.** `.env` (DB connection) and `.streamlit/secrets.toml`
  are gitignored and must stay that way.

## Deployment target & owner context

- **Pilot host:** an always-on **Windows** server. The app lives at
  **`C:\WORKBENCH_V5`** (local disk â€” never OneDrive, per spec Â§9.2). Since
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
  request with `SELECT set_config('app.current_org_id', <uuid>, false)` â€” see
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

- **End-user ACCESS INSTRUCTIONS** â€” DONE (first pass): `docs/ACCESS.md` covers
  dev-mode launch (no login), real Auth0/Entra sign-in setup (Google/Microsoft/
  email), first-login-becomes-admin, and the approve-users flow. Finalize the
  public URL section once the domain + Auth0 are actually live.

## Current status (update this as you go)

**Done**
- New repo established, seeded from v2.4.1.
- Section 9 deploy stack: Linux (`deploy/`) + Windows (`deploy/windows/`) â€” Caddy
  config, systemd/NSSM services, install + DB-setup + backup scripts.
- Pilot Postgres schema live (tenancy, users/admin, 18-role library, RLS,
  optimistic-concurrency + soft locks, `poc_records`, audit log).
- Â§9.3/Â§9.4 app layer: `core/user_admin.py`, `core/oidc.py`, `ui/admin.py`,
  `data/concurrency.py` â€” **8/8 acceptance tests pass** (AC-9.3, AC-9.4).
- Host bring-up: Python/uv/PostgreSQL installed on `C:\WORKBENCH_V5`; schema
  loaded; `.env` written; app deps synced; 8/8 pilot tests green on the host.
- **App boots + is login-gated.** `core/session.py` resolves the user (legacy
  ungated / OIDC gate / `ER_DEV_LOGIN` dev bypass); `app.py` renders the account
  chip and routes admins to the admin panel. `data/db.py` boots with an empty
  inventory when no property record data is present. Verified via Streamlit AppTest in all
  three modes; 397 tests pass (2 pre-existing legacy legacy_loader failures;
  test_listings.py needs an absent `pullers` module â€” both predate this work).

- **V5-Walk multi-tenancy & roles (Â§10) â€” core done (V5.1.x).** Permission model
  (`core/permissions.py`), org/membership lifecycle (`core/orgs.py`), org context
  in session, and the point-and-click **Organization & roles** admin tab
  (`ui/admin.py`). 16/16 pilot + multi-tenancy tests pass (AC-10.1..10.5).

- **Â§10.4 enforced in the UI (V5.1.2.0.0).** `ui/authz.py` gates every deal tab
  by module grant (lock notice otherwise); mask/scrub helpers; admin sidebar
  "ðŸ‘ Preview as role" shows the app as any preset. 21/21 tests green.

- **Module A skip-trace (Â§4) â€” DONE (V5.2.0.0.0).** `core/skiptrace/` pipeline
  S1â€“S7 + provider abstraction (mock adapters; `ER_SKIPTRACE_PROVIDERS=live` for
  real vendors). Compliance-gated `callable` (AC-A3), cost telemetry + budget cap
  (AC-A4). Owner Intelligence panel on the Diligence tab (FR-A1). 12 tests green.

**Next (per BUILD-ORDER.md)**
0. Â§9 serving step 2: Caddy + domain + Auth0/Entra OIDC for true public HTTPS
   â€” INCLUDES zero-downtime deploys (owner commitment 2026-07-29): before
   ~25 concurrent users, run two app instances behind Caddy and blue-green
   swap on code updates; sessions live in OIDC cookies + Postgres so
   restarts never log anyone out. Data updates are already zero-impact
   (WAL). Autopilot keeps deploying code at 3 AM until blue-green lands.
   (step 1 â€” NSSM service + LAN + passcode gate â€” shipped in V5.7.0.0.0).
1. **Live vendor verification**: owner has a BatchData key; run
   `diagnose-skiptrace.bat` on the server and tune `core/skiptrace/live.py` field
   mapping against the real response. VA SCC CIS scraper still unverified (build
   env is firewalled from cis.scc.virginia.gov).
2. Public serving: Caddy + NSSM, DNS, port-forward, Auth0/Entra OIDC.
3. Phase 0 execution (Â§7.3): **P0-1 spine builder SHIPPED (V5.8.0.0.0)** â€”
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

### Richmond parcel join: evidence-scan pattern (2026-08-12)
Three Richmond feeds used three id schemes (COR 405010001, VGIN 74807-style,
workbook C0010124002); the workbook's 76,976 assessed values sat orphaned.
Fix pattern to reuse: when a join is broken, don't guess from samples - the
richmondreview 2b now scores every raw attribute by actual overlap with the
target apn set and prints winners (V5.38.2.2.0), and the alias fix cites
that evidence (V5.38.3.0.0: ptmid/pin1 + the _APN_FORMAT_BY_CITY shape
rule). COR still has NO letter-format column - COR units merge by its own
apn; if COR-vs-VGIN duplicate properties show up in backbone totals, the
crosswalk (not more aliases) is the next move.

### A frozen metric usually means a stale SCOPE, not a stale source (2026-08-13)
The rent gate did not move when the owner added HUD_API_TOKEN: coverage was
byte-identical (9.2%, 1,110 rows) across cycles. The token worked fine - the
pull and stamp loops were still HR-only (7 counties) against a 15-city
backbone. Two rules from this:
  * When expanding city coverage, grep for `HR_CITY_TO_COUNTY_FIPS_5` - each
    remaining use is a place the expansion silently stops. `CITY_TO_COUNTY_
    FIPS_5` is the whole-backbone map; HR-only is now a deliberate choice
    (HMDA still is), never a default.
  * Freshness checks must test COVERAGE, not just age: `is_fresh(90d)` kept
    an HR-era table authoritative forever. Any cached-source gate should ask
    "does it contain everything I now map?" alongside "how old is it?".
Identical gate numbers two days running = suspect the scope, not the feed.

### Seeded numbers must name their basis (owner ask 2026-08-13)
Asked how price/NOI were populated on an untouched property, the honest
answer was "a constant that looks like analysis": units x $130k, 100 units
assumed when unknown, $1,500 rent fallback. The fix pattern, reusable for
any future default: pick the best per-asset ANCHOR available (own sale ->
assessor value -> market constant), return WHICH one was used alongside the
number (core/deal_seed.DealSeed.price_basis), and have every surface render
that basis next to the field - warning styling when the value is a market
placeholder. A default nobody can trace is indistinguishable from data.

### Per-user edits, field governance, signup email â€” SHIPPED V5.25.0.0.0 (2026-08-07)
Property Card edits now save per-user (`user_property_overrides`, per-user RLS
like the inbox; shared folder JSON = legacy base + dev-mode fallback). Field
tiers live in `core/field_policy.py` + `docs/DATA-DICTIONARY.md` â€” reference
(locked) / org (empty v1) / user; the edit form derives its set from the
policy, never re-enumerate. `core/mailer.py` sends branded signup/approval
emails over SMTP from .env â€” AWAITING OWNER: SMTP credentials
(SMTP_HOST/PORT/USER/PASS in .env) before any mail actually sends; until then
every send is a clean "not configured" notice. Owner decisions still open:
which fields (if any) promote to org tier. Mail: LIVE 2026-08-08 via
Microsoft Graph (core/mailer.py) â€” sender = the FREE SHARED MAILBOX
welcome@eight-rock.com, app "Workbench Mailer" (Mail.Send application
permission + admin consent), creds in .env GRAPH_*. Paid-for lessons, do
not re-fight: (1) the tenant's Security Defaults HARD-BLOCK Basic SMTP â€”
no app password will ever work; (2) Graph refuses to send FROM an alias
(ErrorSendAsDenied) even with SendFromAliasEnabled â€” the sender must be a
real mailbox, and a license-free shared mailbox is the pattern; (3) angle
brackets in .env examples get copied literally by a non-technical owner â€”
show fake-but-real-shaped values instead. Hardening DONE 2026-08-08:
ApplicationAccessPolicy scopes Mail.Send to welcome@ only (mail-enabled
security group "Workbench Mailer Scope"; Test-ApplicationAccessPolicy
verified Granted for welcome@, Denied for Brian@). `docs/DATA-DICTIONARY.pdf` is the owner-facing doc,
GENERATED from field_policy by scripts/build_data_dictionary_pdf.py â€” never
hand-edit the PDF; any field-policy change requires the rebuild command in
that script's docstring (test_data_dictionary_pdf goes red otherwise).

### Data API v1 â€” SHIPPED V5.26.0.0.0 (2026-08-07)
`api_server.py` + admin "Data API" tab: per-org hashed Bearer keys, metered
usage (api_usage = the future Stripe meter), reference-layer-only endpoints.
Run: `run-api.bat` (port 8600, localhost). NOT public until a Caddy route is
added; billing awaits owner pricing + Stripe. api_keys/api_usage are
deliberately non-RLS bootstrap tables (key lookup discovers the org).

### Property activity trail â€” SHIPPED V5.27.0.0.0 (2026-08-09)
Admin â†’ Activity: who viewed/edited which properties (org-scoped
property_activity + per-user overrides summary). Views throttled once per
session per property via st.session_state guard in
ui/property_detail._log_property_view; edits logged with changed field
names at the per-user save. Fail-silent everywhere.

### Morning report: ALWAYS include the pending-approval queue (owner directive 2026-08-09)
Report new users waiting for approval every morning. Source:
reports/pending-users-latest.txt (autopilot `pendingusers` step). If that
file shows 0 or is stale/absent, say so explicitly. Also standing owner Qs
answered 2026-08-09: Richmond pulls ~83.3K raw parcels/night (76,739 VGIN
VA_Parcels + 6,570 Undeveloped subset), 111 qualify MF â€” VGIN has no unit
counts/sales, hence thin.

### Headless .env is now CENTRAL - do not re-solve per script (2026-08-09)
`data/pg.database_url()` lazily loads .env when DATABASE_URL is unset, so any
autopilot/cron script importing `data.pg` gets Postgres creds automatically.
The old per-script dotenv dance is no longer required for pg access (run_backup
still loads it for pg_dump's own env). This closes the third recurrence of the
2026-07-31 "headless steps must load .env" lesson.

### Municipal API discovery via browser DevTools â€” the standing technique (owner directive 2026-08-09)
To find any municipality's property data source: open a known property on its
public portal, F12 -> Network -> XHR, reload, read the API URL (that is how VB
= Spatialest was found). Claude should drive this itself with the pre-installed
Chromium/Playwright FOR SITES THE PROXY ALLOWS; NOTE the build container is
firewalled from city/gov portals (proxy 403), so for those the discovery runs
on the HOST autopilot or via the self-discovering puller (scripts/pull_sales.py
does the "Network tab" job programmatically: probes vendor endpoints, keeps the
one returning real sale data). Never pull blind against an unverified endpoint
(Apollo lesson). Copy-as-PowerShell from DevTools also reveals required headers
(VB needed Origin/Referer) - fold those into the puller.

### VB sale history = ArcGIS Property_Sales_ FeatureServer, NOT Spatialest (CONFIRMED 2026-08-10)
DECISIVE, owner-verified. This was solved in an earlier chat and lost because it
was never written here - hence the wasted Spatialest chase. RECORD cross-session
findings the moment they land.
  * Source: VB Real Estate Assessor "Property Sales" dataset on the city's
    ArcGIS Hub - SAME AGOL org as VB parcels:
    https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/Property_Sales_/FeatureServer/0
    Standard Esri FeatureServer: no key/auth, 2000 rows/page via resultOffset.
    ~594k total records; 47,631 arm's-length (Sale_Price>0) since 2021.
  * Schema: GPIN, Street_Address, City, Zip_Code, Neighborhood, Land_Value,
    Improvement_Value, Total_Value, Sale_Price, Document_Number, Deed_Book,
    Deed_Page, Sales_Date (epoch ms).
  * Puller: scripts/pull_arcgis_sales.py (autopilot step `arcgissales`).
    SIZE FIRST (returnCountOnly) then paginate; WHERE Sale_Price>0 AND
    Sales_Date>=DATE 'YYYY-01-01'. Writes attributes VERBATIM as kind='sales'
    into muni_records - extract_sale_records already maps Sale_Price/Sales_Date/
    Deed_Book/Deed_Page and phase0.normalize_record maps GPIN->apn, so NO
    field map needed; sale_index picks it up next cycle.
  * Registry ARCGIS_SALES_FEEDS: add Norfolk/Chesapeake (same ArcGIS Hub
    pattern) once their layer URLs are verified - never pull blind.
  * Spatialest is a DEAD END for VB deeds: api.spatialest.com serves
    annual-assessment/buildings/taxes (200) but 404s EVERY sales route, and the
    portal recordcard 403s "Direct API access not permitted". The `vbprobe`
    step and scripts/probe_vb_sales.py are RETIRED. Do not re-chase Spatialest
    for VB sales.
  * ROOT CAUSE of "VB sales worked months ago, now doesn't" (found 2026-08-11):
    MUNI_FEEDS carried VB assessor+sales via Property_Sales_view/FeatureServer/0
    and that view WENT DARK - the nightly pull "succeeded" with 0 records, so
    nothing alerted. Entry now status="superseded" (documented, not pulled).
    Standing rule: a feed that returns 0 rows where it once returned thousands
    is a BREAK, not a success - treat [OK] ... 0 records lines as failures.

### Municipal sales coverage - THREE ADAPTER TYPES (owner-verified 2026-08-11)
All in scripts/pull_arcgis_sales.py (autopilot `arcgissales`), registry
SALES_SOURCES, rows land VERBATIM as muni_records kind='sales' (sale_history's
key sets + phase0 apn aliases make field maps unnecessary). Every adapter
sizes the pull FIRST and never deletes on a transient empty.
  * Virginia Beach = esri_history: Property_Sales_/FeatureServer/0, full
    transfer events (~594k, ~2wk lag). Default since-2021 arm's-length;
    ER_ARCGIS_SALES_SINCE_YEAR widens for full deed-chain tenure.
  * Norfolk = socrata_snapshot_stack: data.norfolk.gov FY files are ONE row
    per parcel (latest transfer), re-published yearly. Stack FY19..FY27
    (th3n-jr9u, pdf2-gh9c, 8bfx-a5g8, 7tu9-2ytx, yvpm-8aid, 9gmp-9x4c,
    g7sg-tivf, m5ya-5grb, qva7-tzrf) + dedupe (gpin,transfer_date), later FY
    wins -> ~last 3 sales/parcel. FY27 lags ~5 days. MUNI_FEEDS assessor
    entry now FY27 qva7-tzrf - ROLL YEARLY (FY25 had gone stale).
  * Chesapeake = esri_date + xlsx_join: parcels layer (already kind='assessor')
    carries TRANSFER/DEEDBK/DEEDPG, NO price; prices come from the assessor's
    annual LandBook XLS portal items (commercial 28f709bf..., residential
    714668f4...) joined on MAP_PARCEL (aliased to apn). "currentowner" +
    "transfer" added to sale_history key sets for this. No chesva
    Property_Sales FeatureServer exists - do not hunt for one.
  * Richmond = socrata_snapshot_stack on data.richmondgov.com: "Property
    Transfer History" uxre-by3i (quarterly, full history) + "Property
    Transfers" k9h9-y482 (recent); MUNI_FEEDS entry upgraded to live Socrata
    "Property Assessments Current" vm9j-9f88. STATUS 2026-08-11: first host
    run FAILED every count query while Norfolk's identical code worked, so
    the ORIGINAL "auth-gated 403" note about this domain was probably right
    (do not dismiss prior probe notes without re-testing). Dataset IDs are
    confirmed SODA-accessible via dev.socrata.com foundry, so it's the HTTP
    layer: V5.35.0.1.0 adds exact status logging (_LAST_ERR), a browser UA
    (the Spatialest lever), and ER_SOCRATA_APP_TOKEN -> X-App-Token (free
    token: evergreen.data.socrata.com). Next cycle's arcgis-sales-latest.txt
    is decisive; if it still says HTTP 403, mint the token. Socrata column
    spellings vary per city - the stack adapter probes _STACK_ID_KEYS/
    _STACK_DATE_KEYS candidates; extend those tuples if a city reports 0
    dated transfers against non-zero counts.
  * FIRST HOST RUN RESULTS (2026-08-10 22:15 cycle): VB 47,631; Norfolk
    114,698 unique (gpin,date) from a 671k-row FY stack; Chesapeake 120,557
    LandBook rows (30,272 commercial + 90,285 residential). 282,886 sale
    rows total, indexed same cycle. Richmond 0 (above).

### All-seven Hampton Roads sales push (owner "do all of them" 2026-08-11)
Status per remaining jurisdiction after the source hunt:
  * Newport News: COVERED (most-recent sale) - its parcel layer already
    carries last-sale fields (assessor+sales, live).
  * Suffolk: portal CONFIRMED Spatialest WITH a Sales tab
    (community.spatialest.com/va/suffolk) but api.spatialest.com never
    answered - pull_sales.py now probes BOTH hosts (SPATIALEST_HOSTS) and only
    settles for "no sales feed" after all hosts strike out. Runtime
    sale-verification still gates every combo.
  * Hampton + Portsmouth: no city-published sales dataset found. The HRPDC
    regional exchange (HRGEO, geo.hrsd.com/hrgeo/rest/services -
    regionalgis/HRGeo_Parcels_Public merges every member locality under one
    standardized schema) is now a KNOWN_ROOT for Hampton/Portsmouth/Suffolk
    discovery: the host field-scores whatever each city actually shares, and
    if the regional schema carries sale fields they flow to sale_index via
    assessor rows automatically. Portsmouth's own data is otherwise
    portal-only (data.portsmouthva.gov ASP.NET search) - scrape is the
    last-resort follow-up.
  * pull_arcgis_sales arcgis adapter now takes per-source cfg["where"]
    (default = the VB arm's-length WHERE) so future Esri sales layers with
    different field names can join the registry cleanly.

### Clickable sale sources (V5.37.0, owner ask 2026-08-11)
Every sale row carries source_url end-to-end: muni_records -> sale_records
(column added; migration ALTERs + FORCES a rebuild so link-less rows never
survive a fresh stamp) -> sale_history_for -> Sale History card, rendered as
a LinkColumn ("view record ->"). core/sale_links.sale_source_link maps
machine endpoints to HUMAN pages (stack tags/LandBook/VB Esri -> explicit
overrides; any Socrata /resource/<id>.json -> /d/<id>; Spatialest API ->
community portal; browsable REST URLs pass through; junk -> no link, never a
broken card). New sales sources should get an override there when their
machine URL isn't itself human-readable.

### Hottest-50 metro sales rollout has begun (2026-08-11)
Chicago (Wave 1 #1) is in SALES_SOURCES: Cook County Assessor "Parcel Sales"
wvhk-k5uv on datacatalog.cookcountyil.gov - TRANSACTION-level full history per
PIN (the Assessor's own published dataset). County-wide ~1.5M rows, so
cfg["soql_where"] applies the arm's-length-since-2021 stance ($where support
added to the socrata adapter). pin/sale_date/sale_price are all already in
the id/date/price key sets. Pattern for the rest of Wave 1: find each metro's
county-assessor sales dataset (Socrata or Esri), add with verified resource
id + appropriate soql_where/where filter, let the sized first host run prove
it. Sales rows index by APN now and join the backbone when each metro's FIPS
activates - history is pre-positioned, not blocked on activation.

### Richmond MF completeness (owner commitment 2026-08-11: "all Richmond MF
### in the DB by morning")
Backbone had 71,590 Richmond properties but only 111 MF - VGIN parcels carry
apn ONLY (no use codes/units), and AGOL keyword search never surfaced
Richmond's own GeoHub org (only RichmondCounty lookalikes). Three concurrent
paths, all riding the hourly cycles overnight:
  1. vm9j-9f88 "Property Assessments Current" (MUNI_FEEDS, live) brings
     property CLASS for every parcel -> phase0 MF-code matching. Same 403-risk
     domain as the transfer stack; rides the browser-UA fix, else needs
     ER_SOCRATA_APP_TOKEN.
  2. COR AGOL org root (services6.arcgis.com/il6vO1TutlF580Ku) added to
     discover_feeds KNOWN_ROOTS - the host walks/field-scores every GeoHub
     layer and auto-feeds qualifying ones (use_code/units) via feeds_extra.
  3. Transfer-history stack (sales) - same UA/token gate.
Morning check: backbone-stats Richmond MF count should jump from 111 toward
Raleigh-scale (~1,000+); arcgis-sales-latest shows Richmond sales; if either
still blocked with HTTP 403, the app token is the single unlock.

### National discovery is ON and AGGRESSIVE (owner directive 2026-08-09)
discover_feeds is national: (city,state) tuples, correct state stamped, VGIN
gated to VA. Autopilot `discover_national` runs EVERY cycle
(ER_DISCOVERY_EVERY_DAYS default 0). Discovered feeds -> feeds_extra
-> pulled; activate a metro's backbone build only after adding its FIPS +
metro label (verified from the discover report). App is "Multifamily Property
Workbench" now, not "Virginia" - do not reintroduce state-specific branding.

### TARGET_METROS = the HOTTEST 50, not the biggest 50 (owner directive 2026-08-09, V5.33.0)
Owner ruled OUT a population sort: "go after the hottest 50 multifamily markets."
TARGET_METROS is now the 50-metro universe of Marcus & Millichap's National
Multifamily Index (the ranking the owner already subscribes to via MyMMI - his
inbox carries the M&M Multifamily National Investment Forecast every January),
ordered into 5 deployment WAVES by the verified 2026 NMI signal (supply-
constrained coastal/gateway/Midwest lead; 2026-oversupplied Sun Belt -
Austin/Houston/Nashville/Jacksonville - sit in later waves). List order = wave
order. Berkadia's quarterly national report is aggregates + an alphabetical
coverage roster, NOT a ranked list - use it as a set cross-check, not the
ranking basis. If the owner wants exact intra-wave ordinals, they come from his
gated MyMMI PDF.

### National expansion is LIVE, wave-based (owner directive 2026-08-09)
r8_market no longer hardcoded - market_data.metro_for(city) self-labels
(never mislabels). Add a metro by: (1) county FIPS in EXPANSION_CITY_TO_FIPS_5,
(2) metro label in CITY_TO_METRO, (3) market in etl_munidata.EXPANSION_MARKETS,
(4) ensure its feed is in MUNI_FEEDS (or discover it). Wave 1 = Raleigh,
Charlotte, Winston-Salem, Greensboro, Durham, Nashville, Atlanta (registry
feeds already existed). Per-metro counts: reports/backbone-stats-latest.txt.
Discovery (discover_feeds) is still VA-scoped (VGIN/TARGET_CITIES) - generalize
it for metros whose feeds aren't yet in the registry. Free sources only unless
owner says otherwise; get creative on non-free.

### hampton-roads-etl now lives IN THIS REPO (2026-08-09)
Folded in from GRANITE (which is being archived). Resolve its dir/db ONLY via
core/etl_location.py (in-repo -> data/ -> legacy sibling) - never hardcode the
sibling path again. Generated hampton_roads.db is gitignored; the ETL is run
standalone (python hampton-roads-etl/hampton_roads_etl.py) to produce it. Its
listings parser is a duplicate of etl_listings/ (the app uses etl_listings via
core.listings_pull); the vendored copy exists because the market ETL imports
pullers.listings for its own pipeline.

### Sign-in "Internal Server Error" = OIDC state split across the blue-green pair (2026-08-10)
Symptom: clicking Log in threw "Internal Server Error"; a refresh usually
worked. Service err log showed Authlib `MismatchingStateError: CSRF Warning!
State not equal` in Streamlit's `/oauth2callback` - a FRAMEWORK-layer failure,
so a try/except in core/oidc.gate would NOT have caught it (diagnose the log
before patching the app). Root cause: install-lan-service sets AUTO_START on
BOTH WorkbenchBlue (8501) and WorkbenchGreen (8502), so both run, but the
Caddyfile load-balanced them with `lb_policy first` and NO session affinity. A
transient blip on 8501 served the login redirect on one colour and the callback
on the other; the OAuth `state` set on the first instance failed the CSRF check
on the second. Fix: Caddy `lb_policy cookie er_upstream` (sticky sessions) in
deploy/windows/Caddyfile - pins each client's whole login handshake to one
colour. A blue-green Streamlit pair should ALWAYS have client affinity (its
WebSocket wants it too). Applying it needs the host to re-run install-caddy.ps1
(regenerates Caddyfile.active) or `caddy reload`. Lesson: any per-instance
in-memory/session state (OAuth state, Streamlit session) breaks under a
load balancer without affinity - the traceback names the layer; read it first.
Follow-ups (V5.33.0.3.0): (1) deploy/Caddyfile (Linux) now mirrors the same
blue-green pair + lb_policy cookie for parity; (2) install-caddy.ps1 hot-reloads
via `caddy reload` when the service already exists (zero downtime) instead of
stop/remove/reinstall, so future Caddyfile edits just need install-caddy re-run
(or a bare `caddy reload --config deploy\windows\Caddyfile.active`).

### Global short-code MF tokens are a trap; roll text vetoes labels (2026-08-11)
The original _MF_USE_TOKENS carried "r-4" as a VA apartment class; Richmond's
roll arrived and "R-4 Single Family" x9,001 became multifamily overnight ->
15.6K "MF" in Richmond, a 20K+ open new_mf alert flood, and a poisoned
rent-coverage denominator. Three rules now enforced in code:
  1. NO city-ambiguous short code lives in the global token set - "r-4" is
     removed; a city where it truly means apartments re-adds it via the
     per-city learned map (use_code_learn), which is evidence-based.
  2. Single-family veto: text containing "single family"/"1 fam"/etc. never
     classifies MF by label (_SF_VETO_SUBSTRINGS); explicit units >= 10
     still win. The phase0 report's "Top use codes" line printed the bug
     plainly ("R-4 Single Family x9001") - READ that section every cycle;
     wrong codes there = bad aliasing, its own caption says so.
  3. Alerts self-heal: run_sweep closes open alerts (status='stale') whose
     property no longer qualifies MF, so a misclassification correction
     drains the flood next cycle instead of carrying it forever.
Legit Richmond MF codes (R-48/R-53/R-63/R-73 "Multi Family...") match via
the "multi family" substring and are untouched.

### salespull: coerce record-derived APN with str() before .strip() (2026-08-10)
pull_sales.sample_gpins crashed the WHOLE sales pull mid-locality with
`'int' object has no attribute 'strip'`: some rolls store APN/GPIN as a bare
integer, and `record.get("apn") or ""` keeps the int, so `.strip()` raised.
Fix: `str(... or "").strip()`. Rule for any value that flows out of a muni
`record` JSON blob: it may be int/float/None, never assume str - coerce before
string ops. Regression: tests/test_pull_sales.py::test_sample_gpins_handles_numeric_apn.

## Scaling playbook â€” top-50 US metros (owner directive 2026-07-29)

Owner will ask for all 50 metro regions pulled + reconciled **in one turn,
under 4 hours**. Hampton Roads took ~6 screenshot round-trips; that pace is
unacceptable. Every lesson below was paid for â€” do not relearn any of them.

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
9b. **A documented-but-manual setup step is a step that never happened.**
   The nightly DB backup was "scheduled via Task Scheduler" in the deploy
   README from day one; a server task audit (2026-08-08) found it was never
   registered - the pilot Postgres had NO backups, ever. And as written it
   would have hung on a password prompt under SYSTEM. Corollary to lesson 9:
   for every scheduled dependency, verify the task EXISTS on the host (task
   listing in hand), make the script runnable non-interactively, and prefer
   wiring it into the one loop that already runs (autopilot) over a second
   human-registered schedule.
9. **Never assume always-on.** The "always-on" pilot host slept through
   the first 3 AM run and Windows skipped the task without a trace â€” a
   schedule alone is not execution. Every scheduled dependency must be
   registered with wake (`-WakeToRun`) AND missed-run catch-up
   (`-StartWhenAvailable`), and the loop must independently verify that
   the run actually happened (reports on the remote), never infer it
   from the schedule existing. Corollary: scheduling fixes are
   chicken-and-egg â€” new task settings only apply after the host executes
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
11. **"Done" means data on the owner's screen â€” nothing else.** GRANITE
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

**Morning analysis 2026-08-05 (do not re-diagnose these):**
- comp overlap now **48.7%** (was 66.9% pre-VGIN-collision). The VGIN fix
  (V5.20.1) restored **Richmond (111 MF)** but **Hampton (2), Suffolk (17),
  Portsmouth (0)** are still near-empty: their VGIN VA_Parcels feed carries no
  unit counts AND their use codes are unmapped (Portsmouth codes are blank;
  Hampton/Suffolk numeric). Separately, **Virginia Beach = 15,858 MF of which
  15,470 are "Multi Family" code-only (no units)** â€” likely over-inclusive
  (duplex/condo swept in), which both inflates the backbone and dilutes overlap.
  These are the real levers, NOT anchor methodology â€” but both are the
  BACKLOGGED feed/aliasing work, still owner-gated. Do not touch phase0 aliasing
  blind; validate against host data first.
- rent delta **27.0%**, `rents_from_listings` still **1**. Root cause is now
  precise (from the phase0 rent-signal log): scraped/favourited properties are
  in cities with **no backbone** (e.g. Madison Terrace is in Hopewell, not a
  covered metro) -> not in the crosswalk -> can't stamp a listing rent. HUD_API_
  TOKEN IS now live on the host (public-data pulls FMR with token). So the gate
  moves only when favourites land in COVERED cities, not from more scraping of
  uncovered ones. Not a bug.
- Autopilot: all steps exit 0. A transient `Could not resolve host: github.com`
  appears mid-run (host DNS blip) but the ret/next pushes succeed â€” reports are
  current. Not actionable from here; only flag if it stops self-recovering.

### Public-data self-feeding â€” SHIPPED V5.12.0.0.0
`core/public_data.py` + autopilot step `publicdata`: HMDA + HUD FMR
pull in-workbench, freshness-gated, LEI names cached in `lei_names`.
HUD FMR live-pull awaits owner adding free HUD_API_TOKEN to .env.
Listings scraper (rent-gate path) remains the next data build.

### Module C (spec 6.1) â€” COMPLETE as of V5.12.2.0.0
Radar v2 scoring + GRANITE Loans surface (Tabs 2-5) + continuous alert
sweep + alert->outreach routing (GRANITE Alerts "To Outreach" ->
outreach_queue -> Outreach panel Sweep queue). Remaining polish lives
in the UI queue, not Module C scope.

### Rent-gate data path â€” LIVE as of V5.12.5.0.0
etl_listings/ (ported scrapers) + core/listings_pull.py + autopilot
step `listings`: favorites' asking rents -> rent_listings -> crosswalk
ingest -> est_avg_rent (beats FMR). Needs favorites marked in the app
(Properties/_favorites.json) + optionally manual URLs in
_favorite_listings.json to skip bot-blocked search. Watch
reports/listings-latest.txt for scrape statuses; blocked sources want
manual URLs, not code fixes.

### Section 9 step 2 (public serving) â€” blue-green machinery SHIPPED V5.13.0.0.0
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
- V5-P0.5 pilot (auth, admin, concurrency), V5-Walk Â§10 multi-tenancy + Â§10.4 UI
  enforcement, Module A Â§4 skip trace (+ live adapters), **V5-P2 Â§4.4 compliance
  gate C1-C7**, **V5-P3 Module B outreach B1-B5**, **Module C radar v2 Â§6.1**,
  **Phase 0 spine Â§7.2**, **V5-P4 Module D inbox->deal Â§6.2**, **Module E
  Doc AI/underwriting hardening Â§6.3** (V5.6.0.0.0: extraction QA
  `core/extraction_qa.py`, anomalies `core/rent_roll_anomalies.py`, named
  stress overlays `core/stress_overlays.py`, DD->verdict
  `core/verdict_tightening.py` â€” all deterministic; wired into Exec
  Summary, rent-roll views, doc ingest). Full suite 608 passed with 4
  pre-existing data-dependent failures (legacy_loader x2, test_db +
  test_property_io smoke tests that need the real property record data/deal folders).

## Module D privacy invariant (do not regress)
Raw mail is **per-user private**: `inbox_messages` / `mailbox_connections` carry
per-user RLS requiring both `app.current_org_id` and `app.current_user_id`.
Always reach them via `data.pg.user_connection(org_id, user_id)` â€” never
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
  **zero rows** and silently no-ops (`current_org_id()` is NULL) â€” while
  `CREATE INDEX`/constraint checks are *not* RLS-filtered and still see every
  row. That mismatch caused the `ux_term_sheets_message` duplicate-key failure.
  Toggle `NO FORCE` / `FORCE` around such DML inside one `DO` block so a failure
  rolls the whole thing back and never leaves RLS off.
- If the app errors with a stale-module `AttributeError`, the working copy is out
  of sync â†’ `git fetch origin && git reset --hard origin/main` (safe; `.env` is
  gitignored). Legacy features needing external setup (doc ingestion â†’ API key,
  ETL refresh) should degrade to a NOTICE, never a red crash.

**How to launch locally (host):** `uv run streamlit run app.py` â†’ http://localhost:8501.
Set `$env:ER_DEV_LOGIN=1` first to exercise the admin panel before OIDC is wired.

## Lesson â€” an empty data card is a source question, not always a code bug (2026-08-05)

Sale history read empty. We first found and fixed a real code bug (wrong function
name, V5.24.9) â€” but it was STILL empty after, because the deeper issue is data
availability, not code. Sale history has three possible sources (folder
`sales.json`, `sources.json â†’ assessmentHistory`, `muni_records`), and the
assessor feed we actually ingest carries FY assessment VALUES, not deed/transfer
records â€” so "who sold to whom for how much" simply isn't in our data for a
freshly-pulled property. Rule: before writing more code to "fix" an empty
feature, prove where the data is supposed to come from and whether it exists
there â€” a read-only diagnostic that names the empty source
(`scripts/diagnose_sale_history.py`) settles in one run what a round of guessing
can't, and stops you from building a fancier reader over a source that has
nothing to read. Assessment value â‰  sale price; don't conflate the feed you have
with the feed the feature needs.

**CORRECTION (2026-08-06): the conclusion above was wrong â€” and the way it went
wrong is its own lesson.** A live-DB diagnostic (run by the owner on the box)
found 1.38M `kind='assessor+sales'` rows carrying TOTSALPRICE / SALE_DATE /
DEED_BOOK / DEED_PAGE. The transfer data was there all along; the reader
couldn't see it because `_PRICE_KEYS` lacked `totsalprice`, the address
fallback demanded exact equality ("Street" vs "St" failed), and the market
scope was case-sensitive. The trap: `diagnose_sale_history.py` **shared the
reader's alias table**, so it "confirmed" the no-data theory by testing the
data through the very gap that hid it. Rules: (1) a diagnostic must not reuse
the suspect code path's parsing â€” sample the RAW keys and eyeball them (the
owner's raw-key dump is what broke the case); (2) before concluding "the feed
lacks X", grep the feed REGISTRY notes â€” `etl_munidata.py` line 82 literally
said "TOTSALPRICE+SALE_DATE" the whole time; (3) when a key alias table meets
a new feed, diff the feed's actual field list against the table up front
(same rule as the 50-metro alias sweep). Fixed in V5.24.14.

## Lesson â€” you can't kill a remote Streamlit session; flag it and let it self-logout (2026-08-05)

Owner wanted admins to "sign people out" from the who's-online screen. There is
no server-side session registry to revoke â€” Streamlit native auth lives in a
signed cookie in each browser, and the only way to invalidate every cookie
(rotating `cookie_secret`) logs *everyone* out. So "sign out user X" is done with
a flag: `presence.request_logout(sid)` adds the id to a set and drops it from the
online list now; the TARGET session checks `should_logout(sid)` on its next rerun
and calls `st.logout()` itself. Honest consequence, stated in the UI: it lands on
that person's next interaction, not instantly (we can't force another browser to
rerun). This works only because the flag set and the target session share one
process â€” true here since Caddy's `lb_policy first` pins all traffic to the Blue
instance; if traffic ever fanned across instances this would need a shared store
(Postgres/Redis). Rule: when asked to act on another live session, don't pretend
to reach into it â€” leave a flag it reads and acts on itself, and tell the user
when it takes effect. (Also this pass: a control that exposes other users' IPs is
gated to operators at BOTH the page AND the entry point â€” the topbar pill is a
plain span for non-admins, not just a link to a blocked page.)

## Lesson â€” st.login() reads the FLAT [auth] table; a config TEMPLATE that lies costs an hour (2026-08-05)

Streamlit native auth has two shapes: a single **default** provider (all keys â€”
`redirect_uri`, `cookie_secret`, `client_id`, `client_secret`,
`server_metadata_url` â€” directly under `[auth]`, used by `st.login()` with no
argument) and **named** providers (`[auth.<name>]` sub-tables, used by
`st.login("<name>")`). `core/oidc.py` calls bare `st.login()`, so it needs the
FLAT shape â€” but `secrets.toml.example` and the setup doc showed the nested
`[auth.auth0]` form. Result during go-live: `StreamlitAuthError: missing keys
['client_id','client_secret','server_metadata_url']` with the keys visibly
present, and several confused restarts. Auth0 is ONE provider that federates
Google/Microsoft/email internally â€” there was never a reason for a named sub-table.
Rules: (1) a config template/example is load-bearing documentation â€” it must
match exactly what the code parses, or it actively misleads; when you change how
config is read, fix the template in the same breath. (2) For Streamlit auth
specifically: bare `st.login()` â‡’ flat `[auth]`. Two more go-live traps folded
into the doc the same day: secrets reload only on **restart** (the file-watcher
is off from the fade fix), Windows silently saves `secrets.toml.txt` with the
extension hidden, and the OAuth callback to the public host times out from
INSIDE the LAN (NAT hairpin) â€” fixed per-machine with a `hosts` entry, not a code
change.

## Lesson â€” a setup script that WRITES a shared file must upsert, never clobber (2026-08-05)

`setup-db.ps1` ended by writing a fresh 3-line `.env` via `Set-Content` â€” fine on
a blank box, catastrophic on a live one: it would have wiped the owner's Anthropic
key and all four live skiptrace provider keys (Cobalt/BatchData/Trestle/Apollo)
the moment he ran it to finish login setup. `.env` is a SHARED file many features
write into; any script that touches it must read-merge-write (back up, preserve
unmanaged lines, refresh only its own keys), the same upsert pattern
`_save_api_key_to_env` already uses for the API key. Rule: a provisioning/setup
step that owns a few keys in a shared config file must never own the whole file â€”
`Set-Content`/truncate-write on `.env`, `secrets.toml`, or any multi-writer file
is a data-loss bug waiting for the second feature to need it. Upsert, and back up
before you rewrite.

## Lesson â€” FORCE RLS blocks the owner's own pg_dump; back up with a BYPASSRLS reader (2026-08-09)

The first-ever nightly dump failed: pg_dump connected as the app's
`workbench` role and Postgres refused COPY on RLS-protected tables ("query
would be affected by row-level security policy") â€” FORCE ROW LEVEL SECURITY
applies to the table owner too, which is exactly why tenant isolation holds
and exactly why the app's credentials can never take a full backup. Never
"fix" this by granting BYPASSRLS to the app role (that would kill isolation)
or by `--enable-row-security` (dumps only the empty no-context view). The
pattern: a dedicated read-only backup role. One-time, on the host (prompts
for the postgres superuser password; generate any long password for the
role):

    & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -d workbench -c "CREATE ROLE backup_reader LOGIN PASSWORD '<long-random>' BYPASSRLS; GRANT pg_read_all_data TO backup_reader;"

then in .env:
    ER_BACKUP_DATABASE_URL=postgresql://backup_reader:<long-random>@localhost:5432/workbench

Related probe lesson, same cycle: a diagnostic that hides WHY it failed
wastes its whole cycle â€” the VB probe reported status=0 twelve times with
the exception text discarded. Every failed request in a probe must carry
its reason; and when everything fails at connection level, retry once with
a browser User-Agent before concluding anything (WAFs gate on UA).

## Lesson â€” a broad `except` + an untested default path = a feature that never ran (2026-08-05)

Sale history read "No sale history available" on EVERY property from day one.
Cause: `_muni_db_path` called `phase0.workbench_db()` â€” a function that does not
exist (it's `find_workbench_db`). Every call raised `AttributeError`, and
`sale_history_for`'s `try/except Exception: return []` swallowed it into a clean
empty list. Two failures stacked: (1) a broad except that turns a NameError-class
bug into "no data", indistinguishable from a legitimate empty result; (2) 10
passing tests that ALL injected `db_path=`, so not one exercised the
`db_path=None` branch the live app actually takes. Rules: a bare
`except Exception` around a whole feature must not be the thing that decides
"empty vs broken" â€” let coding errors (AttributeError/NameError/TypeError)
surface, or log them, don't fold them into the success-shaped fallback. And a
test that hard-codes the injectable dependency proves the injected path, never
the default one the product runs â€” cover the no-argument path explicitly. Verify
a data feature actually returns data against a populated store, not just that it
doesn't crash.

## Lesson â€” put the data where the eye already is; a popover is a second click (2026-08-05)

Shipped the owner contact detail as an `st.popover` beneath the People block.
Owner's reaction: "Move all POC/Contact information under PEOPLE â€” no weird
dropdown below." A collapsed popover reads as a detour when the user is already
looking at the People card â€” the contact info belongs *in* that card, inline
under the person it describes. Rebuilt as inline HTML (`_poc_contact_rows_html`
/ `_people_contact_html`) rendered inside the existing People markdown, routed by
POC role to the right person. Rule: surface derived detail where the user is
already looking, not behind an extra click â€” a popover/expander is justified only
when the detail is genuinely secondary or space is truly scarce, not as a default
home for "extra" data. (Also: because People renders on every property view, the
POC read is done ONCE per render and shared across the owner/manager rows â€” see
the DB-degrades-not-crashes lesson; three popover-era reads became one.)

## Lesson â€” verify WHICH repo the owner is actually running (2026-07-31)

A full ALN sweep, product rename and theme panel were built and pushed to
`eightrockdev/granite` before a screenshot showed the owner's app still
unchanged: they run `C:\WORKBENCH_V5` = `eightrockdev/workbench_v5`. The two
repos share file names (`ui/property_detail.py`, `data/db.py`, config.COLORS),
so nothing about the work *looked* wrong â€” it was just landing somewhere the
owner never opens. The tell was in the screenshot all along: the topbar version
pill read `V5.13.1.4.0`, which only exists in workbench_v5.

Before touching UI the owner has described, confirm the repo by matching
something they can see â€” the version pill, a section heading, a label â€” against
the code. "The Forced-Seller Radar isn't on the Subject tab" should have been
read as "I am looking at the wrong tree", not "the owner misremembered": in
workbench_v5 `app.py` rendered it there, first, exactly as described.

Corollary: this repo already had a specified vocabulary for the ALN work
(spec Â§7.3 â€” `8r_class`, `8r_form`, provenance `"aln"` -> `"8r"`). Read the spec
before inventing replacement names; the GRANITE pass had invented "survey",
which would have been wrong here.

## Lesson â€” `CREATE TABLE IF NOT EXISTS` is not a migration (2026-08-01)

The overnight listings step died with `table rent_listings has no column named
name`, losing the only two successful scrapes of the cycle â€” and listings are
the one source that moves the rent-delta gate. The table had been created by an
older build; every column added to `_ROW_COLS` since then was simply absent,
and `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists.

This is the second instance in two days. The first was `properties`, whose
`aln_id`/`aln_pull_date` columns only got renamed on a full loader rebuild that
usually does not run. Any table written by long-lived installs needs a
reconcile-on-every-run step, not a create-if-absent. `ALTER TABLE ADD COLUMN`
is cheap and idempotent; call it unconditionally.

Related: the fix's tests first went into `tests/test_listings.py`, which skips
wholesale when `hampton-roads-etl` is not checked out beside the workbench â€”
so they never ran. **A test filed next to a heavy dependency inherits its skip.**
Schema tests need nothing but the module under test; they live in
`tests/test_listings_schema.py` and run everywhere.

## Lesson â€” a report must never contradict itself (2026-08-01)

The alert sweep printed "0 new multifamily" and then listed 25 `[new_mf]`
entries. Both numbers were correct: the counter tallies rows INSERTED this
cycle (`INSERT OR IGNORE` returns rowcount 0 for ones already there), while the
list shows every OPEN alert. Nothing in the report said they measured different
things, so it read as a bug and cost time to re-derive.

The list was also capped at 25 with no total, so a backlog of 200 looked
identical to a backlog of 25. Any capped list needs its true total beside it â€”
"showing the 25 most recent of 41" â€” or the cap silently becomes the finding.

## Lesson â€” normalize an identifier in ONE place, or not at all (2026-08-01)

The Phase-0 rename changed synthesized property ids from `aln-<n>` to
`legacy-<n>`. `property_io.is_favorite` was taught to normalize the prefix so
old `_favorites.json` entries kept working â€” and the UI duly kept showing
those properties starred. `listings_pull.favorite_universe()` was not, so the
rent scraper silently skipped every favorite saved by an older build.

The failure mode is the dangerous kind: **the star still rendered.** The owner
had every reason to believe those properties were being scraped, and the
listings report showed "not_found" â€” indistinguishable from a property the
scraper genuinely could not locate. A partial normalization is worse than none,
because it removes the symptom that would have led to the cause.

When an id format changes, grep for every consumer of that id in the same
commit. `_fav_key` is now shared by both call sites.

## Lesson â€” "verified" in a doc is not a test (2026-08-01)

BUILD-ORDER recorded AC-10.1 â€” cross-org RLS isolation â€” as verified. Nothing
tested it. The spec asks for the suite by name ("verified by an automated
cross-org RLS test suite"), and every tenant guarantee in the pilot rests on
those policies rather than WHERE clauses: `list_messages` has no filter at all.

Writing it surfaced three things a hand-written test would have missed, all
found by making the sweep GENERIC over `pg_class.relrowsecurity` and asserting
that every protected table was actually exercised:

- `outreach_touches` is append-only by trigger (AC-B2) â€” an exact-row-count
  assertion was wrong, not the schema. The property that matters is "every
  row I can see is mine", not "I see exactly one".
- `inbox_messages` / `mailbox_connections` isolate per USER as well as per
  org, so an org-only connection correctly sees nothing there.
- `revocations` has a table-level `e164 OR email IS NOT NULL` check that no
  column-level metadata reveals.

The completeness test is the important one: a table that cannot be seeded is
invisible to the sweep, so it asserts coverage explicitly rather than quietly
reporting green over eight of fifteen tables. Mutation-checked â€” an
over-permissive `USING (true)` policy fails six of the seven tests.

## Lesson â€” a skip condition must match what the test needs (2026-08-01)

Ten Postgres suites gated on `pg.is_configured()`, which only checks that a
URL exists. On any machine with a URL pointing at a stopped server that meant
**76 ERRORS**, not 76 skips. Four of them sat red across an entire session and
were repeatedly explained away as "environmental" â€” which was true, and
exactly why it was dangerous: a genuine regression appearing in that block
would have been read the same way and ignored.

They now gate on `pg.is_reachable()`, which connects once and caches. A run
with no database is `820 passed, 88 skipped` â€” zero red â€” so anything red is
real. Gate on the resource being USABLE, not merely named.

## Lesson â€” a column nobody reads is not a feature (2026-08-01)

`organizations.ai_enabled` had been in `db/pilot_schema.sql` since the pilot
schema was written, commented "Section 11 per-org LLM flag". No code read it.
An org could set it and every generative surface would carry on calling the
model â€” AC-11.2 was not merely untested, it was unimplemented, and the schema
made it look done.

Grepping the SPEC against the TESTS found this in one pass: 18 acceptance
criteria, 4 with no test reference, and of those the one whose supporting
column already existed was the most misleading. Worth repeating periodically
rather than trusting a status doc, which is how AC-10.1 was also recorded as
verified with no suite behind it.

The gate is placed on the line that CONSTRUCTS the client, not at the top of
each function. A surface that forgets a check at the top still gets a client;
a surface that forgets the check that *is* the client cannot.

## Lesson â€” calibrate a threshold against the measurement, not the goal (2026-08-01)

The AC-A2 latency guard was first written with a 50 ms per-property budget,
reasoned from the spec's 60-second SLA. Measured cost is 0.36 ms, so the
threshold permitted a **139x regression** before firing. It read as a strict
test and could not fail â€” the same as not existing.

Only a mutation check found it: injecting a slowdown produced MISSED. Then a
second lesson immediately on top â€” the first injection was 2 ms against a 5 ms
threshold, so the MISS was the test being *correct*, not loose. Both the
threshold and the mutation have to be sized against the real number.

Rule for any performance guard: measure first, set the bound at a small
multiple of what was measured, then prove it fires by breaking the thing on
purpose. A bound derived from the requirement rather than the measurement is
decoration.

## Lesson â€” `2>$null` on a native command is fatal under EAP=Stop (2026-08-02)

Both installers died on their own idempotent cleanup step:

    & $nssm stop $svc 2>$null | Out-Null     # "Can't open service!"

`nssm stop` on a service that does not exist writes to stderr. In Windows
PowerShell 5.1, redirecting a NATIVE command's stderr wraps that output in a
`NativeCommandError` record, and `$ErrorActionPreference = "Stop"` makes it
terminating. So the cleanup that exists to make a re-run safe is precisely
what broke the FIRST run, on every machine where the service was not already
there â€” the only machines that matter for an installer.

`2>&1` is fine. It is the discard-to-`$null` form that manufactures the error.

Second-order lesson from the fix: wrapping the call in a PowerShell FUNCTION
introduced a new hazard, because a function binds tokens beginning with `-` as
its own parameters, where `& $exe` passes them straight through. `-m` and
`--server.port` were one binding rule away from vanishing. Every call now
passes a single explicit `@(...)` array.

Both are guarded in `tests/test_deploy_scripts.py`. Neither is detectable by
reading the script â€” only by running it on a clean machine, which is the one
thing the owner should not be doing to find bugs.

## Lesson â€” a freshness gate must expire for the right reasons (2026-08-02)

The listings step reported `fresh (pulled within 7 days) - skipping` on a day
when its previous run had **crashed**. `is_fresh` reads the stamp written by
the last SUCCESSFUL pull, and a crash writes no stamp â€” so the step coasted on
an older success and skipped itself. Three things followed from one gate:

1. the failure was invisible for a week;
2. the schema fix shipped for that failure could not run;
3. the owner starred new favourites and nothing scraped them.

A cache key has to include everything that would change the answer. Time was
the only input, so neither "the code changed" nor "the inputs changed" nor
"last time this blew up" could invalidate it. Freshness is now keyed to the
favourite set as well as the clock, and a failed attempt clears its own claim.

Adjacent: `run_listings.main` said "never fails the cycle" and returned 0 â€”
but the exception escaped before the return, so it failed the cycle anyway.
A comment describing intent is not a mechanism. It now catches, prints, and
invalidates.

## Lesson â€” a proxied DNS record silently blocks ACME (2026-08-02)

Both of the owner's domains sit behind Cloudflare with the root and www A
records **Proxied** (orange cloud). Caddy proves domain control with an
HTTP-01 challenge on port 80; a proxied record means Cloudflare answers that
request instead of the host, so the challenge never arrives and no
certificate is ever issued. There is no error â€” just retries in
`caddy-err.log` and a site that never comes up on HTTPS.

`install-caddy.ps1` now resolves the domain before registering anything and
names the problem outright if the address is in Cloudflare's published
ranges, with the fix ("switch Proxy status to DNS only"). The `workbench`
record must be grey-cloud. (DNS-01 would work behind the proxy but needs the
caddy-dns/cloudflare plugin, which the stock binary does not carry.)

Second lesson, from the same screenshot: the owner holds BOTH eight-rock.com
and eightrockcapital.com. Seeing the latter, I rewrote every script's default
domain â€” then the next screenshot showed eight-rock.com is the live one (real
host, SPF/DKIM/DMARC, M365 mail) and eightrockcapital.com still points at the
RFC 5737 placeholder 192.0.2.1. Reverted. **One screenshot is a fact about
one page, not about the whole configuration** â€” the same mistake shape as the
wrong-repo day, and the fix is the same: confirm before rewriting.

## Lesson â€” the installer knows the answer the operator is guessing (2026-08-02)

Step 4 of go-live is "forward TCP 80/443 to the workbench." The router shows a
DHCP table of unlabelled hostnames, and I was about to work out which entry was
the workbench from a router screenshot. But the machine running
`install-caddy.ps1` **is** the forward target â€” the answer is available on the
box, exactly, at the moment the question gets asked.

Generalized: when a setup step asks the owner for a fact the installer can
read off the machine, the installer should print it rather than the assistant
inferring it from a photo. The same step now also compares the public IP
(ipify) against what the domain resolves to, and warns about the DHCP lease
that will move the LAN address and break the forward silently. Confirmed by
the owner: `192.168.0.45` / `DESKTOP-RINL8AD` is the workbench desktop, public
IP `98.190.60.27` (Cox Business).

## Lesson â€” a blind adapter + silent mock fallback hides its own bugs (2026-08-03)

The Apollo adapter was written without hitting the live API and had three
wrong things (POST not GET, `/api/v1` not `/v1`, body not query param). Each
would 404/error, and the registry's per-provider mock fallback would swallow
it â€” so the status line reads "live (apollo)" while every call returns
nothing or mock. **When an adapter is built blind against a vendor doc,
verify the method/path/param/auth against the reference before shipping, and
add a test that pins those exact wire details** â€” the fallback that makes the
system robust also makes a broken adapter invisible. Same shape applies to
the BatchData/Cobalt/Trestle adapters: their field-parse is still
host-verified, and a silent all-mock result is the tell that a live adapter's
wire contract is off.

## Lesson â€” v5 UI still had a live import of the v2.4.1 ETL (2026-08-03)

The "Scrape this property now" button imported `hampton-roads-etl/pullers` â€”
the frozen predecessor's ETL package, absent from the v5 tree â€” so it always
died "No module named 'pullers'". v5 has its own scraper stack
(`core.listings_pull` + `etl_listings`) that the autopilot already uses; the
button just never got moved onto it. **After a rewrite, grep the whole tree
for imports of the OLD system's packages â€” a UI path that isn't exercised by
tests can keep importing the dead module long after the engine moved.** Fix
routed the button through the same code as the nightly pull (one path, one
row shape), with a test that fails if `pullers` is imported on that path
again.

## Lesson â€” a non-result must not wear a result's label (2026-08-03)

Skip trace pierced 100 PRINCE AVENUE LLC and, finding no member on the GA
record, set the "principal" to the LLC name itself and labeled it "Principal
(LLC-pierced)" with empty contacts. That reads as a resolved decision-maker
with no phone â€” indistinguishable from a data gap. **When a resolution step
fails to produce the thing it's for, say it failed; do not fall back to
echoing the input as if it were the output.** New `entity_unpierced` role
states it plainly and routes to the real contact. Also, the honest scope
line for the owner: skip trace finds INDIVIDUALS â€” a single-purpose
institutional LLC with no published member has no individual to find, and no
amount of parsing invents one; the reachable contact is the manager/sponsor,
which is a firmographic lookup, not skip trace. Don't imply a capability the
data can't support.

## Lesson â€” half-live provider chains produce confident garbage (2026-08-03)

Skip trace pierces an LLC to a person (SOS/S3), then skip-traces that person
(BatchData/S4). Turning ONE stage live (BatchData) while the upstream stage
(SOS) is still mock is worse than all-mock: the pierce invents a principal,
and the now-real skip trace attaches genuine phone numbers to that invented
name â€” confident, real-looking, and wrong. **When stages compose, a
downstream stage going live can amplify an upstream mock into a
false-positive; gate the output on the provenance of the WEAKEST stage in
the chain, not the strongest.** The fix marks any LLC-pierced contact
non-callable while its SOS is mock. General rule for the vendor waterfalls:
before presenting a result as actionable, check that every stage it depended
on was real. Also: not every data source has an API â€” VA SCC's CIS portal is
search/filing only, no token; don't send the owner hunting for one that
doesn't exist, route to a vendor that does (Cobalt).

## Lesson â€” a feed's identity is (market, kind, url), not url alone (2026-08-04)

The VGIN statewide fallback (spec 15) serves Hampton, Suffolk, Richmond and
Portsmouth from ONE `VA_Parcels` url, differing only by the locality `where`.
But `muni_records` deduped on `source_url` alone: `run_feed`'s DELETE wiped
every market's rows under that url and `_feed_fresh` let one market's
freshness skip the rest â€” so Suffolk/Richmond came back empty while Hampton
kept the shared rows, and comp overlap dropped from ~67% to 50.5% on the
churn. The code comment even said "(market, kind, url)" while the SQL used
only url. **When one physical source is reused across logical partitions,
the dedupe/refresh key must include the partition** â€” fixed to
`(source_url, market, kind)` in `_feed_key`; it self-heals next cycle (each
market re-pulls its own slice). Watch comp overlap recover.

## Lesson â€” a reused element slot ghosts across reruns; key it (2026-08-04)

The sticky-tab fix (keyed segmented_control + `if active_tab ==` into one
container) cured the st.tabs bounce but introduced a worse symptom: on a slow
rerun the shared slot painted the PREVIOUS tab's content, faded, until the
run finished â€” "the underwriting tab keeps fading and shows other tabs'
data." Streamlit diffs elements by position across reruns; when the content
at a position changes shape (tab switch) it shows the stale subtree during
the fade. **Any place you render different content into the same slot across
reruns needs a stable per-state `key`** â€” `st.container(key=f"...{state}")`
makes Streamlit unmount the old and mount the new instead of ghosting. st.tabs
never had this because each pane is its own keyed container; the conditional-
render pattern must recreate that explicitly.

## Lesson â€” st.tabs and query-param auth both lose state on navigation (2026-08-03)

Two same-day UI bugs, one root shape: state that doesn't survive a
navigation/rerun. (1) A passcode token stamped into the URL dropped the
moment a property link navigated to `?prop=<id>` â€” re-prompting every pull.
Fix: a real browser COOKIE, not a query param, for anything that must
outlive navigation. (2) `st.tabs` resets to the first tab on ANY in-tab
widget rerun (Resolve on Diligence â†’ bounced to Subject). Fix: a KEYED
widget (`segmented_control`) whose value lives in session_state survives the
rerun; st.tabs selection does not. General rule: if a piece of state must
survive a click that reruns the script, it needs a keyed widget
(within-session) or a cookie (across tabs/devices) â€” never a query param the
next navigation can overwrite, and never st.tabs. Both fixes are layered so
a failure degrades to prior behavior, and both carry AppTests that reproduce
the exact regression.

## Lesson â€” a batch pipeline sharing a box with the app IS an app feature (2026-08-03)

"The entire site is running exceptionally slow. It's showing lots of
information twice." The twice was Streamlit stale-element ghosting â€” faded
previous-render copies visible because reruns took so long. The cause was
not in the UI at all: continuously-chained autopilot cycles re-downloaded
~1M muni records and rebuilt the spine from IDENTICAL inputs every cycle on
the same machine serving Streamlit. **When the app and the pipeline share a
host, every wasted pipeline cycle is a UI latency bug.** Fix shape is the
listings lesson generalized: every expensive step skips against an INPUT
fingerprint (+ a code generation so fixes always apply), never against the
clock alone â€” `_feed_fresh` (muni), `spine_input_fingerprint` (phase0), each
with a force env. Also: `st.tabs` renders EVERY tab body on every rerun â€”
anything heavy behind a tab needs `st.cache_data`.

## Lesson â€” run the FULL suite before pushing, not just the obvious test (2026-08-04)

Moved the property panels to the Market tab and pushed after running only
`test_sticky_tabs.py`. The full suite then failed: `test_backoffice_move.py`
still asserted the OLD placement. A cross-cutting move (relocating a panel)
breaks tests that pin the old location, which live in files you didn't touch.
Always `pytest -q` the whole suite before a push, especially for a move/rename â€”
the failing test names point you straight at every place that encoded the old
structure. (Client IP for who's-online comes from Caddy's `X-Real-IP` header â€”
which only exists because the Caddyfile sets `header_up X-Real-IP {remote_host}`;
direct-to-8501 LAN hits have no such header and read as a local address.)

## Lesson â€” a mock provider must never surface as real when live is configured (2026-08-05)

Owner saw a pierced principal with provenance `mock-va-scc` and no contacts
while the providers line said SOS was `live (cobalt)`. The mock SOS fabricates
an officer name from the entity ("Robert Brg" â† "Brg Aura"), and it was shown as
a verified "Principal (LLC-pierced)"; BatchData found nothing because the person
isn't real. Same integrity failure as the radar/coverage work: fabricated data
presented as real. Rules:
- Tag every result with its vendor and, at assembly time, check whether a mock
  vendor produced it. In LIVE-configured mode a mock result must be demoted to
  the honest "unknown/unresolved" state (here: `entity_unpierced`), not shown as
  verified. Keep demo/mock mode's deterministic output only when the whole
  system is in mock mode (`status` says "mock").
- Diagnose provenance by SIGNATURE: MockSOS's filing-id `VA-<7 digits>` and
  conf ~0.85-0.94 are distinguishable from Cobalt's real `sosId`/0.86-0.4. A
  "live" status label is not proof a given record is live â€” the stored record
  can be a stale mock from an earlier resolve.
- `resolve_contacts` is idempotent (re-run replaces the POC set), so a stale
  mock record is fixed by re-running once live keys are in â€” say that instead of
  writing a migration.
- Root cause here is coverage, not code: Cobalt's VA officer data is thin, so
  the real fix for VA LLC principals is the live VA SCC token; skip-trace can't
  find a phone/email until the pierce yields a REAL name.

## Lesson â€” label the data gap; don't reclassify blind to hide it (2026-08-05)

Owner said "do both" â€” (1) fix the VB "Multi Family" over-count + Hampton/
Suffolk/Portsmouth aliasing, and (2) label near-empty covered cities. I did (2)
and deliberately did NOT do (1). Why: (1) means editing phase 0's MF classifier,
which last time (the R-40 substring bug) misclassified 116K parcels, and there
is no way to validate a use-code change against the host's real rolls from the
build env. "Prune the known, never the unknown" also means VB's units-NULL rows
are kept on purpose â€” dropping them to "fix the over-count" would empty VB the
way Portsmouth was once emptied. The safe, honest move is to LABEL the gap: the
Coverage page now says "feed incomplete â€” unit counts not published" for a
covered metro with parcels but no confirmable MF, instead of a tiny number or a
false "Coming soon". An owner "do it now" authorizes the GOAL, not a blind change
that could regress the backbone â€” do the safe half, ship it, and say exactly
what the risky half needs (host validation + the locality use-code dictionaries)
before touching it.

## Lesson â€” the data was already pulled; surface it, don't re-pull (2026-08-05)

Owner asked for a "deed feed" for Sale History ("we had it and it worked
great"). The instinct is to build a scraper against a deeds source. But the
assessor feeds we ALREADY pull nightly into `muni_records` carry last-sale
price/date/buyer â€” phase 0 just lists them in `_IGNORED_KEYS` and drops them.
So the "feed" was a READ, not a new pull: `core/sale_history.py` reads the raw
`muni_records` back out. Rules paid for here:
- Before adding a data source, check what the existing feeds already carry (grep
  the ignored/unmapped keys and the raw record). A new scraper is the last
  resort, not the first.
- A read-only resolver over already-stored data can't break the nightly build â€”
  vastly safer than touching `phase0.build_spine`. Prefer it for "surface X".
- Reuse the proven matcher: `phase0.normalize_record` already turns a raw record
  into `{apn, address, ...}`; the sale resolver keys off that instead of
  reinventing parcel matching.
- When you can't validate against host data from the build env, make the logic
  fully unit-tested with synthetic records AND fully guarded (miss/error -> the
  old empty state, never a wrong value), and say plainly it needs one host
  verification pass. `apn` had to be threaded onto the 8R property dict
  (`data/db._r8_to_legacy_shape`) for the exact-parcel match â€” it wasn't exposed.

## Lesson â€” check for existing scaffolding before planning a build (2026-08-04)

Owner asked for "true login (Microsoft/Google/email) tonight." The instinct was
to plan a multi-file OAuth build. But an Explore pass found the entire OIDC stack
already written and wired: `core/oidc.py` (st.login/st.user bridge),
`core/user_admin.py` (users table + first-user-is-admin onboarding + approval),
`core/session.resolve_user` (already dispatches to `oidc.gate` when `[auth]` is
in secrets), `ui/admin.py` (approve/suspend), and `.streamlit/secrets.toml.example`
with the exact `[auth.auth0]` template. The ONLY code gap was an undeclared
runtime dep (`authlib`, required by Streamlit's native OIDC). Rules:
- Before scoping a feature, grep the tree for it â€” a surprising amount here is
  pre-built and config-gated (this stack, blue-green, the GRANITE feeds). A
  20-minute investigation turned a "big build" into a one-line dependency add.
- Streamlit native `st.login`/`st.user` needs `Authlib>=1.3.2` installed or it
  raises at login time; declare it explicitly (it's not pulled by `streamlit`).
- The identity path is: `st.user` (sub/email/name) -> `oidc._provider_identity`
  -> `user_admin.sync_user_on_login(idp_sub,...)` (keyed on the OIDC `sub`, not
  email) -> `AdminUser` + org + `Permissions`. First login = admin; the rest are
  pending until approved. Don't confuse this with the legacy `core/auth.py`
  MSAL `User` â€” the active stack is `AdminUser`.

## Lesson â€” a script that controls services must self-elevate (2026-08-04)

`update-workbench.bat` synced code fine but the blue/green swap at the end died:
`Restart-Service` (and `sc stop`) need administrator rights, the updater didn't
elevate, and it failed with "Cannot open WorkbenchBlue service" â€” leaving the
app running the OLD code after a "successful" update. install-caddy.bat and
install-service.bat already self-elevate; the updater didn't, even though it
grew a service-restart step. Rules:
- Any .bat/.ps1 that stops/starts/restarts a Windows service, writes to
  Program Files, or creates a scheduled task MUST self-elevate at the top
  (`net session` + `Start-Process -Verb RunAs`, or an IsInRole check in PS).
- When a step is added that needs new privileges, re-check the entry point's
  elevation â€” don't assume the wrapper still has enough rights.
- Fail LOUD and specific: `deploy-swap.ps1` now checks IsInRole and prints how
  to fix it, instead of surfacing the opaque "Cannot open service".

## Lesson â€” don't gate a feature behind chrome you've hidden (2026-08-04)

Admin was a toggle inside `st.sidebar`. But `_inject_branding` hides Streamlit's
default chrome â€” including the sidebar collapse/expand handle. So when the
sidebar was collapsed, there was no visible control to reopen it, and Admin
(and anything else sidebar-only) became unreachable â€” owner: "I don't see an
arrow." Rules:
- If you hide Streamlit's default chrome, you own re-providing every control it
  carried (the sidebar handle, the menu). Don't hide the handle AND put the
  only entrance to a feature behind it.
- Put primary entry points in the MAIN pane where they can't be hidden by a
  collapsed sidebar, and add a URL fallback (`?admin=1`) for anything important
  so it's reachable even if a control is ever obscured.
- The owner had actually asked for Admin by the top-right 8R chrome, not in the
  sidebar; the sidebar placement ignored that and then broke. Build where the
  owner said, not where it was easiest to bolt on.

## Lesson â€” absence of data is not evidence; never score a default as a fact (2026-08-04)

Owner asked "is any of this accurate?" of a Forced-Seller score. It was ~38 and
almost entirely fabricated: `ui/radar_panel.py` fed hardcoded widget DEFAULTS to
the scorer (a pre-checked "HUD loan matures Mar 2027", "taxes current" from an
input defaulting to 0), and `core/radar_v2.py` emitted distress points for
MISSING data ("no permit history" â†’ 75, "no deed record" â†’ 30). So every empty
property scored the same confident number off invented inputs.

Rules:
- A component with no underlying data must be UNKNOWN (contributes nothing),
  never a default score. Absence of a permit record is not "no reinvestment";
  absence of a deed is not a tenure; an unchecked input is not "current". Model
  it as `known=False` and exclude it from the total â€” don't let it add OR
  dilute.
- The UI must not manufacture inputs. Default every not-yet-wired signal to "not
  on file" and make the user opt in to entering one; a widget's default value is
  not data.
- Distinguish "checked and found nothing" (known, low) from "never checked"
  (unknown). A 0 that means "no data" must never render as an all-clear â€” show
  NO DATA and say what feed would fill it.
- When a score aggregates several signals, show coverage (how many had real
  data). A confident number built on 1 real signal and 5 defaults is a lie of
  omission.

## Lesson â€” prune the known, never the unknown (2026-08-03)

Owner directive: only 10+ unit properties matter. The safe implementation
deletes rows whose unit count is KNOWN to be under 10 â€” and nothing else. A
units-NULL row is not "probably a house": Portsmouth's whole roll is
units-NULL and those rows are the learner's anchors and next cycle's
classification targets; pruning the unknown freezes every blind city at
zero forever. Second half: before pruning, snapshot the full roll into a
compact `parcel_index`, because two consumers need every parcel â€” the
verified badge's power to REFUTE (the roll row that says 4 units when a
user claims 48 is exactly the row the prune removes) and the learner's
citywide denominators. Filtering a dataset that downstream guards use as a
DENOMINATOR silently weakens those guards â€” audit every aggregate consumer
before shrinking a table. The prune stays non-destructive because
muni_records remains the rebuild source.

## Lesson â€” a size gate is not a semantics gate (2026-08-03)

Richmond's first discovered "roll" was `Undeveloped_Parcels_Richmond_
Virginia`: 6,570 records (over the 5,000 plausible-roll floor), correct
fields, geo-verified in-city â€” and by definition containing zero apartments.
Every numeric guard passed; the layer's NAME was the only tell. **Validate
the name against subset vocabulary (undeveloped/vacant/study/CZM/flood...)
as its own check** â€” count, fields, and location all measure whether data is
real, none measure whether it is the POPULATION you need. Sibling lesson
from the same cycle: Portsmouth's real 36K roll carries no geometry, which
silently capped crosswalk matching at address-only and starved the use-code
learner (7 anchors over 5 codes â€” never lower the evidence bar to fix that;
fix the anchors). The statewide VGIN layer now doubles as a geometry
supplement for coordinate-less rolls: same APNs, coords merge on. And in
locality filters, FIPS beats names â€” Virginia has both a Richmond City and
a Richmond County.

## Lesson â€” a test fixture that mirrors the code inherits its bugs (2026-08-03)

The Portsmouth use-code learner never ran in production: it queried crosswalk
columns `legacy_id`/`r8_id` where the real table has `legacy_property_id`/
`r8_property_id`, and a bare `except sqlite3.Error` renamed the column error
to "no crosswalk yet". The end-to-end test PASSED the whole time â€” its
fixture hand-built a crosswalk table with the code's wrong column names.
**When a test needs a table another module owns, create it through that
module's own writer** (`persist_crosswalk`), never by copying the consumer's
expectations into a CREATE TABLE. And when catching a broad exception class,
print the exception â€” "no crosswalk yet" was a guess wearing the costume of
a diagnosis. Same failure family as the freshness stamp: the system reported
a plausible benign state while silently doing nothing, and only a
falsifiable end-to-end path (fixture through the real writer) exposes it.

## Lesson â€” killing a supervised process is not stopping it (2026-08-02)

The updater killed the PIDs on 8501/8502 before syncing. Under NSSM that
does the opposite of stopping: the supervisor restarts the service within
seconds, so it came back MID-SYNC still running the old code, and nothing
restarted it after â€” the stale-version failure the kill was added to fix,
recreated by the supervisor the next milestone introduced. **When a process
manager owns the process, go through the manager** (`sc stop` /
`Restart-Service`), and re-audit every raw `taskkill`/`kill` whenever
supervision is added around something that used to run bare. The updater now
detects service mode and ends with `deploy-swap.ps1` instead of starting
with a kill.

## Lesson â€” a rename into a dict literal can silently delete a key (2026-08-02)

The de-id sweep renamed `src_aln` â†’ `src_8r` inside `config.COLORS` â€” which
already had an `src_8r`. Duplicate keys in a dict literal are legal Python;
the first value vanishes with no warning, no linter complaint, and the UI
happily rendered the wrong provenance color for weeks. **When a sweep renames
an identifier INTO an existing namespace (a dict literal, a table, an enum),
grep for the target name first â€” a rename can be a collision.** Permanent
guard: `test_no_dict_literal_in_config_has_a_duplicate_key` walks config.py's
AST and fails on any duplicate literal key. Same-day sibling fix: the record
badge color now resolves through `config.spine_provenance_color()` (grey
pre-flip, teal post-flip), and the inventory cross-ref index limit was raised
to clear the 19K-row backbone (a limit sized to the old table undercounts
"matched" silently after the flip).

## Lesson â€” a forced re-run needs a budget and a resume point (2026-08-02)

The generation-token fix (below) forces a full re-scrape â€” which at 4 sources
Ã— every favourite Ã— ~3s politeness throttle is HOURS, inside an hourly
autopilot cycle that has phase0/alerts/preflight queued behind it. Fixing
"the work never runs" by scheduling unbounded work just moves the failure.
**Whenever a fix triggers a large catch-up job, ship the pacing with the
fix**: a wall-clock budget, per-item commits so a kill keeps what was paid
for, a per-item done-marker (tagged with the generation) so the next cycle
resumes instead of restarting, and the freshness stamp withheld until the
whole set is covered. `core/listings_pull.py` is the pattern:
`TIME_BUDGET_S` / `_recently_attempted` / stamp-on-completion-only.

## Lesson â€” a cache key must name the code, not just the inputs (2026-08-02)

The rent gate sat at 1 of 18,928 for a month. The favourites-key fix shipped
on 2026-08-01, the next hourly pull stamped itself fresh, and every cycle
since printed `[listings] fresh ... skipping`. The stamp meant "something was
pulled recently over this same favourite set" â€” never "pulled by this code."
The fix was live in the tree and could not run.

**Any freshness/skip key must include a generation token that the author bumps
when the step's output changes.** `PULL_GENERATION` in `core/listings_pull.py`
is that token; fold the same idea into any new caching step. Same family as
"create-if-absent is not a migration": shipping the code is not the same as
the code taking effect, and only the second one moves a gate.

Second half of the same failure: the skip line said "fresh," which reads as
health regardless of whether the table holds 18,000 rows or one. **A line that
justifies skipping work must state what it is protecting** â€” the count is now
in the message, so a stuck pull is visible in the daily report instead of
needing a query to find.

## Lesson â€” don't ACTIVE-health-check an upstream that's usually down (2026-08-04)

The Windows Caddyfile active-health-checked both blue-green upstreams every 3s,
but the green slot (8502) is normally not running. Result: caddy-err.log got a
"connection refused" line every 3 seconds, which buried the certificate and
error lines exactly when we needed to read them during go-live. Use PASSIVE
health (`lb_try_duration` + `fail_duration`) for an upstream expected to be
absent much of the time; reserve active polling for upstreams that should always
be up. A log you can't read is nearly as bad as no log â€” noisy INFO drowns the
ERROR you're hunting.

## Lesson â€” ingestion needs dedup, temperature=0, and a leaf-based count (2026-08-04)

Owner uploaded the same T-12 several times and saw "6 fields" then "9 fields",
plus duplicate history rows. Three separate causes, worth remembering:
- No content-hash dedup meant every re-upload re-ran (and, for PDFs, re-billed
  an API call) and appended a row even when 0 new fields were written. Fix:
  `file_content_hash` + `find_prior_ingestion`, skip unless Overwrite.
- The extraction LLM call left `temperature` at the API default, so the same
  PDF could extract slightly different values run-to-run. Always pin
  `temperature=0` for extraction/classification â€” anything meant to be a pure
  function of its input.
- "fields written" counted top-level keys, so a whole nested block counted as
  1 and a null counted as written. Count leaves, skip nulls.
- The headline "6 vs 9" was mostly a red herring: the SAME file had been run
  under different TYPES (t12 vs om), and each type is a different extractor
  writing a different key set. When a count looks wrong, check whether the
  inputs (here, the document TYPE) were actually the same first.

## Lesson â€” a port-forward target must be RFC1918, not just "not loopback" (2026-08-04)

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
  loopback/link-local is not enough â€” 100.64.0.0/10 (Tailscale, cellular
  CGNAT) and public addresses also pass that weaker test.
- On any box that might run a VPN/mesh (Tailscale, ZeroTier, WireGuard),
  interface-metric ordering can rank the virtual NIC first. Never trust
  "first interface" for a physical-LAN answer.
- When auto-detecting a value the operator will act on blindly, print the
  alternatives too, so a wrong pick is visible instead of silently followed.
- Tailscale is present on this deployment (100.113.210.35). It's the simpler
  remote-access path for Brian/Peter (no port-forward, no cert); the public
  domain + Caddy path is only needed for outside users.

## Lesson â€” an auto-save that rebuilds a model can loop forever (2026-08-04)

The Underwriting tab faded in/out on its own at 4% CPU / 0% disk â€” not
performance, not the file watcher, but an infinite rerun loop in the render.
The pattern to recognize (and never write):

    new = Model.model_validate({...SOME fields...})   # loses the others
    if new != loaded:
        save(new); st.rerun()

`_render_dials` rebuilt the deal from ONLY the dial widgets, but `DealState`
also has `selected_levers` + concurrency metadata (`row_version`, `updated_by`,
`updated_at`). model_validate reset those to defaults, so a once-saved deal was
never equal to the rebuild â†’ auto-save + `st.rerun()` fired every render â†’
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

## Lesson â€” "fading with nobody touching it" is a rerun from OUTSIDE the browser (2026-08-04)

Chased a "tab keeps fading / shows Photo Upload from another tab" report as a
tab-switch ghost twice (keyed container v5.19.1, then discriminating CSS
v5.20.2) â€” both missed. The owner's third clue settled it: "just keep coming
and going without me touching anything." A pane that fades and repaints with no
user input is not a rendering bug â€” it is the app being RE-RUN repeatedly. In
Streamlit a rerun happens only from (a) a widget interaction, (b) `st.rerun`,
(c) `st_autorefresh`/`run_every`/a component returning a changing value, or
(d) the file watcher when `runOnSave` is on. There was none of (a)-(c) in the
tree â€” grep confirmed no `run_every`, no `setComponentValue`, no fragments â€” so
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
  restarted â€” say so in the handoff, or the owner "still sees it."
- A keyed-container switch DOES cleanly unmount the old section (proven by an
  AppTest: switch tabs â†’ the old tab's button is gone). So a persistent ghost
  is not the switch; look for what repaints the old DOM â€” here, the auto-rerun.

## Lesson â€” a keyed container does not stop the stale-DOM ghost (2026-08-04)

The property sub-tabs ghosted: switching to Underwriting showed the Subject
header's "Photo Upload"/"Open Folder" bleeding in, faded. v5.19.1 wrapped the
dispatch in `st.container(key=f"ptab_section_{active_tab}")` on the theory that
a per-tab key makes Streamlit unmount the old section cleanly. It did NOT â€” the
owner reported the exact same ghost after. The key changes React identity, but
that is irrelevant to the symptom: on a switch Streamlit does a server round
trip and keeps the PREVIOUS run's DOM on screen marked `data-stale="true"`,
painted faded, until the new render lands. The ghost is the outgoing section's
own leftover, shown during the round trip â€” a keyed container cannot make it
vanish faster because the new content simply isn't there yet.

Two things this cost:
- **Don't reach for `st.tabs` as the fix.** It has no round-trip ghost (switch
  is client-side CSS), but it snaps back to the first tab on ANY in-tab widget
  rerun â€” dragging an Underwriting slider bounces you to Subject. That is why
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
re-rendering in place â€” otherwise the cure strobes the thing the user is using.

## Lesson â€” a second editor for the same field is one deal.json, not two (2026-08-05)

First-user feedback asked for a new "Input" tab where users type the first
numbers â€” but purchase price and NOI are already edited on the Underwriting dial
board. Two independent editors for the same fields is how you get a deal that
says one thing here and another there. The Input tab (`ui/input_tab.py`) avoids
that by owning NO state of its own: it seeds new deals from the shared
`build_default_deal` (extracted from `render_underwriting`), and it writes
through the exact `save_deal(..., expected_version=deal.row_version, ...)` path
the dial board uses, editing via `model_copy(update=...)` so the FR-9.3.1
concurrency metadata survives. Rule: when you add a second surface that edits an
existing record, route it through the existing load/default/save helpers â€” never
let it grow a parallel copy of the state or the defaults. Also: it uses an
explicit `st.form` submit rather than auto-save-on-change, so the new surface
structurally cannot reproduce the dial board's old rerun/fade loop.

## Lesson â€” a keyed segmented_control ignores `default`; move the value, not the param (2026-08-05)

First-user feedback: clicking the Purchase Price KPI should jump to the
Underwriting tab. The obvious wiring â€” an `<a href="?ptab=underwriting">` â€” does
**not** move the section selector, because the property sub-tabs are a
`st.segmented_control(key="ptab_sel")` and a keyed widget reads `default` only on
its FIRST render; once session_state holds a value, changing the `ptab` query
param is ignored. The fix that actually switches: the KPI links to a *separate*
`?goto=<key>` param, and `app._sticky_property_tab` consumes it BEFORE the widget
is instantiated â€” `st.session_state["ptab_sel"] = labels[idx]` then
`del st.query_params["goto"]`. Writing the widget's own session key ahead of
instantiation is the only reliable programmatic way to move a keyed Streamlit
control. Rule: to drive a keyed widget from code, set its session_state value
before the widget line runs â€” never rely on `default`/`index` after first paint.

## Lesson â€” an interactive control that reads Postgres must degrade, not crash (2026-08-05)

The owner-contact popover (People block, `ui/v2_theme_05292026.py`) shows
resolved skiptrace POCs â€” which live in the Postgres `poc_records` store, absent
on the single-user desktop path. The read is funnelled through
`_load_resolved_pocs`, which returns **None** (â†’ "Run Resolve Contacts" pointer)
for every unreachable-store case: no `property_id`, no `org_id` in session, `pg`
not configured, or any exception from `load_pocs`. Rule: any UI control that
touches the pilot-only DB must assume the DB isn't there and fall back to the
always-on card data, never raise into the render path. The inspector renders on
every property view â€” one unguarded `load_pocs` would blank the whole right rail
on the owner's own machine, where there's no Postgres at all.

## Lesson â€” a deterministic export reuses the computed data, it doesn't recompute (2026-08-05)

First-user feedback: investors want the deal's *numbers* in a spreadsheet, not
just the Artifact Engine's Word prose. The Excel export (`core/excel_export.py`)
takes the exact `data` dict `ui.exec_summary._build_summary_data` already
produced (DealState + `cf` cash-flow projection + verdict + metrics) and lays it
into three sheets. It does **not** re-run `build_cashflow`/`run_waterfall` â€” a
second computation path is a second place for the download to drift from the
screen, and "the spreadsheet says X but the app says Y" is exactly the trust
break we can't afford. Rule: an export is a *view* of already-computed state.
Pass the data in; never let the export own a second copy of the math.
Corollary: it's deterministic on purpose (unlike the LLM artifacts) â€” a
spreadsheet is a model, and a model that changes between downloads is broken.

## Lesson â€” the installer knows the answer the operator is guessing (2026-08-02)

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


## Standing rule — Inbox -> KB drop folder (owner directive 2026-08-11)

Applies to every autopilot/analysis session that sweeps mail, on every run. Not optional.

**Produce.** Sweep Gmail (bmccune@gmail.com) and O365 (Brian@eight-rock.com) for new deal mail; ingest to the KB; skip newsletters and non-deal mail. For EACH ingested email write one JSON file, filename = message id, shaped per `core/inbox/kb_drop.py`: keys `external_id`, `from_email`, `from_name`, `subject`, `received_at` (ISO 8601 with offset), `body` (plain text), plus optional `fields` object (`name`, `address`, `city`, `state`, `units`, `asking_price`, `cap_rate` — any subset) when deal facts are already extracted (curated, confidence 0.9). Dedupe on `external_id`.

**Land.** `C:\WORKBENCH_V5\data\inbox_kb\` on the host. Lanes in order: (1) device-bridge grant on `C:\WORKBENCH_V5` then commit files into `data\inbox_kb\` — note the grant cannot be requested programmatically and cannot be added mid-session in a cloud task; (2) git intake lane — commit JSONs to `data/inbox_kb_intake/` on origin/main, `ingest_git_intake` V5.38.3.0.0+ lands them host-side; (3) if neither lane is live, package the JSONs and hand them to the owner as a file. Never leave records only in a session workspace.

**Verify, every run.** Read `reports/inbox-sync-latest.txt` and report the counts in the run summary (files / records / ingested / linked / failed). Processed files move to `inbox_kb\processed\`. If mailbox connectors are unavailable to the session, say so plainly rather than skipping silently. Append delivery status to project doc `claude/inbox-kb-log.md`.
