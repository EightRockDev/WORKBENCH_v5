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

## V5.8.6.0.0 — 2026-07-28  ·  Phase 0 round 5: address-point units + proximity matching
Host round 4: match 41.9%, covered-cities 70.6%, +13 footprint recoveries —
and two decisive discoveries in the unmapped keys:
- **Address-point feeds carry units as ROW MULTIPLICITY.** Chesapeake's
  `UNIT` and Norfolk's `unit_number` fields mean those feeds emit one row per
  APARTMENT sharing a parcel id. The spine builder now derives
  `units = rows per (parcel, feed)` when no explicit unit field exists —
  max across feeds so overlapping sources never double-count, and an
  explicit unit field always wins. The report prints "units derived from
  address points".
- **Proximity last-resort matching**: a large complex's marketing pin and
  its parcel centroid can sit hundreds of meters apart; after address and
  strict-radius passes fail, the nearest **multifamily** entity within
  0.25 mi now matches (never a random house — MF-only), reported as
  "by proximity".
- Round-4 aliases: `PRPRTYDSCRP` → use code; `UNIT`/`unit_number` (address
  designators, NOT counts) and NN's current/previous value + subdivision
  keys join the ignore list.
- 4 new tests. Suite: 674 passed; 4 pre-existing data-dependent failures
  unchanged.

---

## V5.8.5.0.0 — 2026-07-28  ·  Feed discovery round 2: geo-verification
The first discovery run on the host found real gold — Chesapeake's own
Address Points layers carry **units** (score 14) — but also exposed the trap:
ArcGIS Online search returns look-alike layers from OTHER cities (Hampton's
"candidates" were Chesapeake blast-zone parcels). Ingesting those would
poison the spine with wrong-city, wrong-FIPS ids.
- **Every candidate is now geo-verified**: 5 sample records are pulled from
  the layer and checked against the claimed city's bounding box. Records in
  the wrong city → candidate rejected and printed as such. Point and polygon
  geometries both handled; layers whose samples carry no coordinates pass
  with a "geo-verify inconclusive" note.
- Unit-bearing layers now outrank unit-less ones (+3), and only the top 2 per
  city are written, so Chesapeake's Address Points beat everything else.
- 2 new tests (wrong-city rejection with the exact Hampton/Chesapeake shape;
  in-city pass with units ranked first). Suite: 670 passed; 4 pre-existing
  data-dependent failures unchanged.

---

## V5.8.4.0.0 — 2026-07-28  ·  Phase 0 round 4: feed discovery + range addresses
Host round 3 proved the pipeline (Newport News 119/121 matched, 98%) and
isolated the two remaining blockers: five cities have **no unit-bearing feed**
(394 of 639 legacy rows), and Norfolk matching is dragged down by ALN-style
street-number **ranges**. This round:
- **`discover-feeds.bat`** → `scripts/discover_feeds.py`: probes the public
  GIS portals of Virginia Beach, Chesapeake, Hampton, Portsmouth and Suffolk
  (known org roots + ArcGIS Online search), walks every service and layer,
  scores fields against the spine's alias vocabulary (a layer must carry a
  parcel id AND units/use-code), and writes qualifying layers to
  `data/feeds_extra.json`. Runs on the server — the build environment can't
  reach city portals.
- **`pull-muni.bat`**: runs the municipal ETL (built-in + discovered feeds)
  into `data\workbench.db`. `etl_munidata.py` now loads `feeds_extra.json`
  automatically, ignoring unknown keys.
- **Range addresses**: legacy "700-780 Granby St" now keys on the first
  number, matching the assessor's parcel.
- **Dual-radius footprint**: dense districts over-merged at ~200 m (legacy
  208 vs 8R 453); the unit check now accepts agreement at either the full or
  half radius.
- **Covered-cities match rate** in the report: separates "parsing problem"
  from "feed missing" (NN 98% proves parsing; the blended rate is a feed
  problem).
- 6 new tests (discovery scoring/walking offline, range keys, covered-rate).
  Suite: 668 passed; 4 pre-existing data-dependent failures unchanged.

---

## V5.8.3.0.0 — 2026-07-28  ·  Phase 0 tuning round 3 (footprint totals + per-city truth)
Host run 3: match 39.7% (was 31%), overlap 24.5% (was 1.7%). This round fixes
the two structural causes the report exposed:
- **Multi-parcel complexes**: big communities span parcels with *different*
  street numbers (700/710/720 Acqua Dr), which address grouping can't
  reassemble. Unit checks now get a second chance via the **footprint total**
  — every 8R parcel within ~200 m of the legacy point — and the report counts
  how many disagreements that resolves.
- **Per-city breakdown**: the parity report now shows legacy → matched →
  spine-multifamily counts per city, flagging cities whose municipal feed
  carries **no usable multifamily data** (Virginia Beach's sales layer,
  Chesapeake's boundary layer; Hampton/Portsmouth/Suffolk have no live feed).
  That is the real ceiling on the blended match rate — and the to-do list for
  new feed wiring.
- **Norfolk addresses are FIVE fields**: number + number-suffix + direction +
  name + type ("921A W 21st ST") now assemble fully. `usedscrp` and
  `residential_finished_living` (sqft) mapped; grantor/postal/zone keys join
  the ignore list.
- 3 new tests. Suite: 663 passed; 4 pre-existing data-dependent failures
  unchanged.

---

## V5.8.2.1.0 — 2026-07-28  ·  Phase 0 tuning round 2 (P0-2 crash + Norfolk 3-part addresses)
- **Fixes the `KeyError: '8R-51550-...'` crash** in the comp replay: a legacy
  property whose 8R match carries no unit data (Chesapeake's feed has none)
  fell outside the multifamily comp pool; the replay now looks subjects up in
  the full entity set and skips those outside the pool — never crashes.
- **Norfolk addresses are three fields, not two**: number + street *name* +
  street *type* (`700` / `Acqua` / `DR`) now assemble into one address.
- Round-2 aliases from the report: `USECD`, `CLASSDSCRP` (use code),
  `RESFLRAREA` (sqft), `ADDRESSZIP`, `PARNO`, `improvement_year_built`
  (lowest-priority vintage). Deed-book/census/acreage bookkeeping keys join
  the ignore list, so the tuning report shows only real gaps.
- 3 new regression tests (the exact crash, 3-part assembly, round-2 aliases).
  Suite: 660 passed; 4 pre-existing data-dependent failures unchanged.

---

## V5.8.2.0.0 — 2026-07-28  ·  Phase 0 tuning round 1 (from the first real host run)
First run on the real 3.5M-record municipal database: P0-1 passed (719,981
parcels, 5,416 HR multifamily), P0-2 didn't (31% match, 1.7% comp overlap).
All four causes identified from the report and fixed:
- **Norfolk's split addresses**: the feed carries the house number in
  `property_street_number` separate from the street name — no Norfolk address
  could ever match. The number is now joined on (new `address_number` field).
- **Condo-fragmented complexes** ("700 Acqua: legacy 258 vs 8R 1"): a
  community recorded as dozens of 1-unit parcels at the same situs now
  aggregates into ONE entity (units summed, coordinate centroid) before
  matching — with a proximity guard (~250 m clusters) so distinct properties
  sharing a junk address, and PO boxes, never merge.
- **Comp replay pool**: was drawing from all 719K parcels; now multifamily
  entities only, matching the legacy pool's `units ≥ 10` basis.
- **New aliases from the host report**: `MAP_PARCEL`, `PROPCLASS`, `CLASSCD`,
  `property_class_description`, `OWNERNME1`, `current_total_value`,
  `effective_year` (losing to a real `yearbuilt` via new alias priorities),
  `lrsn`. Bookkeeping keys (OBJECTID, SHAPE.*, links, legal, sale fields) are
  excluded from the "no mapping yet" report so it shows only real gaps.
- 8 new tests covering each fix. Suite: 656 passed; 4 pre-existing
  data-dependent failures unchanged.

---

## V5.8.1.0.0 — 2026-07-28  ·  Phase 0 step P0-2: shadow parity
- **`core/phase0_parity.py`** — the numbers the P0-3 cutover decision is made
  on (spec §7.3): every legacy multifamily row matched to the 8R spine by
  normalized address (abbreviation-aware; apartment designators dropped —
  the parcel is the key) with a ~120 m lat/lng fallback; unit-count and
  year-built agreement scoring with the worst disagreements named; and the
  spec's **50-deal comp-set replay** — comp sets built from BOTH spines with
  the production radius/bucket parameters, overlap measured by matched
  identity against the ≥90% gate. Avg-rent delta reported against the ≤5%
  gate where both sides carry rents (absent 8R rent signal is reported, not
  failed). Read-only toward both spines.
- **`run-phase0.bat` now runs P0-1 + P0-2 in one click**: builds the spine,
  then — when the legacy `properties` table is present in the same database —
  prints the full parity report and gate verdict.
- Verification: 8 parity tests (identical worlds pass at 100% match,
  address-style bridging, lat/lng fallback, missing-row penalty, unit
  disagreements named, divergent geography fails the gate, empty spine safe,
  50-subject cap) + an end-to-end CLI run on a dual-table database (P0-1 PASS
  → P0-2 PASS). Suite: 650 passed; 4 pre-existing data-dependent failures
  unchanged.

---

## V5.8.0.0.0 — 2026-07-28  ·  Phase 0 execution, step P0-1: the 8R property spine
- **`core/phase0.py`** — builds `properties_8r` from the self-sourced
  `muni_records` municipal pulls (spec §7.3 P0-1). Handles both feed shapes in
  the wild (Norfolk Socrata flat records; ArcGIS `attributes`/`geometry`
  nesting) through a generic attribute-alias table (APN/GPIN/PARCELID…,
  LIVUNIT/UNITS…, YRBLT/YEARBUILT…). IDs are the deterministic
  `8R-{FIPS}-{APN-hash}` from `core/spine.py`, with geohash provisional IDs
  when a parcel number is missing; forms/market taxonomy re-derived per §7.2;
  provenance `8r`. Idempotent — re-runs refresh in place.
- **Coverage gate**: the report computes P0-1's ≥95%-of-HR-multifamily gate
  and prints PASS / not-yet, plus a per-city list of **attribute keys that
  didn't map** — the tuning loop for feeds with unrecognized schemas.
- **`run-phase0.bat`** — double-click; finds the municipal database
  (`data\workbench.db` or `ER_WORKBENCH_DB`), builds the spine, prints the
  gate. Explains exactly what to copy from the v2.4.1 machine when the 3.9M
  municipal rows aren't on this host yet.
- **`phase0-sweep.bat`** — the AC-P0-1 verification sweep: word-boundary-aware
  case-insensitive ALN scan across repo files, filenames, SQLite stores and
  deal folders. Read-only. Run against this repo it currently reports **569
  references in 40 files** (matching the spec §7.1 inventory) — the number
  P0-3/P0-4 must drive to zero.
- Verification: 11 tests (both feed shapes, deterministic IDs across
  rebuilds, provisional fallback, gate arithmetic incl. unusable-record
  penalty, SFH excluded from the gate, unmapped-key reporting, spine rows
  pass `record_is_clean`) + an end-to-end CLI run on synthetic municipal
  data (60/60, gate PASS). Suite: 642 passed; 4 pre-existing data-dependent
  failures unchanged.

---

## V5.7.0.2.0 — 2026-07-27  ·  Per-account Python environment (ends the .venv ownership wars)
- **Fixes `failed to remove directory ... .venv ... Access is denied`.** The
  Python environment used to live inside the shared app folder; whichever
  Windows account created it owned it, and any other account could neither
  use nor delete it. The environment now lives **outside the repo, one per
  account** (`%LOCALAPPDATA%\EightRockWorkbench\venv`, via
  `UV_PROJECT_ENVIRONMENT` set in `_find-uv.bat`); the Windows service gets
  its own under `C:\ProgramData`. First run per account rebuilds it (about a
  minute). The old `.venv` in the folder is ignored and can be deleted
  whenever convenient by the account that owns it.
- Verified: uv builds and runs this exact project from an external
  environment path (105 packages, app imports clean).

---

## V5.7.0.1.1 — 2026-07-27  ·  uv auto-install without winget
- The uv auto-install now uses the official standalone installer
  (`astral.sh/uv/install.ps1`) first — winget is frequently broken or
  uninitialized on a freshly created Windows account (msstore agreement
  prompt + 0x80071130), exactly what the brian2 account hit. winget remains
  the fallback.

---

## V5.7.0.1.0 — 2026-07-27  ·  Launchers work from any Windows account (uv resolution)
- **Fixes `'uv' is not recognized`** when a second Windows account (brian2)
  runs a workbench set up under another (BrianT) — uv installs per-user, so
  the new account's PATH doesn't have it.
- New shared helper **`_find-uv.bat`**: checks PATH, this user's install
  spots, then **every profile on the machine**, and finally **auto-installs
  uv via winget** for the current account. `start-workbench`,
  `update-workbench` and `diagnose-skiptrace` all use it; the service
  installer and inbox setup PowerShell scripts scan all profiles the same way.
- Note: the first run under a new account may take a minute while `uv sync`
  rebuilds the environment for that account.

---

## V5.7.0.0.1 — 2026-07-27  ·  Updater works from any Windows account
- `update-workbench.bat` now adds its own folder to git's `safe.directory`
  list before syncing, so a folder created under one Windows account
  (BrianT) can be updated from another (brian2) without the "dubious
  ownership" refusal. Error text no longer blames the network for git errors.

---

## V5.7.0.0.0 — 2026-07-27  ·  §9 serving, step 1: Windows service + office-network access
- **`install-service.bat`** (self-elevating) → `deploy/windows/install-lan-service.ps1`:
  installs NSSM if needed, registers the **EightRockWorkbench** Windows service
  (auto-start at boot, auto-restart on crash, rotated logs in `logs/`), binds
  Streamlit to `0.0.0.0:8501`, opens the firewall for **private networks only**,
  and prints the LAN URLs. Refuses to install from a OneDrive/Dropbox path
  (spec §9.2). `uninstall-service.bat` reverts cleanly.
- **Passcode gate** (`core/session.require_passcode`): with `ER_APP_PASSCODE`
  set — the installer requires one, ≥6 chars — every visitor sees a passcode
  screen before anything else, in every auth mode (dev-login included), with a
  constant-time comparison. Interim protection until Auth0/Entra OIDC lands;
  no-op when the variable is unset, so local dev is unchanged.
- `docs/ACCESS.md`: new "Office-network access" section.
- Verification: 6 gate tests (no-op when unconfigured, blocks, wrong-code
  stays blocked, unlock persists across reruns, whitespace forgiven, gate runs
  before auth branching). Suite: 631 passed; 4 pre-existing data-dependent
  failures unchanged.

---

## V5.6.2.1.0 — 2026-07-26  ·  Location-independent install (movable folder)
- **The workbench folder can now live anywhere on disk.** Every double-click
  launcher (`start-workbench`, `update-workbench`, `setup-inbox`,
  `set-skiptrace-key`, `diagnose-skiptrace`) previously hardcoded
  `C:\WORKBENCH_V5`; they now resolve their own folder (`%~dp0`) and pass it
  to the PowerShell scripts, so moving the folder breaks nothing.
- **`ER_PROPERTIES_ROOT`** (optional, in `.env`): points the deal-folder store
  (`Properties/`) at any path — second drive, NAS — independent of the app
  folder. Unset keeps the classic sibling layout.
- Everything else was already relocation-safe: `.env`, `data/workbench.db`,
  the ETL resolver and git are app-relative; PostgreSQL is a separate service
  reached via `DATABASE_URL`. Caveats documented: don't place the folder in
  OneDrive/Dropbox (spec §9.2 — sync corrupts SQLite/git), move `Properties/`
  alongside (or set the override), and delete `.venv` after a move so `uv`
  rebuilds its absolute paths.
- Suite: 625 passed; the 4 pre-existing data-dependent failures unchanged.

---

## V5.6.2.0.0 — 2026-07-26  ·  "Pull from this computer" — uploads without the browser
- **The 0-byte upload dead-end is gone.** When the browser sends an empty file
  (cloud-only OneDrive placeholder, drag-out of an email preview — the content
  never reaches ANY website in those cases), the panel now points to a new
  **📂 Pull from this computer** picker instead of just explaining the problem.
- The picker reads the document **straight from the server's disk**, so where
  the file is stored genuinely doesn't matter: it lists the newest documents
  from the property folder, Downloads, Desktop and Documents automatically
  (with size + timestamp so an empty stub is visible), accepts any pasted file
  or folder path, copies the chosen file into the deal folder, and runs the
  exact same extraction pipeline. A Python disk-read also triggers OneDrive's
  own download of placeholder files — the one thing a browser can't do.
- Upload and disk paths now share one extraction runner, so QA reports,
  provenance, and the no-API-key panel behave identically on both.
- Verification: headless end-to-end drive of the picker — typed path →
  Extract click → 6 T-12 fields committed to sources.json ("no AI used"),
  plus auto-scan discovery with no typing. Suite: 625 passed; the 4
  pre-existing data-dependent failures unchanged.

---

## V5.6.1.1.0 — 2026-07-26  ·  Bulletproof uploads (empty / legacy / mislabeled files)
- **Fixes "could not extract text from ... .xlsx"** on the reported upload. The
  file card showed **0.0B** — the browser sent an empty file (typical of a
  cloud-only OneDrive/SharePoint placeholder or a drag straight from an email
  preview). The app accepted the 0 bytes and failed downstream with a useless
  message.
- **0-byte uploads are now caught at the door**: the panel refuses them with a
  plain explanation (open the file once / Save As to Desktop, re-upload) and
  never overwrites a previously-stored good copy. A second backstop inside
  `ingest_document` catches empty files arriving by any other path.
- **Spreadsheets are routed by CONTENT, not file extension.** xlsx is a zip
  (`PK`), legacy xls is an OLE2 file — the readers are chosen by those magic
  bytes, with fallback to the other reader. This fixes: real `.xls` files
  (xlrd), `.xls` bytes mislabeled `.xlsx` and vice versa (common from PM
  systems), and openpyxl's refusal to open a good xlsx that merely has an
  `.xls` filename. Corrupt bytes fail with a named cause + "re-save as .xlsx"
  hint — never a traceback.
- Verification: 4 new hostile-file tests (0-byte xlsx/pdf/csv, garbage bytes,
  mislabeled extension round-trip) + full-app headless boot. Suite: 625 passed;
  the 4 pre-existing data-dependent failures unchanged.

---

## V5.6.1.0.0 — 2026-07-26  ·  Excel/CSV ingestion without an API key
- **Fixes "Extraction failed: ANTHROPIC_API_KEY not set"** on Excel rent-roll
  upload. A spreadsheet is structured data — running it through a language
  model added cost, latency, a key requirement, and a failure mode for a task
  a parser does perfectly. Per §11 (LLM-optional core), tabular files now
  parse **deterministically first**; AI is only needed for PDFs and layouts
  the parser can't recognize.
- **`core/rent_roll_parser.py`** — rent-roll parser (header detection across
  many real-world column spellings, currency/date coercion, title and totals
  rows skipped, status inferred from tenant when no status column, first
  matching sheet in a multi-sheet workbook) and a conservative label-matching
  T-12 parser (rightmost totals column; requires the revenue/opex/NOI
  backbone or returns nothing — never a half-parsed statement). Both emit
  confidence 0.98 and still pass through the Module E extraction-QA gate.
- The parsed `rentRoll` block is committed **unwrapped** (a wrapped summary
  would have crashed the rent-roll tiles); scalar fields keep full provenance
  wrapping.
- **No key + unparseable/PDF** now shows an actionable panel — what still
  works without a key, where to get one, and a paste-it-here form that saves
  to `.env` — instead of a raw red error.
- Verification: 13 new parser/routing tests (incl. the exact reported
  scenario: `Crossroads ... Rent Roll ... .xlsx` with no key ingests
  successfully) + headless renders of the no-key panel and the full
  xlsx → ingest → sources.json → rent-roll-UI chain. Full suite: 621 passed;
  the 4 pre-existing data-dependent failures unchanged.

---

## V5.6.0.0.0 — 2026-07-26  ·  Module E: Doc AI & Underwriting hardening (§6.3)
**V5-P4 second half — extraction QA, anomaly detection, named stress tests,
DD→verdict tightening. All deterministic (§11): zero model calls.**

- **`core/extraction_qa.py`** — deterministic validation per document type.
  T-12: revenue/expense lines must tie to printed totals, NOI is definitional,
  loss lines can't exceed GPR (sign-error catch). Rent roll: unit rows tie to
  the stated count, occupancy math closes, per-unit rent/sqft sanity bands
  (decimal-slip catch, offending units named). OM: PPU ties to price/units,
  cap ties to NOI/price, percent-vs-fraction band. Cross-document (§6.3 by
  name): rent-roll unit count ties to the OM; rent-roll potential rent ties to
  T-12 GPR; OM in-place NOI vs T-12 ("underwrite the T-12" note). Per-field
  confidence walk flags anything under 70% for human confirm. `run_qa()` →
  report with `blocking` = errors or low-confidence fields.
- **`core/rent_roll_anomalies.py`** — the four spec'd detectors: below-comp
  units (vs the property's own floorplan median — ordinary loss-to-lease does
  NOT fire, proven by test), duplicate unit numbers, lease-expiration clusters
  (≥25% of expirations in one month), RUBS-as-rent (charges≡rent despite
  other income; repeated flat premium over market). Findings name the units.
- **`core/stress_overlays.py`** — named scenarios wired to the real pipeline
  (calc→waterfall→IRR): **2008-style** (2yr zero rent growth then half-speed,
  vacancy +300bps, exit cap +100bps), **COVID-style** (1yr flat, +300bps y1
  vacancy incl. collections stress, cap unchanged), **insurance shock** (+40%
  insurance line ≈ +3% opex permanent, expense growth +100bps). Failure bar =
  the sensitivity 12% LP-IRR flag; an incomputable LP IRR (capital never
  returned) counts as failed, not skipped. Deltas vs base reported.
- **`core/verdict_tightening.py`** — bidirectional DD→verdict per spec: open
  hard dealbreaker ⇒ NO-GO; CRITICAL risk ⇒ NO-GO; HIGH risk ⇒ one tier down;
  REJECT/FURTHER_DILIGENCE recommendation or non-IC-ready DD caps GO at WATCH
  ("a DD finding downgrade can move GO → WATCH automatically"); a failed named
  overlay caps GO at WATCH; blocking extraction QA caps GO at WATCH. Only ever
  tightens — clean signals never upgrade a thin deal.
- **UI**: Exec Summary shows the tightened verdict (with "economics alone read
  GO" note + ⚠ rationale lines) and a **Named Stress Tests** card (PASS/FAIL
  chip, stressed LP IRR, delta vs base). Rent-roll views flag anomalies above
  the tiles. Document ingest runs QA after every commit — blocking failures
  show ⛔ before anyone trusts the numbers ("validated writes only", §7.4).
- **Verification**: 62 new tests (22 QA, 16 anomalies, 10 stress, 14
  tightening) + headless AppTest renders of the Exec Summary and rent-roll
  panels against a deliberately-corrupted deal (dup unit, below-comp unit,
  broken NOI, unit-count mismatch) — all surfaces render and block correctly.
  Full suite: 608 passed; the 4 pre-existing data-dependent failures unchanged.
  DD tightening only engages once dd.json exists, so fresh deals aren't demoted.

---

## V5.5.2.0.0 — 2026-07-25  ·  Market data: find it instead of blaming the operator
- **Fixes "ETL database not loaded. Run `python hampton_roads_etl.py` from
  `hampton-roads-etl/`."** Two bugs behind one message. (1) That standalone ETL
  project is not part of the v5 deployment, so the instruction was a dead end.
  (2) The lookup path was `<module>/../../..`, correct in the v2.4.1 layout
  (`<root>/python_workbench/core/`) but a level too high in v5 where `core/`
  sits directly under the app root — on the pilot host it resolved to
  `C:\hampton-roads-etl\`, so the database could never be found even when
  present.
- **`core/etl_db.py`** — single resolver used by every reader: `$ER_ETL_DB`,
  then `data/`, then `hampton-roads-etl/`, then the legacy v2.4.1 sibling
  folder. Re-resolved per call, so dropping the file in and restarting works.
  `core/calibration.py` carried the same stale path and now shares the resolver.
- **`core/etl_locate.py` + a "Find it on this machine" button** — the operator
  upgraded from v2.4.1, so that database is almost certainly already on the
  host. The app now searches the likely roots (bounded depth, skip-list for
  heavy directories), ranks hits largest-first (a tiny file is an aborted run),
  and copies the chosen one into place. It copies rather than moves, so the
  previous workbench keeps working. Gated to admins: an analyst must not be able
  to write to the app's data directory in a multi-tenant deployment.
- **`ui/etl_notice.py`** — one honest empty state, now used by the Inventory,
  Comps, and Direct-Mail panels, that says what is missing, what it powers, and
  where the app looked.
- Tests: 20 new (path resolution incl. the legacy layout, env override,
  largest-first ranking, depth and skip-list limits, copy-onto-itself no-op,
  admin gating) plus a guard that no `ui/*.py` panel tells the operator to run
  the undeployed ETL script again. 546 passed; the 4 pre-existing
  data-dependent failures are unchanged.

---

## V5.5.1.3.0 — 2026-07-25  ·  Migration unblocked (RLS-blocked dedupe)
- **Fixes "could not create unique index `ux_term_sheets_message` — duplicate
  keys exist"** on databases that accumulated duplicate term sheets before that
  index existed. Root cause: the dedupe `DELETE` that runs first is *itself*
  subject to `FORCE ROW LEVEL SECURITY`. A migration has no tenant context, so
  `current_org_id()` is NULL, the DELETE matched **zero rows** and silently
  no-opped — while `CREATE UNIQUE INDEX` is *not* RLS-filtered, saw every
  duplicate, and failed. The dedupe now runs with `FORCE` toggled off and
  restored inside one `DO` block, so RLS can never be left switched off (and a
  non-owner role degrades to a loud error rather than a silent skip).
- **Also fixes the downstream `relation "mailbox_connections" does not exist`.**
  That table is created in the same transaction as the failing index, so the
  abort rolled it back too — which is why connecting a mailbox failed at the
  final step even though the Microsoft sign-in itself succeeded. One root cause,
  both errors; one startup now heals both.
- **`data/migrate.py` now checks indexes, not just tables/columns.**
  `ux_term_sheets_message` and `ux_inbox_owner_msg` *enforce correctness* (no
  duplicate term sheets on re-sync; per-user idempotency). A missing one is
  silent data corruption, so it counts as drift.
- Regression test: seeds the exact broken state (duplicates + FORCE RLS on),
  proves one `ensure_schema` heals it, that no duplicates remain, and that
  `term_sheets` is left with `FORCE ROW LEVEL SECURITY` still **on**.
- Audited the rest of `db/pilot_schema.sql` for the same class of bug: the
  dedupe was the only DML against an RLS-forced table.

---

## V5.5.1.2.0 — 2026-07-25  ·  One-click inbox setup
- **`setup-inbox.bat` + `deploy/windows/setup-inbox.ps1`** — double-click
  one-time setup for Module D. Generates `ER_TOKEN_KEY` automatically (and
  **never regenerates an existing one**, which would orphan already-connected
  mailboxes) and stores `MS_GRAPH_CLIENT_ID` in `.env`. Prints a masked
  confirmation plus the next two steps. Pure ASCII, per the PowerShell 5.1 rule.
- `docs/INBOX-SETUP.md`: leads with the double-click path, keeps the manual
  commands as the alternative, and adds an AADSTS54005 ("code already
  redeemed") troubleshooting note.

---

## V5.5.1.1.0 — 2026-07-24  ·  Self-healing schema + device-code fix
- **`data/migrate.py` — automatic schema migration on startup.** The app now
  compares the live database against the tables/columns the running code needs
  and, if anything is missing, applies the idempotent `db/pilot_schema.sql`
  itself. This kills an entire class of failure for the operator: pulling new
  code can no longer produce a raw `UndefinedColumn` crash (exactly the
  `inbox_messages.owner_user_id` error reported). Failure surfaces as an
  actionable banner, never a traceback; the guard never drops data.
- **Verified by test** (`tests/test_migrate.py`): a required column is dropped,
  detected as missing, auto-healed — **and the per-user RLS privacy policy is
  proven restored**, since a dropped column cascades the policy away and silently
  losing it would expose private mail.
- **Device-code sign-in fix**: a Microsoft device code is single-use, and a
  Streamlit rerun could re-submit it (AADSTS54005 "already redeemed"). The code
  is now marked spent before submission, the button disables after one use, and
  a friendly "Get a new code" path replaces the raw error.

## V5.5.1.0.0 — 2026-07-24  ·  Module D: per-user mailbox privacy + connect flow
**Security model: private mailbox, shared pipeline.** (Owner requirement.)
- **Per-user RLS**: `inbox_messages` gains `owner_user_id`; `inbox_messages` and
  the new `mailbox_connections` are protected by a `user_isolation` policy that
  requires BOTH `app.current_org_id` AND `app.current_user_id`. A missing user
  context returns **zero rows** — it fails closed rather than leaking. Deals,
  term sheets and CRM contacts stay ORG-visible: the pipeline is shared work.
- **`data/pg.user_connection(org, user)`** — the only way to reach per-user data.
- **`core/inbox/oauth.py`** — OAuth **device-code** sign-in via MSAL (no public
  redirect URL needed yet; the server never sees a password). Requests
  `Mail.Read` only. **Tokens encrypted at rest** with Fernet via `ER_TOKEN_KEY`
  (§8.1 SR-2.4); the DB never holds plaintext. `disconnect()` purges that user's
  stored mail but keeps the org's deals.
- **`core/inbox/__init__.sync_inbox(org, user)`** uses that user's own token,
  falling back to demo fixtures when no mailbox is linked.
- **UI**: connect/disconnect panel with the device code, an explicit privacy
  banner, and **received dates now shown** in the confirm queue and all-mail views.
- **`docs/INBOX-SETUP.md`** — Entra app registration + `ER_TOKEN_KEY` steps.

**Bug fixes (both reported by the owner):**
- **Duplicate term sheets on every Sync** — term sheets are now unique per source
  message (partial unique index + `ON CONFLICT ... WHERE message_id IS NOT NULL`).
- **Missing dates** — `received_at` is now rendered wherever messages are listed.
- **Cross-user ingest collision** — the idempotency key was `(org, provider,
  external_id)`, so a second user syncing the same message id collided with the
  first user's row and tripped RLS. Key is now owner-scoped.
- Tests: **32** in `tests/test_inbox.py`, including a privacy block proving a
  colleague (and an org admin) cannot read another user's mail, that missing user
  context returns nothing, that repeated syncs don't duplicate, and that stored
  tokens are never plaintext.

## V5.5.0.0.0 — 2026-07-24  ·  V5-P4: Module D — Inbox -> Deal Engine (§6.2)
- **Schema**: `inbox_messages` (idempotent on org+provider+external_id), `deals`
  (pipeline records, row_version-tracked), `term_sheets` (lender history),
  `crm_contacts` — all RLS org-private.
- **`core/inbox/classify.py`** — deterministic broker / lender / attorney / LP /
  other classification from sender domain, subject, body and attachment signals,
  with a calibrated confidence and an **ambiguity penalty** (a close runner-up
  lowers confidence, which is what pushes borderline mail to a human).
- **`core/inbox/extract.py`** — deterministic fact extraction: units, asking
  price, cap rate, street address, city/state, deal name; and lender terms
  (rate, LTV, amortization, IO, term, proceeds). Per-field confidences combine
  into a weighted composite; implausible values (99,999 units, a 95% cap) are
  rejected, and an unlabeled dollar figure scores low on purpose.
- **`core/inbox/engine.py`** — the **§6.2 confidence gate**: high-confidence mail
  creates/updates a pipeline record automatically; anything below the bar is
  **queued for one-click human confirm and never silently written**. Ingest is
  idempotent, follow-up mail updates the same deal, lender mail attaches a term
  sheet, contacts accumulate into CRM and into the Module B relationship graph.
- **`core/inbox/providers.py`** — MailProvider abstraction with a deterministic
  5-message mock fixture plus **live Outlook/Graph and Gmail adapters**
  (`ER_INBOX_PROVIDER=graph` + `MS_GRAPH_TOKEN`), falling back to mock when
  unconfigured — same pattern as the skip-trace vendors (§8).
- **`ui/inbox_panel.py`** (CRM module -> "📥 Inbox → Deal"): confirm queue with
  editable fields, pipeline with stage control, term-sheet history, all-mail view.
- Tests (`tests/test_inbox.py`): **22**, covering classification, extraction,
  the confidence gate both ways, one-click confirm, dismissal, idempotency,
  deal updates, CRM accumulation, and a full mock sync.
- **Fix found by the suite**: `attempt_touch` had no way to pin the evaluation
  instant, so the quiet-hours rule made a test wall-clock dependent. Added
  `now_utc` (a scheduled cadence runner needs it too) and a new regression test
  proving a 22:00-local dispatch is refused and the dispatcher never invoked.

## V5.4.0.1.0 — 2026-07-24  ·  Updater hardening
- `update-workbench.bat` now stops a running app, **hard-resets to origin/main**
  (so a diverged local copy can never block an update), fails loudly on network
  error, then syncs deps and applies the DB migration. `.env` is untouched.

## V5.4.0.0.0 — 2026-07-24  ·  Phase 0 groundwork: Eight Rock native spine (§7.2/§7.4)
- **`core/spine.py`** — the identity + taxonomy that replaces ALN:
  - `property_id(fips, apn)` -> deterministic `8R-{FIPS}-{12 hex SHA-256 of the
    normalized APN}`; regenerable from public records alone, provably non-ALN.
  - `provisional_property_id()` -> `8R-{FIPS}-X{geohash9}` for stock without an
    APN yet (self-contained geohash implementation), plus alias/crosswalk shape.
  - `classify_8r_class()` -> A/B/C/D from Eight Rock's OWN criteria (vintage band,
    rent percentile vs. submarket, permit reinvestment, condition flags) with a
    rationale trail — converts a licensing liability into buy-box IP.
  - `derive_8r_form()` -> garden / townhome / mid-rise / high-rise / small-plex
    from assessor use codes + unit counts.
  - `scan_text_for_aln()` / `record_is_clean()` -> the AC-P0-1/AC-P0-2
    "not discernible" verification, with word-boundary matching so "walnut" and
    "Alnwick" don't produce false hits.
- Tests (`tests/test_spine.py`): 15 passing.

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
