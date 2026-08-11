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

## Session log (append-only, date-stamped)

- **2026-08-11:** File created (owner directive). Seeded with the standing
  corrections above, consolidated from CLAUDE.md history, the owner's global
  directives, and this session's corrections (do-work-twice ×2, Richmond 403
  dismissal, VB 0-records silent break, "answer the question first").
- **2026-08-11 (wrapup):** Appended deadline-deliverable pattern,
  UI-provenance preference, directive-handling rules. Cross-checked: no
  duplicates against existing sections. Pushed to workbench_v5 main (the
  persistence step - done by Claude, never pending on the owner).
