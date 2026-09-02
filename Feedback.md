# Feedback.md — durable corrections, preferences, and process lessons

**Owner:** Brian (bmccune@gmail.com) · **Project:** Eight Rock Workbench (workbench_v5)

## How to use this file (the contract)

1. **Session start:** read this file BEFORE doing any work and apply every
   lesson below. CLAUDE.md points here; that pointer is what makes this
   self-executing.
2. **Session end / wrapup:** re-read the session, cross-reference against
   this file to avoid duplicates, append new durable learnings **with date
   stamps**, commit and push. In this environment, `git push` to
   workbench_v5 main IS the re-upload — Claude does it, not Brian; a lesson
   that isn't pushed does not exist.
3. **Update as frequently as necessary**, not just at wrapup — the moment a
   correction lands, write it. The 2026-08-11 VB waste happened because a
   solved problem lived only in an old chat.
4. **Overflow rule:** anything too long for a short memory edit belongs here.
   This file is the long-form memory. CLAUDE.md holds project/technical
   lessons; Feedback.md holds how-to-work-with-Brian lessons and
   cross-cutting process rules. When in doubt, write it in both.

---

## Communication (standing, owner directive)

- **1–2 sentences** on what was done. Then **Next actions**, then **What I
  need from you** — omit either heading if empty. No recaps, no restating his
  request, no process narration. Evidence only when asked or when reporting a
  failure he must act on. (Standing; reaffirmed every session.)
- Answer the actual question first, plainly ("Is Richmond 100% in the
  database?" → "No —"), then what's being done about it. (2026-08-11)
- When something is his to do, give the exact paste-ready command/env line,
  not a description of it. Include the fallback trigger condition ("only if
  the report still shows 403"). (2026-08-10)

## Working style (standing, owner directive)

- **Self-serve first, always.** Never ping-pong scripts, questions, or
  lookups back to Brian if it can be done from here (read the repo, search
  the code, run the check, search the transcripts, search the web). The ONLY
  things to bounce back: what physically lives on his machine and nowhere
  else (host DB, host logs, browser F12 captures, .env secrets, account
  signups). (2026-08-07, reaffirmed harshly since)
- **Be aggressive.** "Do not be polite — go attack and fetch this data."
  No self-imposed throttles (a weekly discovery gate got overridden to every
  cycle). When he says GO GO GO / don't stop: ship working increments
  end-to-end, don't stop at specs or proposals. (2026-08-09/11)
- **Bias to outcome commitments.** "In the DB by morning" means: wire every
  concurrent path tonight, arm a self-check before his morning, and have the
  single unblock step ready to hand him if an external gate holds. (2026-08-11)
- Decisions he delegates ("you tell me", "make this decision decisively")
  should be MADE, with the basis stated in one line — not returned as
  options. (2026-08-09)

## Never do work twice (the most repeated correction)

- **Search before deriving.** Before designing any integration or answering
  "how do we get X", check IN ORDER: CLAUDE.md/Feedback.md, the code
  registries (MUNI_FEEDS, SALES_SOURCES, KNOWN_ROOTS), reports/, the local
  chat transcripts (`/root/.claude/projects/*/`), then the web. Brian runs
  parallel Claude sessions — work products arrive from them as pasted specs;
  implement those verbatim instead of re-verifying. (2026-08-11, after the
  VB/Spatialest day-long waste and the Norfolk/Chesapeake re-derivation)
- **Record cross-session findings the moment they land** — in CLAUDE.md
  (technical) and here (process). An endpoint verified in another chat that
  isn't written into the repo WILL be re-hunted at full cost. (2026-08-11)
- When Brian pastes a verified spec from another session, his paste IS the
  spec — wire it, test it, push it. Don't re-search, don't ask for helper
  specs. (2026-08-11)

## Data-pipeline rules (paid for in failures)

- **[OK] ... 0 records from a once-full feed is a BREAK, not a success.**
  The VB Property_Sales_view silently died for weeks this way. Treat zero
  where thousands stood as a failure line in every report. (2026-08-11)
- **Size the pull before paginating**; log expected vs written. A silent
  truncation must be impossible to mistake for "done". (2026-08-10)
- **Never delete existing rows on a transient empty pull.** (2026-08-10)
- **Never dismiss a prior probe note without re-testing it.** The registry
  said data.richmondgov.com was 403-gated; I waved it off as stale and the
  first host run proved it right. (2026-08-11)
- **Verbatim storage + alias vocabularies beat per-source field maps.** New
  sources should land raw records; extend the shared key sets
  (sale_history, phase0 aliases) instead of writing adapters' mappings.
  (2026-08-10)
- **The traceback names the layer — read the host log before patching.**
  The sign-in 500 was Authlib/Caddy (infra), not app code; a gate try/except
  would have fixed nothing. Ask for the one log only Brian's machine has,
  with the exact command to fetch it. (2026-08-10)
- Headless scripts don't inherit the app's .env — solved CENTRALLY in
  data/pg.database_url(); never re-solve per script. (2026-08-09, 3rd
  recurrence finally killed it)
- Windows host + firewalled build container: pulls run on the HOST via
  autopilot; the container can't reach city portals, ArcGIS, Socrata, or the
  host DB. Verify sources via WebSearch + host-run sized logs, never by
  curling from the container and concluding "down". (standing)

## Municipal-data techniques (standing)

- **F12 → Network → XHR is the standing discovery technique** for any
  municipal portal; copy-as-cURL captures the headers that get past WAFs.
  Browser-like User-Agent is the first lever against 403s; Socrata app token
  (ER_SOCRATA_APP_TOKEN) is the second. (2026-08-09/11)
- Vendor truth beats vendor assumption: "same pattern everywhere" broke on
  contact — VB=Esri transaction table, Norfolk=Socrata FY snapshots,
  Chesapeake=XLS LandBook join, Suffolk=Spatialest on the community host.
  Hunt each locality's actual shape. (2026-08-11)
- Fiscal-year-stamped datasets (Norfolk) go STALE silently — roll the FY id
  yearly; the FY stack recovers history. (2026-08-11)

## Product/targeting decisions (owner-made, don't relitigate)

- **Hottest 50 multifamily markets, NOT biggest 50 by population** — M&M NMI
  universe, wave-ordered. Berkadia = cross-check, not ranking. (2026-08-09)
- App branding is "Multifamily Property Workbench" — never "Virginia".
  (2026-08-09)
- Comp-overlap anchor tuning is BACKLOGGED at the ~67% ceiling — do not
  resume without his say. (2026-07-30, standing)
- Morning report always includes: pending-user approvals, per-metro property
  counts, gate movement, step failures with fixes pushed. (2026-08-09)
- GRANITE is archived read-only; everything needed was folded into
  workbench_v5. Never push there; recoverable by unarchiving if ever needed.
  (2026-08-10)

## Process hygiene

- Bump WORKBENCH_VERSION + CHANGELOG on every change; record lessons in
  CLAUDE.md in the SAME commit as the fix. (standing)
- Host publishes hourly; expect push races — `git pull --rebase` then push.
  (standing)
- Arm a send_later self-check before any "by morning" commitment; verify
  outcomes from the published reports, not from optimism. (2026-08-11)

---

## Deadlines and deliverables (2026-08-11)

- **An outcome deadline gets a deliverable artifact, not a status promise.**
  "Review it all at 3AM" -> a dedicated published report
  (reports/richmond-review.txt) that answers the review in one read, exits
  non-zero while ANY gap remains, and prints each gap's exact unblock. Plus a
  self-check armed to fire BEFORE the owner's deadline so fixable gaps get
  fixed first. Reuse this pattern for every future "by <time>" commitment.
- **Numbers he'll act on should be env-tunable, not hardcoded** (tax rate ->
  ER_RICHMOND_TAX_RATE), so a rate change never needs a code change.

## UI provenance (2026-08-11)

- **Data shown in the UI should link back to where it came from** - a HUMAN
  page (dataset page, portal), never a raw API endpoint. Owner asked for it
  on sales ("click a sale -> the website that reported it"); assume the same
  preference for future surfaced data. Mapping lives in core/sale_links.py -
  add an override whenever a new source's machine URL isn't human-readable.

## Directive handling (2026-08-11)

- **When Brian re-pastes a standing directive verbatim, execute its cycle
  immediately** - it's a trigger, not a reminder to acknowledge. (This
  Feedback.md contract re-paste = run the wrapup cycle now.)
- A repeated instruction ("Load the Richmond Data now" sent twice) means
  confirm what is already running in one line and spend the turn on the NEW
  part of the message (the sale-links ask), not on re-explaining the old.

## Done means done (2026-08-11, owner: "Not review. I want it done.")

- **The data is the deliverable; a report is a byproduct.** Never frame an
  outcome commitment around the artifact that documents it. When one path to
  "done" has an external gate (a 403, a token, a portal), BUILD A SECOND
  INDEPENDENT PATH on different infrastructure in the same session (Richmond:
  Socrata domain + the assessor's own rva.gov monthly files) so "done" does
  not hinge on a single unblock.

## First-contact iteration (2026-08-11 overnight)

- **First contact with a new source almost always fails on shape, not
  availability** - tonight: Drupal /media/<id> links (no extension, then an
  HTML landing page, THEN the file), year-per-tab workbooks, a form-derive
  default that made every parcel "MF". Budget at least two fix-push cycles
  between first pull and a deadline, and verify each cycle's actual output -
  the sized log lines are what make each failure diagnosable in one read.
- **Never let a derived default masquerade as evidence**: derive_8r_form
  returns "garden" for everything, so "form IS NOT NULL" = the whole city.
  Counts shown to the owner must be filtered on primary evidence (units),
  with coverage printed next to them.

## Closed-form headlines vs the engine (2026-08-31)

- **A panel that computes its own closed-form headline will disagree with
  the engine, and the tell is a metric that refuses to move** — check
  whether the number is a per-unit ratio in disguise before believing it.
  The Value-Add CAPEX tile read $2.30/CAPEX-dollar for 2 units or 200
  because unit count cancelled out of its formula; the owner's schedule
  edits moved nothing in the header. The fix is never a better formula in
  the tab: put the state on the model (DealState), teach the engine
  (build_cashflow), and make the panel report the engine's own with/without
  difference. Guard the seam with an AST test over every call site.

## Session log (append-only, date-stamped)

- **2026-08-11:** File created (owner directive). Seeded with the standing
  corrections above, consolidated from CLAUDE.md history, the owner's global
  directives, and this session's corrections (do-work-twice ×2, Richmond 403
  dismissal, VB 0-records silent break, "answer the question first").
- **2026-08-11 (wrapup):** Appended deadline-deliverable pattern,
  UI-provenance preference, directive-handling rules. Cross-checked: no
  duplicates against existing sections. Pushed to workbench_v5 main (the
  persistence step - done by Claude, never pending on the owner).
- **2026-08-11 (post-3AM verify):** "Rows on hand" is not "data usable": the
  76,976 rva.gov Public Data Set rows all landed as ORPHAN properties - their
  parcel key column (PID) had no alias, so zero merged onto the COR/VDEM
  parcels and the unit/tax gaps stayed open while section 2 looked complete.
  A count next to a source URL proves ingestion, not the join. The Richmond
  review now prints per-source field-mapping health and cross-source apn
  overlap (section 2b) so a dead join names itself. Also fixed: the muni
  sweep deleted all ~477K sales-puller rows as "retired" every cycle
  (nightly full re-download; a one-night host outage would have made the
  review run with silent holes).
- **2026-08-31:** Value-Add CAPEX wired into the returns engine
  (V5.64.1.0.0) — the closed-form-headline lesson above; renovation plan
  moved onto DealState, all 8 build_cashflow call sites pass it, AST seam
  guard added, 27 tests proven red against pre-fix code.
- **2026-08-27 (production data loss, self-inflicted):** Owner: *"We had many
  users - they're gone now. What happened?"* `uv run pytest` on the OWNER'S
  SERVER emptied the live pilot database on 2026-08-18. `tests/conftest.py`
  loads `.env` so the Postgres suites can find a connection; on that machine
  `.env` IS production, and six suites open each test with `TRUNCATE users,
  organizations, audit_log ... CASCADE`. Lost: 5 user accounts, both orgs, 3
  deals, 37 CRM contacts, 41 *paid* skip-trace records, 81 inbox messages, 53
  activity rows, 17 audit entries. The owner's own login re-bootstrapped him
  as the first user, so it looked like "the users vanished", not a wipe.
  Fixed by `tests/pgguard.py` (a destructive suite may only run against a
  database whose NAME says it is disposable) + `restore-pilot-db.ps1`.
  Three process lessons, all costlier than the code fix:
  1. **Never run the test suite on the owner's machine.** The server runs the
     app. Tests run here, or against a scratch database, never both.
  2. **Do not state an inference as a finding.** Asked on 8/26 where the users
     were, I answered "nobody else has ever signed up" - I had verified only
     that the list was unfiltered. The rows had been deleted. That guess cost
     a day and told the owner his memory was wrong when it wasn't. Say what
     was checked, and what remains unchecked, separately.
  3. **When printing a config file, print the line needed - not the file.**
     Asking for `secrets.toml` to read one database URL dumped the live Auth0
     client secret and cookie secret into a chat transcript; both had to be
     rotated. Mask by default, request the narrowest thing that answers the
     question.
- **2026-09-01 (owner notes):** (1) Auth0 client secret + cookie secret
  ROTATED by the owner - never mention that exposure again. (2) "Row
  recovery report" meant nothing to him - I had named a deliverable after
  its mechanism instead of its outcome. Name things by what they do for
  him ("get back the emails the restore overwrote"), never by internal
  plumbing. (3) The Suffolk FOIA deadline check is expected to keep
  running until records arrive - the one-shot fired 8/28, so the daily
  brief carries the Suffolk question forward until resolved.

- **2026-09-02 (the Richmond that wasn't):** Three weeks of unit-count work
  targeted a feed that turned out to be Richmond, CALIFORNIA - added by
  name ("Richmond GeoHub") without ever reading one raw record. The
  jurisdiction proof was in every row the whole time (Cal-Fire FHSZ codes,
  Contra Costa APNs, Illinois mailing addresses). Lessons: (1) NEVER admit
  a data source by its name - verify a raw record's jurisdiction (coords
  in the city bbox, state-specific fields) before ingesting; the bbox
  check existed and the org walk bypassed it. (2) When a join keeps
  failing against expectations, read the RAW DATA before building a
  cleverer join - the geometry bridge, the apn shapes, and the address
  pass were all correct engineering against the wrong-city premise.
