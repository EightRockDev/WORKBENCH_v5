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

## V5.14.6.0.0 — 2026-08-02  ·  flip-day UI reads fixed while it is still dual-run
The last non-gate item on the P0-3 "remaining before the flip" list. Three
finds, one root cause worth pinning:

- **A duplicate dict key had erased a provenance color.** The
  de-identification sweep renamed `src_aln` → `src_8r` in `COLORS` — a dict
  that already had a teal `src_8r` for the self-sourced backbone. Python
  keeps the last duplicate silently, so the reference-survey grey vanished
  and every "property record" badge rendered in backbone teal — the one
  distinction the badge exists to draw. The grey is back as `src_ref`, and
  an AST test now fails on ANY duplicate literal key in config.py.
- **The record badge follows the read seam.** Property Card "db" rows and
  both inventory "matched to property records" counters now color through
  `config.spine_provenance_color()`: grey while `SPINE_READ_SOURCE`
  is "legacy", teal the moment it flips to "8r". Flip day changes one
  config value and the UI tells the truth without an edit.
- **The cross-ref index limit now clears the backbone.** The inventory
  address index capped `list_properties` at 10,000 — sized to the ~2,500-row
  legacy table, silently dropping half the ~19,000-row backbone post-flip,
  with every dropped row counting as "unmatched." Raised to 50,000.

Theme plumbing kept honest: `src_ref` added to the palette extractor and the
Appearance panel's token list, so custom themes cover it.

Mutation-proven: reintroducing the duplicate key fails three named tests.

## V5.14.5.1.0 — 2026-08-02  ·  the re-scrape gets a time budget and a resume point
V5.14.5.0.0 correctly forces a full re-scrape — and that exposed the next
problem before it happened: a full favourites pull is 4 sources × every
favourite at a ~3s politeness throttle, which is hours, and the autopilot
runs the listings step inside the hourly cycle in front of phase0, alerts
and preflight. The first generation-bump pull would have blockaded the very
cycle meant to apply it. Worse, rows were held in memory and inserted only at
the end, so a kill anywhere along the way lost every scrape already paid for.

Three changes, one behavior:

- **20-minute wall-clock budget** (`TIME_BUDGET_S`). When it runs out the
  pull stops, reports how many pairs it deferred, and **withholds the
  freshness stamp** — an unfinished pull is not "fresh."
- **Resume instead of restart.** Each attempt now records its
  `pull_generation`, and the next cycle skips (property, source) pairs
  already attempted this generation within the window. A big favourite set
  converges across cycles; adding a star scrapes only the new property.
- **Each row commits as it lands.** A crash or an end-of-window kill keeps
  everything already scraped.

All three guards proven by mutation: disabling the budget check, dropping the
generation filter from the resume query, and stamping despite deferred work
each fail a named test.

## V5.14.5.0.0 — 2026-08-02  ·  a scraper fix now invalidates the freshness stamp
Every hourly cycle since 2026-08-01 has reported `[listings] fresh (pulled
within 7 days, same favourites) - skipping`, and the P0-2 rent delta has sat
at 26.9% against a 5% gate with `rents_from_listings` at 1 of 18,928.

The favourites-key fix shipped, then the next pull stamped itself fresh — and
the stamp only ever meant "something was pulled recently, over this same
favourite set." It never meant "pulled by this code." So the fix ran once,
against whatever state existed at that moment, and every cycle afterwards
skipped. **A fix that cannot run is indistinguishable from no fix**, and the
report said "fresh" throughout.

`PULL_GENERATION` (now 2) is folded into the fingerprint, so bumping it on any
change to what a pull yields invalidates the stamp exactly the way starring a
property does. Both say the same thing: the last pull is not a repeat of the
one now due. The fingerprint still tracks the favourite set and is still
order-independent, so a reshuffled `_favorites.json` does not trigger a
spurious 18,000-property scrape.

The skip line now also prints the `rent_listings` row count. "fresh -
skipping" read as health for a month while the table held a single row; the
count belongs in the line that justifies the skip.

Guard proven by mutation: dropping the generation from the fingerprint fails
`test_a_scraper_change_invalidates_the_freshness_stamp`.

## V5.14.4.2.0 — 2026-08-02  ·  the installer names its own port-forward target
The router asks which LAN device to forward 80/443 to, and the answer is a
number nobody has written down. Reading it off a router's DHCP table means
picking the workbench out of a list of unlabelled hostnames.

`install-caddy.ps1` now reports it. After registering the service it prints
the machine's own LAN address — the correct answer by construction, since it
is the machine Caddy is being installed on — plus the reminder to reserve
that address in DHCP, because a lease renewal that moves it breaks the
forward silently. It then fetches the public IP from ipify and compares it
against what the domain resolves to, printing a MISMATCH line when the two
disagree.

That last check catches what the Cloudflare-proxy detection cannot: a
grey-cloud record pointing at a stale address. Both are silent failures whose
only symptom is a site that never comes up.

Covered by `test_caddy_reports_the_port_forward_target`.

## V5.14.4.1.0 — 2026-08-02  ·  catch a proxied DNS record before it eats the certificate
Both of the owner's domains sit behind Cloudflare with their A records
**Proxied**. Caddy proves domain control with an HTTP-01 challenge on port 80,
and a proxied record means Cloudflare answers that challenge instead of the
host — so no certificate is ever issued, with no error to show for it, just
retries in `caddy-err.log`.

`install-caddy.ps1` now resolves the domain before registering the service and
says so plainly if the answer falls in Cloudflare's published ranges,
including the fix: switch Proxy status to **DNS only** (grey cloud) for the
`workbench` record. It also flags `192.0.2.1`, the RFC 5737 documentation
placeholder, as not a real host. Neither aborts the install — Caddy retries
indefinitely and will pick the certificate up once DNS is right — but the
operator finds out at install time rather than from silence.

Also reverted a mistake made an hour earlier in the same session: on seeing a
screenshot of `eightrockcapital.com`, every script's default domain was
rewritten to it. The next screenshot showed `eight-rock.com` is the live
domain — real host, SPF/DKIM/DMARC, Microsoft 365 mail — while
eightrockcapital.com still resolves to the placeholder. The original default
was right. One screenshot describes one page, not the whole configuration.

---

## V5.14.4.0.0 — 2026-08-02  ·  the listings step was skipping itself into a corner
Overnight the step reported `fresh (pulled within 7 days) - skipping` — on the
day after it had crashed. `is_fresh` reads the stamp from the last SUCCESSFUL
pull; a crash writes no stamp, so it coasted on an older success. One gate
produced three failures at once: the crash stayed invisible, the schema fix
shipped for it could not run, and the favourites the owner starred were never
scraped.

Freshness is now keyed to the **favourite set** as well as the clock. Starring
a property is an instruction to scrape it, and waiting out a week to honour
that makes the feature look broken while the rent gate cannot move. A
fingerprint of the favourite ids rides along with the success stamp; a
different set re-scrapes immediately, the same set in a different order does
not.

A failed attempt now clears its own freshness claim, so the next cycle
retries rather than reporting "fresh" over a step that never ran.
`run_listings.main` also said "never fails the cycle" and returned 0 — but the
exception escaped before the return, so it failed the cycle regardless. A
comment describing intent is not a mechanism; it now catches, prints the
traceback, and invalidates.

Six tests, both guards mutation-checked: reverting to clock-only freshness and
removing the failure-invalidation are each caught.

**Gates unchanged and unchangeable until this reaches the host** — comp
overlap 66.8% (parked), rent delta 26.9%, `rents_from_listings` still 1 of
18,928. That number cannot move while the step that feeds it is skipping.

The alert-report fix from yesterday is confirmed working, and it immediately
earned its keep: the sweep now reads "NEW this cycle: 0 ... OPEN (carried
forward): **176**". Yesterday's format showed 25 and implied that was all of
them.

---

## V5.14.3.2.0 — 2026-08-02  ·  fix: both installers died on their own cleanup step
`install-service.bat` and `install-caddy.bat` both failed at the same line
with `nssm.exe : Can't open service!`, having installed nothing.

`nssm stop <name>` on a service that does not exist writes to stderr. Under
Windows PowerShell 5.1, redirecting a NATIVE command's stderr with `2>$null`
wraps that output in a `NativeCommandError`, and `$ErrorActionPreference =
"Stop"` makes it terminating. The stop/remove pair exists to make a re-run
idempotent — and it is exactly what broke the FIRST run, on every machine
where the service was not already present. Which is every machine an
installer is for.

Both scripts now route nssm through an `Invoke-Nssm` helper that relaxes the
preference around the native call, restores it in a `finally`, and reports a
non-zero exit rather than dying on stderr. `-Quiet` covers the stop/remove
pair, whose failure on a clean box is expected and meaningless. A failed
`install` now throws, instead of the script continuing through a dozen `set`
calls and finishing with a success message over a service that does not
exist. The same hazard in `install.ps1`'s `cmd /c` variants is fixed too.

Wrapping the call in a PowerShell function introduced a second hazard worth
recording: a function binds tokens starting with `-` as its own parameters,
where `& $exe` passes them through. `-m`, `--server.address` and
`--server.port` were one binding rule from disappearing. Every call site now
passes a single explicit `@(...)` array.

Four new checks in `tests/test_deploy_scripts.py`: no native `2>$null`
anywhere, the helper both relaxes and restores the preference, it is defined
before it is used, every call passes an explicit array, and a failed install
stops the script. Neither bug is visible by reading the script — only by
running it on a clean machine, which is the one thing the owner should not be
doing to find bugs.

---

## V5.14.3.1.0 — 2026-08-01  ·  the AC-11.2 test no longer needs the AI layer
Running the suite in the AC-11.1 configuration — SDK removed from the build —
left exactly one failure, and it was the new AC-11.2 test: it imports
`anthropic` to install a tripwire on the client constructor, which cannot
work when there is no SDK to trip. An AC-11.2 test requiring the AI layer is
the wrong shape; it now skips there, where AC-11.1's own tests already prove
nothing is constructed.

Verified green in all four configurations the product supports:

| Configuration | Result |
|---|---|
| Postgres available | 935 passed, 4 skipped |
| No database | 851 passed, 88 skipped |
| AI layer removed (AC-11.1) | 850 passed, 89 skipped |
| AI disabled (AC-11.2) | 851 passed, 88 skipped |

---

## V5.14.3.0.0 — 2026-08-01  ·  AC-A2 latency guard; every spec AC now has a test
**18 of 18** acceptance criteria in the spec are now referenced by the test
suite. The audit that started this found four with none: AC-11.1/11.2/11.3
(shipped in V5.14.2.x) and AC-A2, the resolution-latency SLA.

AC-A2 is stated honestly rather than faked. The SLA is end to end and most of
its budget is spent inside vendor APIs this environment cannot reach and whose
speed is not ours to control; asserting a wall-clock number against stubbed
vendors would measure the stubs. What IS ours is the pipeline's own cost, and
two regressions there would blow the SLA however fast the vendors are:
per-property overhead creeping up until it eats the 60s budget alone, and
batch cost growing faster than linearly — an O(n^2) sibling scan turns 1,000
properties into hours. Both are now guarded, the second as a RATIO between
batch sizes so the test describes the algorithm rather than the machine.

The threshold took two attempts and the second is the point. It was first set
at 50 ms per property, reasoned down from the 60-second SLA — against a
measured cost of 0.36 ms, which permitted a **139x regression** before firing.
It looked strict and could not fail. Now calibrated at 5 ms, ~14x measured.
Mutation-checked both ways: a +15 ms regression trips the overhead test, and a
genuinely quadratic batch path trips the linearity test.

851 passed, 88 skipped.

---

## V5.14.2.1.0 — 2026-08-01  ·  the AI-off path shows a fallback, not a traceback
V5.14.2.0.0 introduced `AIDisabled` and nothing caught it. An org with
`ai_enabled` off would have hit a raw traceback on the two surfaces that call
a model from the UI — strictly worse than before the flag existed, and the
opposite of what AC-11.2 asks for ("every generative surface offers its
manual/template fallback instead").

Both surfaces now catch it and render the fallback the exception carries:
Document Auto-Ingestion points at hand-entry on the Property Card, the
Artifact Engine at the deterministic Preview block. Two tests assert the
handling exists AND that the fallback text is actually displayed rather than
the error being swallowed.

Verified in a browser with `ER_AI_ENABLED=off`: the app boots and Subject,
Underwriting and Summary all render clean.

---

## V5.14.2.0.0 — 2026-08-01  ·  Section 11: the AI layer is optional, and now provably so
An audit of the spec's 18 acceptance criteria against the test suite found
four with no test. Three are Section 11, the "deterministic core first, AI
last" commitment the whole product architecture rests on.

**AC-11.1** — the deterministic core runs with the AI layer removed — turned
out to hold already: with `import anthropic` blocked outright the suite is
820 passed, 88 skipped. Holding today and being unable to quietly stop
holding are different properties though, so five tests now exercise
underwriting math, distress scoring, the property spine, deal persistence and
T-12 preprocessing with the SDK blocked.

**AC-11.2** was not merely untested — it was **unimplemented**. The
`organizations.ai_enabled` column had existed since the pilot schema was
written, labelled "Section 11 per-org LLM flag", and no code read it. An org
could switch it off and every generative surface would carry on calling the
model. `core/ai_gate.py` is now that switch, wired into all five call sites.

Two deliberate choices. The gate sits on the line that **constructs the
client**, not at the top of each function: a surface that forgets a check at
the top still gets a client, whereas one that forgets the check that *is* the
client cannot. And `AIDisabled` carries the fallback text, because AC-11.2
asks for a manual/template path to be offered, not merely for the call to be
skipped. A settings-store outage means "no opinion" and keeps the default —
an outage must not silently disable a paid feature, nor silently enable one.

**AC-11.3** now tests the real validator: `validate_polish` rejects AI prose
that introduces, drops or moves a number. A changed price in an outreach
letter is a misrepresentation to a seller, not a formatting slip, so a moved
decimal has its own case.

25 tests. Mutation-checked: removing the gate from one surface is caught by
two of them.

---

## V5.14.1.1.0 — 2026-08-01  ·  a run with no database is now green, not red
Ten Postgres suites skipped on `pg.is_configured()`, which only checks that a
URL exists. Pointed at a stopped server, that produced **76 errors and 4
failures** instead of skips. Those four sat red for a whole session and were
each time explained away as environmental — correct, and precisely the hazard:
a real regression surfacing in that block would have been dismissed the same
way.

`pg.is_reachable()` connects once, caches the answer, and gates the suites on
the database being usable rather than merely named. A run without a database
is now **820 passed, 88 skipped, nothing red**; with one, **904 passed**. Red
means red again.

---

## V5.14.1.0.0 — 2026-08-01  ·  SR-2.2 zero-data-training, enforced in CI
The spec commits to this architecturally, not as a setting: "all LLM calls
route through no-training API endpoints ... no customer-data fine-tuning
pipeline exists". A commitment enforced by architecture has to be checked
against the architecture, so `tests/test_zero_training.py` asserts the shape
of the code rather than the behaviour of any one call.

Six checks: model calls occur only at the five reviewed sites; no second
model vendor is referenced anywhere; no fine-tuning or training surface
exists; every call site imports the Anthropic SDK rather than hand-rolling
HTTP; no `requests`/`httpx` POST targets a model-shaped endpoint; and the
allow-list itself cannot go stale (an entry for a file that no longer calls a
model would hide a later reintroduction at that path).

This is the failure mode worth guarding: a second vendor added under time
pressure, or a call that bypasses the shared client, breaks the commitment
**silently** — nothing errors, no output changes, and the only evidence is in
a diff nobody re-reads. Deal data, T-12s, rent rolls and POC records are the
most sensitive material the product touches.

Mutation-checked: a probe file was planted for each violation in turn and
each was caught by its own guard.

Also corrects BUILD-ORDER's note that AC-10.1 was verified — it now points at
the suite that verifies it.

---

## V5.14.0.0.0 — 2026-08-01  ·  AC-10.1 actually verified (cross-org RLS suite)
BUILD-ORDER recorded AC-10.1 as verified. No test existed. The spec asks for
it by name — "a user in Org A cannot read, list, or reference any Org B deal,
document, or LP record, verified by an automated cross-org RLS test suite" —
and this is not a soft requirement: tenant isolation here is enforced entirely
by Postgres policies, not by query filters. `list_messages` runs
`SELECT * FROM inbox_messages` with no WHERE clause at all.

`tests/test_cross_org_rls.py` seeds two tenants and, for **all 15**
RLS-protected tables, proves Org A cannot read, list, update, delete, or
insert-into Org B, and that a connection with no tenant context sees nothing
rather than everything.

Generic by design: the table list comes from `pg_class.relrowsecurity` at
runtime, so a table added later without a policy fails automatically. A
companion test asserts every protected table was actually exercised — without
it the suite would have reported green over the eight tables it could seed,
which is precisely the silent-partial-coverage failure this codebase keeps
producing.

Building it surfaced three real invariants that a hand-written test would have
encoded wrongly:
- `outreach_touches` is append-only by trigger (AC-B2). An exact-row-count
  assertion was the test's error, not the schema's; the property that matters
  is "every row I can see is mine".
- `inbox_messages` / `mailbox_connections` isolate per USER as well as per org,
  so an org-only context correctly sees nothing there.
- `revocations` carries a table-level `e164 OR email IS NOT NULL` check that
  no column metadata exposes.

Mutation-checked twice: dropping the `deals` policy fails the coverage test,
and replacing it with `USING (true)` fails six of the seven.

Suite: **898 passed, 4 skipped, 0 failed** against Postgres.

---

## V5.13.8.3.0 — 2026-08-01  ·  starred properties the scraper was skipping
The owner added favorites to feed the rent gate, which surfaced a bug that
would have blunted exactly that. `listings_pull.favorite_universe()` resolved
favorites by EXACT `property_id` / `legacy_id`, but the Phase-0 rename changed
synthesized ids from `aln-<n>` to `legacy-<n>`. `property_io` normalizes that,
so the UI kept showing older favorites starred — while the scraper matched
exactly and skipped them.

The failure mode is the bad kind: the star still rendered, and the listings
report said `not_found`, which is indistinguishable from a property the
scraper genuinely could not locate. Both call sites now share `_fav_key`.

Confirmed by mutation: the new test passes against the fix and fails against
the original exact-match implementation. Four tests cover the old prefix,
modern 8R ids, two distinct 8R ids not colliding under normalization, and the
empty case.

Given only 4 targets and 2 successes in the last cycle, any favorite the
scraper could not see was a material share of the rent-gate sample.

---

## V5.13.8.2.0 — 2026-08-01  ·  overnight: listings step fixed, alert report made honest
**listings crashed** with `table rent_listings has no column named name`,
taking two successful Zillow scrapes with it — the only source that moves the
rent-delta gate. The table was created by an older build and
`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
every column added to `_ROW_COLS` since was missing. It now reconciles the
column set on every run via `ALTER TABLE ADD COLUMN` (cheap, idempotent) and
prints what it added. Second schema-drift bug in two days after `properties`;
the pattern is recorded in CLAUDE.md.

The fix's tests initially went into `tests/test_listings.py`, which skips
wholesale without `hampton-roads-etl` beside the checkout — so they never ran.
Moved to `tests/test_listings_schema.py`, which depends only on the module
under test.

**alert sweep contradicted itself**: "0 new multifamily" printed directly above
25 `[new_mf]` lines. Both were right — the counter tallies rows inserted this
cycle, the list shows all open alerts — and nothing said so. The report now
separates "NEW this cycle" from "OPEN (carried forward)", and states the true
total beside the capped list ("showing the 25 most recent of 41") so a backlog
of 200 cannot look like a backlog of 25. `count_open_alerts()` added.

Gates unchanged overnight and none of this moves them: coverage 100% (PASS),
comp overlap 66.8% (backlogged at its ceiling — anchor tuning stays parked),
rent delta 26.9% against the 5% gate on 265 pairs. Crosswalk 471 -> 475, rents
stamped 18,764 -> 18,928, still 100% FMR-derived with exactly 1 row from a live
listing. The rent gate cannot close on FMR alone, which is precisely what the
listings crash was blocking.

---

## V5.13.8.1.0 — 2026-07-31  ·  discovery: a parcel roll has to be parcel-sized
Hampton's accepted feed served **716 records** for a city of roughly 50,000
parcels. Nothing about its fields was wrong — address, apn, assessed_value,
use_code, year_built and coordinates all present, score 12 — so field scoring
took it, and Phase 0 then reported Hampton as having no usable multifamily
data. It is a coastal-zone study extract, not the assessor roll.

Discovery now asks each candidate how many records it serves (one
`returnCountOnly` query, a number rather than data) and folds that into the
score. A layer at or above 5,000 records is promoted; below it is demoted and
labelled "too small to be a full parcel roll, probably a subset or study
extract", and listed in the rejected notes. Demoted, not rejected: if a city
has no other candidate, a subset still beats nothing — it just must not
outrank a real roll. The threshold sits below Suffolk's ~30K parcels, the
smallest city in the market, so no legitimate roll trips it. A server that
declines to answer leaves the score untouched rather than looking empty.

This does not by itself find Hampton's real roll — that needs a discovery run
with network access, which only the operator's machine has. It does mean the
run will stop settling for the wrong layer.

---

## V5.13.8.0.0 — 2026-07-31  ·  learn apartment use codes where the roll publishes numbers
Three Hampton Roads cities report no multifamily. The Phase 0 diagnostic
already said why, and it is three unrelated problems, not one:

| City | Records | Cause |
|---|---|---|
| Hampton | **716** | wrong layer — a coastal-zone study subset, not the ~50K parcel roll |
| Portsmouth | 36,464 | full roll, but use codes are bare integers (`9`, `18`, `7`) |
| Suffolk | 0 | no feed discovered |

Portsmouth is fixed here. `core.phase0.is_multifamily` recognises use codes by
TEXT — "Apartment", "Multi Family", "MF" — which works for Norfolk, Virginia
Beach and Chesapeake and is useless against integers. Guessing what `18` means
would be inventing data, so it is **learned** instead: the crosswalk already
links 45 known Portsmouth apartment properties to specific parcels, so the
codes those parcels carry can be counted.

Conservative on purpose, because the failure is asymmetric — sweeping in a
generic code would bury tens of thousands of houses in the comp pool, exactly
what VB zoning "R-40" did by substring-matching "r-4". A code is accepted only
with >= 3 supporting parcels, >= 25% of the city's known multifamily, and
<= 10% of the entire roll (apartments are always a small minority). Every
candidate is printed with its evidence, accepted or not, so a rule can be
audited before it changes what the comp engine sees. Learning runs only for
cities whose codes are opaque AND that currently find nothing; where the text
rules work, it stays out of the way. A known unit count still wins — a duplex
on the apartment code is still a duplex.

The learned map is per-city data in `learned_mf_use_codes`, not code:
Portsmouth's `18` says nothing about Suffolk's `18`. Re-learning replaces
rather than accumulates, so a corrected roll cannot leave a stale rule in
force.

18 tests, including the full Portsmouth scenario end to end — 36K parcels
finding zero multifamily, then the code learned, then those parcels
classifying — and the rejection cases that keep "Residential" out.

Hampton and Suffolk need feed work, which needs network access this
environment does not have: every municipal GIS, Socrata and Census host is
blocked here. That work has to run on the operator's machine.

---

## V5.13.7.1.0 — 2026-07-31  ·  stop depending on winget for NSSM
`install-service.bat` sat at ">> Checking NSSM" with no output, then failed
with "Could not install NSSM". Two faults: winget's output was piped to
`Out-Null`, so a slow install was indistinguishable from a hang, and winget
was the *only* way the script could obtain NSSM — when it fails (stale
package sources, a proxy, or machine policy) the install simply stopped.

winget now runs with its output visible and is tried first, but a failure
falls through to downloading `nssm-2.24.zip` from nssm.cc directly and
extracting the right architecture's `nssm.exe` into `tools\` (gitignored).
That is a 300KB zip with no installer, so it works anywhere outbound HTTPS
does. TLS 1.2 is forced explicitly — Windows PowerShell 5.1 still negotiates
TLS 1.0 by default on some builds and would otherwise fail the fetch. If both
routes fail, the error now gives the exact URL, the exact destination folder,
and says to check the proxy, rather than "install it from nssm.cc".

---

## V5.13.7.0.0 — 2026-07-31  ·  make the go-live steps double-clickable
Two instructions failed the moment the owner tried them, both because they
assumed knowledge the scripts should carry themselves.

`install-service.bat` installed only BLUE, so the go-live plan said to run
the PowerShell script twice by hand — which failed outright: PowerShell does
not execute a script from the working directory without `.\`, and the shell
was in `system32`. It now installs BOTH colours in one double-click, prompting
for the passcode once, and stops on the first failure instead of pressing on.

Caddy had no installer of its own — it was buried inside `install.ps1`, which
also installs Postgres and the app services. `install-caddy.bat` /
`deploy/windows/install-caddy.ps1` now do just the front door, in the order
go-live actually follows: winget-install Caddy, substitute the domain into
`Caddyfile.active`, **validate the config before registering anything**,
report whether 8501/8502 are actually answering their health checks, register
the service, open 80/443, and state plainly that no certificate is issued
until DNS resolves and the router forwards both ports.

---

## V5.13.6.2.0 — 2026-07-31  ·  fix: the Appearance work crashed the topbar
V5.13.4.0.0 shipped a broken `ui/theme_panel.py`. Moving `_identity()` out of
the panel was done as a text-slice edit, and the slice landed the function
directly beneath the `@st.dialog("Appearance")` decorator that belonged to
`open_theme_dialog`. `_identity` therefore *became* a dialog: calling it
rendered a modal and returned None, so `render_avatar_button` raised
`TypeError: 'NoneType' object is not subscriptable` on `_who["name"]` — and
because the avatar renders in the topbar, every page died with a traceback.

Caught by the end-of-session browser check, not by the suite, and the reason
is worth recording: the theme tests deliberately avoided importing
`ui.theme_panel` because it raised under a bare interpreter. That avoidance
was treated as a harness quirk to work around. It was the bug, already
present and already visible. Three tests now import the module, assert
`_identity()` returns a dict rather than None, and assert that
`open_theme_dialog` is the ONLY function carrying `@st.dialog` — verified by
reintroducing the fault and watching them fail.

Second decorator casualty of the same editing style today: `get_connection`
lost `@contextlib.contextmanager` the same way in V5.13.5.2.0 and was caught
by the tests immediately.

Suite: 865 passed, 4 skipped, 0 failed against Postgres.

---

## V5.13.6.1.0 — 2026-07-31  ·  the whole suite is green; guard the RLS superuser trap
Standing up a real Postgres to test the migration turned the 61 long-standing
"errors" in the suite into actual results for the first time. They were
connection failures, not failures — with a database present the full suite is
**862 passed, 4 skipped, 0 failed**, including the four `test_migrate.py`
tests that had been red in every run today.

Four inbox tests did fail at first, and they read like a serious privacy hole:
a colleague reading another user's mail, an admin reading a user's mail, a
missing user context not failing closed. `list_messages` has no WHERE clause
at all — isolation is entirely row-level security.

It was the test environment, and the distinction is worth stating precisely
because the alarming reading was wrong. PostgreSQL exempts **superusers** from
RLS even on tables declared `FORCE ROW LEVEL SECURITY`, and the scratch
database was connected as `postgres`. Re-run against a non-superuser role
owning the database — which is exactly what `install.ps1` creates — all 32
inbox tests pass. Production was never affected: the `workbench` role is
created without SUPERUSER and owns the database, the one combination that
makes FORCE RLS bite.

`tests/conftest.py` now refuses to run at all against a superuser connection,
with an explanation. The false red is the lesser risk; the real one is someone
"fixing" working isolation code to satisfy a test that was never going to
pass. `docs/SETUP.md` documents the local setup that mirrors production.

---

## V5.13.6.0.0 — 2026-07-31  ·  P0.5 last item: SQLite -> Postgres migration + verifier
The pilot's tenancy tables already live in Postgres; the property spine and
calibration tables were still SQLite. `scripts/migrate_sqlite_to_pg.py`
creates the Postgres tables, copies every row, and proves the copy.

Deliberately the BUILD-AND-PROVE half, not the cutover: it never touches the
SQLite side and does not change where the app reads from. Same order Phase 0
uses for the spine — build, prove parity, then flip when the owner chooses.

Verification is three checks, because each alone is defeatable. Row counts
would pass a migration that copied the right NUMBER of wrong rows. Key sets
would pass one that copied the right rows with corrupted values. So it
compares counts, primary-key sets, and a column-by-column sample, with
numeric comparison for floats so SQLite REAL -> DOUBLE PRECISION does not
read as a mismatch. Copies upsert, so a re-run after a partial failure
neither duplicates nor errors.

Two real defects surfaced from testing against a live Postgres rather than
compiling and assuming:
- the primary key was hard-coded per table and `calibration_current` was
  wrong ("metric"; it is "name"). It is now read from the SQLite schema, and
  a composite key raises instead of copying with no conflict target — which
  would have let a re-run duplicate every row.
- copy/verify assumed the SQLite and Postgres table names were identical.
  True in the migration, but it made both untestable in isolation.

Measured: 15,000 rows x 47 columns copied and verified in 1.4s. Ten tests
covering the round trip, re-run idempotency, float precision, nulls, and —
the ones that matter — that the verifier actually FAILS on a missing row, an
extra row, and a changed value. A verifier that only ever reports OK is worse
than none: it turns "we did not check" into "we checked and it was fine".

---

## V5.13.5.2.0 — 2026-07-31  ·  SQLite in WAL mode before the pilot goes multi-user
The pilot runs a blue-green service PAIR against a single `workbench.db`,
with the hourly autopilot writing to the same file. Connections were opened
with `sqlite3.connect(path)` and nothing else, so the database used the
default rollback journal: one writer blocks every reader for the length of
its transaction, surfacing as "database is locked" in the UI. `.gitignore`
has listed `*.db-wal` / `*.db-shm` all along — WAL was intended and never
switched on.

Connections now set `journal_mode=WAL` (readers proceed through a write),
`busy_timeout=10000` (wait for a lock instead of failing instantly),
`synchronous=NORMAL` (safe under WAL — survives a process crash, risks only
the last commits on power loss) and `foreign_keys=ON`. WAL is a persistent
property of the file, so this is a no-op after the first connection, and a
failure to set it degrades to the old journal rather than raising: WAL does
not work on network shares, which is also why the deploy scripts refuse to
run from OneDrive.

Caught while auditing the go-live path, not from a report — with one service
it would rarely bite, and the pilot is about to be the first time two
processes serve the same file.

---

## V5.13.5.1.0 — 2026-07-31  ·  one service-naming scheme across the deploy path
`install.ps1` registered a single service called `Workbench` on 8501 while
`install-lan-service.ps1` puts `WorkbenchBlue` on the same port — running the
full-stack installer and the service installer left two services fighting for
8501, and `deploy-swap.ps1` would have found neither. `install.ps1` now
delegates to the service installer for both colours instead of rolling its
own, retiring any old `Workbench` service it finds.

The service installer gained `-BindAddress`: `0.0.0.0` for direct LAN access
(opens the private-profile firewall rule, as before) and `127.0.0.1` when
Caddy fronts it, which skips the rule *and removes any left by an earlier LAN
run* so the raw port does not stay exposed after switching modes. `-KeepPasscode`
lets the pair be installed with one prompt rather than two.

`tests/test_deploy_scripts.py` now holds the deploy path to the same standard
as the app: ASCII-only (PowerShell 5.1 reads .ps1 as ANSI), balanced
delimiters, every script agreeing on the blue-green names and ports, Caddy
routing to both, per-colour uv environments, localhost mode not opening the
firewall, no stale single-service registration, the swap failing loudly on an
empty machine, and the launcher/updater stopping the process they actually
start. Twenty-five checks over files that are otherwise run once, by hand, on
go-live day — the worst possible place to find a disagreement.

---

## V5.13.5.0.0 — 2026-07-31  ·  read monthly-grid T-12s correctly; blue-green deploy actually works
**T-12 grid extraction (root cause of the 71% expense miss).** A real
statement (Franklin Group / Yardi export, 295 rows x 14 columns) reported
285,532 of operating expenses against its own stated 989,583. The sheet was
being dumped into the prompt as a raw grid: one row per GL line, twelve
monthly columns, and a `Total` column the model had no way to identify. It
read across the months.

`core/t12_grid.py` now resolves the grid deterministically before any model
sees it (section 11 keeps the core LLM-free): detect the header row of period
columns, prefer the statement's own `Total` column, fall back to summing the
periods, and emit an `ANNUAL TOTALS` block ahead of the raw grid. On the file
in question that produces 1,363,689 income / 989,908 opex with the nine
expense categories summing to exactly the printed total. Preferring the
sheet's own Total matters: re-deriving it invites a rounding argument with
the owner's accountant. The raw grid is still passed through, so monthly
seasonality is not lost.

The prompt carried the other half of the failure. It now states that ANNUAL
TOTALS figures are already full-year, that these statements print a summary
AND a repeating GL detail section so categories must be counted once, and —
the big one — that every expense dollar must land somewhere: a combined line
like "Taxes & Insurance" that maps to no single schema field goes into the
closest one with a note rather than being dropped, which is how a partial set
arises. Below-the-line items (debt service, capex, replacement reserves) are
explicitly excluded from opex, and the model must flag its own failure to tie
out instead of silently returning a fraction.

**Blue-green deploy was not installable.** `deploy-swap.ps1` restarts services
named `WorkbenchBlue` / `WorkbenchGreen` and its header cited
`install-lan-service.ps1 -Name/-Port` — parameters that did not exist. The
installer hard-coded one service, `EightRockWorkbench`, on 8501. Running it
twice, as the go-live plan says to, would have re-registered the same service
and left Caddy with a single upstream; the swap script would then have found
neither colour, skipped both, and exited 0 reporting "zero-downtime deploy
complete". Same false-green shape as the updater bug earlier today.

The installer now takes `-Name`/`-Port`, rejects any pair Caddy does not
route, gives each colour its own service, log files, firewall rule and uv
environment (a shared one lets both services sync into the same tree during a
swap), and prints the exact command for the second colour. The swap script
counts what is installed before touching anything: none is an error, one is a
loud warning that restarting means real downtime.

---

## V5.13.4.0.0 — 2026-07-31  ·  batch upload, account identity, honest QA wording
**Multi-file ingestion.** The uploader took one document at a time. It now
accepts a batch: every file is staged to disk first, so one unreadable file
no longer blocks the readable ones beside it, and extraction runs
sequentially because each pass writes the same `sources.json`.

**Removed the "Pull from this computer" picker.** It existed to work around
0-byte cloud-only OneDrive stubs. Brian never used it, and it carried 113
lines of directory-scanning. The 0-byte case now names the offending files
and says what to do (open the file so it downloads locally, then re-drop it)
instead of pointing at a panel that no longer exists.

**Account identity on the avatar.** "How do I know who I'm logged in as?" had
no good answer — the only indicator was a caption at the bottom of the
sidebar with no role information. The avatar tooltip now names the signed-in
user and their roles, and the dialog opens with an identity card: name,
email, roles, admin flag, auth backend, and org. Critically it shows an
active §10.4 role preview in a warning — an admin previewing as another
preset is seeing someone else's app, and that must never be silent.
`identity()` lives in `core.theme_prefs`, not the panel, so it is importable
without a Streamlit script-run context and therefore testable.

**Extraction QA wording.** A tie-out failure read
"1,564,561 vs 1,363,689 expected (14.7% off)", which on a first-ever upload
reads as though the workbench held a prior expectation about the deal and was
contradicting the owner's own statement. Both figures always come from the
uploaded document: one printed on it, one derived by summing what was
extracted. Failures now read "X from adding up the revenue lines vs Y on the
statement's Total Revenue line — 14.7% apart".

---

## V5.13.3.1.0 — 2026-07-31  ·  fix: wrapped sources.json value crashed Underwriting
The Underwriting tab died with `TypeError: '>' not supported between
instances of 'dict' and 'int'` on a real deal. `sources.json` stores values
either bare (`60000`) or provenance-wrapped
(`{"value": 60000, "source": "T12"}`). `totalRevenue` and `totalOpex` were
unwrapped before use, but `t12_fixedCharges.realEstateTaxes` was passed
straight through as `pre_sale_tax` and hit `pre_sale_tax > 0`.

Every sources.json read now goes through one `_scalar()` unwrapper that
handles both shapes and returns None for anything non-numeric, and
`_apply_expense_adjustments` normalizes its own argument rather than trusting
callers — this runs against hand-edited JSON, so a bad file should degrade to
the fallback estimate, not take down the tab. Three regression tests: the
wrapped-taxes crash, bare-vs-wrapped producing identical results, and junk
values falling back cleanly.

Pre-existing, unrelated to the V5.13.3.0.0 concurrency work; it surfaced on a
property whose T-12 had been ingested with provenance stamps.

---

## V5.13.3.0.0 — 2026-07-31  ·  P0.5: concurrent editing is safe (FR-9.3, AC-9.3)
`data/concurrency.py` implemented optimistic concurrency and soft locks in
full — and nothing in the app called any of it. Two analysts on the same deal
silently overwrote each other: `save_deal()` was a blind `write_text`, last
writer wins, no signal to either party. BUILD-ORDER listed the admin page and
concurrency as remaining for P0.5; the admin page was in fact already built
and wired, so this closes the piece that was actually missing.

**FR-9.3.1 compare-and-set.** `DealState` carries `row_version` / `updated_by`
/ `updated_at`, mirroring the Postgres audit columns. `save_deal()` takes the
version the editor loaded and refuses to write when disk has moved past it,
returning a `SaveResult` with the winner's name and their copy of the deal.
Omitting the version keeps last-writer-wins, so the single-user desktop path
and existing fixtures are unchanged. Files predating this load as version 0.

**FR-9.3.2 conflict resolution.** The losing save gets a side-by-side of the
four headline numbers — theirs vs yours — and two explicit buttons. Dial
state is never auto-merged: averaging two people's underwriting would invent
a deal neither of them ran. Lever toggles in the value-add panel are the one
exception and do merge, because they touch a single list field; that merge
re-reads the winner's deal and re-applies only the lever set.

**FR-9.3.3 presence.** The Underwriting tab takes a heartbeat soft lock and
shows who else has the deal open, above the dials. It is advisory — it never
blocks an edit, and it stays silent when Postgres isn't configured, so the
desktop path is untouched.

Honest bound, documented in the code: on the file-backed store the version
check and the write are two operations, not one transaction, so a collision
inside that window can still slip through. The soft lock is what keeps two
editors apart; the version check is what makes a lost update visible instead
of silent. Records needing true atomicity live in Postgres and go through
`optimistic_update()`.

Six tests cover the two-browser case (AC-9.3), the retry path, legacy
unversioned files, corrupt files, and the unchanged single-user path.

---

## V5.13.2.4.0 — 2026-07-31  ·  autopilot actually runs hourly now
The dev cadence was documented as hourly but ran back-to-back all day, which
is why a console window kept reappearing every couple of minutes. The
PowerShell trigger anchored the next run to `(Get-Date).AddMinutes(2)` — a
RELATIVE time — and `reschedule()` fires at the end of every cycle, so each
finish re-armed "+2 minutes" again and the hourly `-RepetitionInterval` was
overwritten before it could ever apply.

The trigger now anchors to the top of the next hour, which is idempotent:
re-registering mid-cycle recomputes the same next run, so cycles land on the
hour regardless of how long one takes. `-MultipleInstances IgnoreNew` already
skips a run that would overlap an overrunning cycle. The `schtasks` fallback
was already `/SC HOURLY` and is unchanged — only the PowerShell path, the one
that actually runs, was wrong.

---

## V5.13.2.3.0 — 2026-07-31  ·  say where the API key comes from
The artifact-engine key prompt said a key was needed and that it must start
with `sk-`, but never said where to get one. It now links
console.anthropic.com/settings/keys, notes the key is shown only once, and
warns that API usage bills separately from a Claude.ai / Claude Code
subscription — the console account needs its own credits.

Also catches the commonest wrong paste: a Claude Code **OAuth token**
(`sk-ant-oat...`) passes a naive `sk-` check and would save happily, then
fail at generation time. It is now rejected at the input with an explanation
of the difference. The old placeholder actively encouraged this by calling it
"the same key you use with Claude Code".

---

## V5.13.2.2.0 — 2026-07-31  ·  the update never actually stopped the app
The owner updated, restarted and hard-refreshed, and the topbar pill still
read a stale version while `config.py` on disk read the new one. The app is
launched as **python.exe** (`uv run python -m streamlit run app.py`), but
`update-workbench.bat` stopped it with `taskkill /F /IM streamlit.exe` —
which matched no process, and whose error was swallowed by `>nul 2>&1`. The
old server kept running with the previous modules resident, so no amount of
browser refreshing could show new code: Python does not reload modules on
an HTTP request.

Both launchers now stop whatever is **listening on 8501/8502** by PID, which
is independent of how the app was started. `update-workbench.bat` then
re-checks the ports and warns loudly if something is still bound instead of
reporting success; `start-workbench.bat` clears a stale server before
launching, so a plain double-click is enough to recover.

This is the root cause of the whole day's "the fix isn't showing up" loop —
including the earlier conclusion that the work had landed in the wrong repo.
That conclusion was correct and separate, but this bug would have hidden the
corrected work too.

---

## V5.13.2.1.0 — 2026-07-31  ·  tell the launcher windows apart
The owner screenshotted a console reading "this window closes itself when
done" in a tab labelled `run-phase0`, with no way to know whether it ever
would. Two different tools looked identical on screen: `autopilot.bat` really
does self-close (`exit /b 0`), while `run-phase0.bat` and every other
double-click launcher end in `pause` and wait for a keypress forever — correct
for a hand-run tool, indistinguishable from a hang.

Each launcher now sets its own window title, so a waiting window says
"press a key to close when finished" in the title bar. Autopilot titles itself
RUNNING then DONE, and writes `reports\autopilot-status.txt` so its state can
be checked without reading a 30-45 minute log.

---

## V5.13.2.0.0 — 2026-07-31  ·  AC-P0-1 de-identification + Appearance panel
The owner opened the app and still saw ALN badges on every Property Card row,
"vs ALN 7.24%" in Calibration, the product still reading QUARRY, a dead avatar,
and the Forced-Seller Radar sitting above the header card. Four fixes:

**De-identification (spec §7, AC-P0-1).** 782 vendor references across 71 files
are down to 25, none of them user-visible. The retired provenance key `"aln"`
now maps to `"8r"` per §7.3, so the Property Card badge reads **8R** and the
legend says "8R Backbone"; `provenance.canonical()` keeps records and deal.json
files written under the old key resolving instead of raising. `data/aln_loader.py`
became `data/legacy_loader.py`; columns `aln_id`/`aln_pull_date` became
`legacy_id`/`pull_date`; the inventory cross-reference columns and filters now
read "Prop …", pairing with the existing "Asr …" prefix.

The 25 remaining hits are back-compat and ingestion machinery, not product
copy: the provider's literal export filenames/sheet/headers (quarantined in one
marked block at the top of `legacy_loader.py`, retiring with that module at
P0-4), the column-migration code that has to name the old columns to rename
them, and the retired-key alias. `ER_LEGACY_DATA_DIR` now overrides the export
folder.

**Schema migration.** `workbench.db` only rebuilds when a source export is
newer than it, so an existing install would have pulled the renamed columns and
failed every write against `properties`. `data/db.py:migrate_legacy_columns()`
renames in place, runs from `ensure_db_synced()`, and is idempotent.

**Favorites.** Synthesized property_ids changed prefix, so `_favorites.json` can
hold entries written under the old one. Matching now normalizes
`<slug>-<digits>` ids to their numeric tail; 8R, UUID and custom- ids still
compare byte-for-byte.

**Subject tab.** The Forced-Seller Radar moved below the property detail — it
rendered first in `app.py` and pushed the header card (photo, name, address,
Favorite) off the top of the tab.

**Appearance panel.** The topbar avatar was a static div wired to nothing. It is
now a button opening a dialog over all 45 theme tokens plus font stacks and a
type scale, saved per signed-in user via `core.storage`, applied in `main()`
before any component reads `config.COLORS`. It also takes a URL: `core/
palette_extract.py` reads a site's stylesheets, infers roles from how each
colour is *used* (a colour in `background` is a surface candidate, one in
`color` is text), derives the rest, and forces text to clear WCAG AA so a
low-contrast site can't produce an unreadable workbench. A contrast warning
guards hand edits.

Verified in a headless browser against the seeded demo inventory: no ALN string
anywhere on screen, badges read 8R, topbar reads QUARRIE on one line, header
card leads the Subject tab with the radar last, and the dialog opens and saves.
Tests 730 passed (38 new), zero new failures.

---

## V5.13.1.4.0 — 2026-07-31  ·  first LIVE HUD pull survives the seeded table
The .env fix worked - the 15:30 UTC cycle saw the token and reached the
HUD API for the first time - and then crashed inserting: the copied
hud_fmr table has 8 columns, the puller's bare `INSERT ... VALUES
(7 slots)` assumed its own 7-column shape. The insert now names its
columns (extra seeded columns stay NULL), and any needed column missing
from a variant table is added via ALTER TABLE first. Also fixed the
follow-on politeness bug the regression test exposed: _stamp's upsert
never updated `description`, so the "copied" stamp would have survived
the live pull and the token would have forced a fresh HUD API pull
every ~35-min cycle; the upsert now refreshes provenance fields, so
one live pull marks the table in-workbench and the 90-day gate holds.
No data was lost in the crash - the pre-insert DELETE rolled back with
the failed transaction.

## V5.13.1.3.0 — 2026-07-31  ·  autopilot can finally SEE the HUD token
core/public_data.py read HUD_API_TOKEN from os.environ, but nothing in
the autopilot path ever loaded .env - only the app side does. So a
token added to C:\WORKBENCH_V5\.env (exactly as instructed) was
invisible to every cycle and the FMR pull skipped forever. The module
now loads .env itself (never overriding real env vars; proven by an
import test that reads a token present only in .env), and the skip
line states WHY ("no HUD_API_TOKEN visible" vs "already pulled live"),
so the next report is a definitive answer instead of ambiguity.

## V5.13.1.2.0 — 2026-07-31  ·  V2 theme ACTUALLY default (verified by rendering)
V5.13.1.0.0 claimed the V2 default but patched a DUPLICATE gate in
ui/components.py; app.py renders through ui/v2_theme_05292026.is_v2(),
which still required ER_THEME=v2 - so the host showed V1 at
V5.13.1.1.0 and the owner caught it. Fixed the real gate (unset OR
empty -> v2; only ER_THEME=v1 restores legacy), pointed the duplicate
at identical logic, and replaced the topbar pill's stale hard-coded
"v2.1.4" with the real WORKBENCH_VERSION. THIS time the fix was
verified by launching the app headless and screenshotting the V2
landing (Quarrie hero, class-chip cards, occupancy colors) before push.

## V5.13.1.1.0 — 2026-07-31  ·  updater no longer collides with a running cycle
`update-workbench.bat` did a full `git reset --hard`, which tries to
rewrite `reports\*.txt` - files the in-flight Autopilot cycle holds
open on Windows. The lock aborted the entire sync ("unable to unlink
old 'reports/pull-latest.txt'"), so the owner could not pull new code
mid-cycle. The updater now syncs everything EXCEPT `reports\`
(cycle output the next cycle rewrites and re-syncs itself anyway) and
sets `GIT_ASK_YESNO=false` so git never stalls on a y/n prompt.

## V5.13.1.0.0 — 2026-07-31  ·  V2 theme is now the DEFAULT
All the redesigned UI (landing cards, compact hero, verdict-graded
stat bar) lived behind ER_THEME=v2 - never set on the pilot host, so
the owner saw the legacy V1 layout despite current code. V2 is now the
default; ER_THEME=v1 restores the old layout if ever needed.

## V5.13.0.1.0 — 2026-07-31  ·  New HUD token forces the first live pull
The 90-day freshness gate would have ignored a newly added
HUD_API_TOKEN until the copied data aged out. A token now forces the
first in-workbench live pull; after that, normal freshness applies.

## V5.13.0.0.0 — 2026-07-31  ·  Section 9 step 2 begins: zero-downtime blue-green serving
The owner's commitment (2026-07-29): before ~25 concurrent users, no
one ever sees a restart. The serving stack now supports it:
- Caddyfile: BLUE (8501) + GREEN (8502) upstream pair with 3s health
  checks against Streamlit's /_stcore/health - Caddy routes to whichever
  color is alive.
- deploy/windows/deploy-swap.ps1 (pure ASCII): restarts the two app
  services one at a time, waiting for each to pass health before
  touching the other; aborts safely if a color never comes healthy.
Built + verified in-repo; goes LIVE when the host runs the serving
install (Caddy + the two NSSM services + domain) - the remaining §9
step-2 pieces are that install plus Auth0/Entra OIDC for public HTTPS.

## V5.12.5.0.0 — 2026-07-30  ·  Listings scraper ported in-workbench (rent-gate data source)
The proven hampton-roads-etl rent scrapers (apartments.com, Zillow,
RentCafe, property sites) now live IN the workbench (`etl_listings/`)
with a workbench-native runner (`core/listings_pull.py`) - the old
runner was welded to a repo layout never deployed to the host, so the
rent gate had no real-rent source. New autopilot step `listings` (after
publicdata, before phase0): favorites-scoped (<20 properties, polite
3s delays, robots-respecting), manual-URL-first, freshness-gated 7
days. Scraped effective rents land in rent_listings and the SAME
cycle's backbone build ingests them through the crosswalk, beating the
FMR estimate - the 26.4% rent delta starts moving toward the 5% gate
the moment favorites are marked. 2 new tests.

## V5.12.4.0.0 — 2026-07-30  ·  UI round 3: the stat bar grades itself
The deal workspace's stat cards showed raw numbers with no signal. They
now grade themselves against the ratified Eight Rock bars: going-in cap
vs GO 7.5%/WATCH 7.0%, 5-yr IRR vs the 15% LP target (watch band within
2 pts), stabilized DSCR vs 1.30/1.10. GO = green value + rail, WATCH =
amber, NO-GO = red - the stat strip reads as an instant verdict before
you open a single tab. Render-verified with all three tones firing.

## V5.12.3.0.0 — 2026-07-30  ·  UI round 2: landing hero compressed
The stacked hero (44px title + 30px subtitle + full quote card) pushed
every property card below the fold. Now one tight band: Quarrie + tagline
left, live inventory count right (gold, monospace), quote collapsed to
a single ellipsized line. Cards are visible on load. Render-verified
end-to-end.

## V5.12.2.0.0 — 2026-07-30  ·  Alert -> Outreach routing (Module C complete)
Spec 6.1's "alert routing to the Outreach Engine" ships: every sweep
alert on the GRANITE Alerts tab has a "📞 To Outreach" button that
routes it into a durable dial queue (`outreach_queue`), and the
Outreach panel now opens with the 🎯 Sweep queue - the routed targets,
oldest first, each one step from Resolve Contacts and the compliant
call list. Worked targets clear with one click; routing and working
are idempotent. This closes the radar-hit -> pierce -> dial loop the
gap map showed as three disconnected surfaces. 1 new test.

## V5.12.1.1.0 — 2026-07-30  ·  Two more panels find their data
The loan-maturity alert panel and the rent-listings panel both
hard-coded the legacy v2.4.1 sibling path (hampton-roads-etl/) instead
of using core/etl_db.py - so they stayed empty on the pilot host even
AFTER the database landed at data/hampton_roads.db. Both now resolve
through the one resolver. With the copied db in place, the Alerts tab's
maturity panel and the listings panel light up on next refresh.

## V5.12.1.0.0 — 2026-07-30  ·  UI overhaul round 1: the property cards
The landing-page cards the owner flagged ("the layout sucks") were the
old link-styled design: name, address, AND every stat value underlined
- a page full of raw hyperlinks with zero hierarchy. Rebuilt:
- No underlines anywhere on a card; name ellipsizes cleanly and turns
  gold on hover; gold accent rail + lift on hover.
- Class renders as a colored chip (A green / B blue / C amber / D red).
- Occupancy is color-coded (>=95% green, 90-95% amber, <90% red) - scan
  the grid and the weak assets jump out.
- Stats row divided from the header; tighter grid density (300px min).
Verified by rendering inject_v2_theme() end-to-end. Next UI rounds:
landing hero/search, deal-analysis workspace.

## V5.12.0.0.0 — 2026-07-30  ·  The workbench feeds itself: in-app HMDA/HUD pullers
New autopilot step `publicdata` (between pull and phase0): the
workbench now pulls FFIEC HMDA multifamily originations + lender
rollups, and HUD Fair Market Rents, straight into the ETL database it
already reads (creating data/hampton_roads.db when absent). No more
file copies from other machines - in Hampton Roads or any future
metro. Chained-cycle safe: freshness-gated (HMDA 30d, FMR 90d - a
fresh table makes the step a seconds-long no-op) and LEI->lender-name
GLEIF lookups persist in a lei_names cache so each lender is resolved
once, ever. HUD FMR needs the free HUD_API_TOKEN in .env (step prints
the sign-up link; the copied hud_fmr table keeps serving until then).
Failed pulls report and never fail the cycle. 4 new tests.

## V5.11.4.2.0 — 2026-07-30  ·  Overlap ceiling declared at ~67%; centroid restored
The 2-cycle deal concluded: centroid 66.9%, largest-parcel 66.4%,
address-parcel 66.4% - measured on live cycles hours apart. Centroid
(the best config) is restored; comp overlap moves to the BACKLOG per
the owner's call. The remaining ~23 points are a replay-methodology
question (assessor-built pools vs a survey vendor's curated list), not
a data-tuning one. Dual-run continues; users unaffected; the nightly
loop keeps measuring for free. Build focus pivots to the visible
queue: in-workbench HMDA/HUD pullers, UI overhaul, alert->outreach.

## V5.11.4.1.0 — 2026-07-30  ·  Owner correction: anchor at the ADDRESS, period
The complex sits where its address sits. The head parcel carries the
cluster's address, so its own geocode is the anchor - never an
outparcel average, never the largest building (V5.11.4.0.0's rule,
reverted same hour). Fallbacks only when the head has no coords.

## V5.11.4.0.0 — 2026-07-30  ·  Comp-overlap fix: anchor complexes at the main building
The last concrete overlap lever: aggregated complexes sat at the MEAN
of every member parcel's coordinates, so scattered outparcels dragged
the pin off the building and re-ranked every distance-based top-12 comp
set. Complexes now anchor at their largest member's parcel (centroid
stays the fallback). The chained cycles measure the effect within the
hour - per the 2-cycle deal with the owner: if overlap moves, 90% is in
reach; if flat, the ceiling is declared at ~67% and comp-overlap goes
to the backlog.

## V5.11.3.1.0 — 2026-07-30  ·  IMMEDIATE cadence: cycles chain back-to-back
Owner escalation: not hourly - immediate. Each cycle re-registers the
task to fire 2 minutes after it ends, so cycles run continuously all
day; the hourly repetition remains only as a safety net if a cycle
dies. DEV_MODE=False restores nightly.

## V5.11.3.0.0 — 2026-07-30  ·  Dev cadence: cycles run HOURLY, not nightly
Owner directive: this is a system under active build, not a deployed
one - nothing waits for 3 AM. The autopilot re-registers itself hourly
after every cycle (clean or not) while DEV_MODE=True in
scripts/autopilot_run.py; flipping it to False restores
nightly-when-clean. Data, alerts, and code fixes now flow all day.

## V5.11.2.1.0 — 2026-07-30  ·  Ownership changes: recorded, not alerted (owner ruling)
A fresh trade is a poor outreach target - trade notifications removed.
Every observed owner transition now records silently to the
`ownership_changes` table (the self-built deed chain feeding the radar
tenure score, pullable anytime); the Alerts tab keeps only actionable
kinds (new_mf, units_jump).

## V5.11.2.0.0 — 2026-07-30  ·  Ownership-change alerts (sale detection)
The sweep now catches the best-timed outreach signal there is: an
owner-name flip on the assessor roll means the property TRADED. Alert
kind `owner_change` shows old -> new owner with units and city;
case/whitespace-only renames are ignored; snapshot migrates in place.
1 new test.

## V5.11.1.0.0 — 2026-07-30  ·  Continuous alert sweep (spec 6.1 monitoring)
Alerts stop being pull-based panels: a deterministic sweep
(`core/alerts.py`) now runs as the 5th autopilot step after every
backbone rebuild, diffing against the previous cycle's snapshot and
persisting durable, deduped alerts (`alerts` table): new multifamily
entities on the backbone, and material unit-count moves (>= 10 units).
First-ever sweep seeds silently. The GRANITE Loans Alerts tab shows the
open hit list with one-click dismiss; sweep results also publish as
reports/alerts-latest.txt every cycle. Next: routing alert hits into
Outreach dial lists. 2 new tests.

## V5.11.0.0.0 — 2026-07-30  ·  GRANITE Loans module v1 (spec 6.1, Tabs 2-5)
The loan module exists as its own surface for the first time — a
sidebar module ("🏦 GRANITE Loans", gated by the existing `granite`
permission) with four tabs:
- **Lenders** — the real lender database: every multifamily lender by
  market, all years, originations, volume, median loan, and the median
  rate spread (pulled by the ETL for months but never read anywhere).
- **Loan Comps** — comparable originations from `hmda_originations`
  (74K+ rows with previously ZERO readers): amount, LTV, rate spread,
  purpose, lender, banded around a deal size. Denied applications are
  excluded.
- **Borrower Intel** — search any owner entity and see its whole
  Hampton Roads footprint on the 8R backbone (parcels, units, assessed
  value, cities), one step from Module A's Resolve Contacts.
- **Alerts** — the C3 loan-maturity pressure panel moves in; saved-
  search alert routing to Outreach is the named next step.
Deterministic data layer in `core/granite_loans.py` (spec 11 - no LLM
in the core), read-only against the ETL db and the backbone, degrades
to empty when a source is absent. 3 new tests.

## V5.10.3.0.0 — 2026-07-30  ·  Publish fix: never git-track the live log
The 2:51 PM cycle ran perfectly (all 4 steps exit 0) but EVERY publish
bounced: code releases landed on origin/main mid-cycle, and the host
could not rebase past them because tracked `reports/autopilot.log` is
held open for append by the running .bat - Windows locks it, so any git
operation rewriting that path fails, leaving the branch behind and every
push rejected non-fast-forward. The next run then could not even update
its code ("local changes would be overwritten") - same file. Fix: the
live log is untracked + gitignored forever; publish() ships a COPY
(`reports/autopilot-run.log`) and self-heals old clones (`git rm
--cached`); stage-1 does the same and force-checkouts so no dirty
tracked file can ever block a code update again. Regression test added.
Recovery on the host: close the stuck window, run update-workbench.bat
once (its reset --hard clears the wedge), and the cycle self-heals.

## V5.10.2.1.0 — 2026-07-30  ·  Regression suite driven green (P0-3 gate prep)
"Full regression suite green" is a P0-3 flip gate. Fixed the four
standing non-Postgres failures: two stale aln_loader tests updated to
the loader's documented contract (ALN-Id fallback rows are KEPT; non-ALN
sheets load as empty, not an error); the Properties/ and workbench.db
smoke tests now skip unless the REAL library/ALN data is present
(a partial checkout no longer fails them); test_listings skips cleanly
when the sibling hampton-roads-etl repo is absent instead of killing
collection. Remaining reds are Postgres-connection-only and pass where
the pilot's Postgres runs.

## V5.10.2.0.0 — 2026-07-30  ·  Nightly cutover preflight
`run_phase0.py` now writes machine-readable `phase0-gates.json`, and a
new 4th autopilot step (`scripts/preflight_cutover.py` →
`cutover-preflight.txt`) reports distance-to-flip every cycle: P0-1/P0-2
gates, crosswalk materialized, rent-signal coverage by source, and a
deal-reference migration dry run, each marked PASS/----, plus the
flip-day runbook when everything holds. The flip stays a deliberate act
- the preflight only reports.

## V5.10.1.0.0 — 2026-07-30  ·  P0-3 round 2: listings rents, deal migration, honest labels
- **Listings → backbone rent ingest** (`rent_signal.apply_listings_rents`):
  scraped effective rents from `rent_listings` (the ETL listings puller)
  now flow onto `properties_8r` through the persisted crosswalk -
  1BR/2BR blend, averaged across sources, `rent_source='listings'`,
  always beating and never downgraded by the FMR estimate. Runs in every
  backbone build; reports its count. This closes the "listings keyed to
  ALN ids" blocker WITHOUT touching the ETL repo.
- **Deal-reference migration tool** (`core/cutover.py`): rewrites
  `deals.property_id` / `outreach_touches.property_id` legacy ids to 8R
  ids via the crosswalk. Never guesses (unmapped ids counted and left
  as-is - they still resolve through the read seam), idempotent
  (`8R-` rows skipped), dry-run mode, works on SQLite and Postgres.
  Executed at flip time.
- **Honest labels**: "8r" is a first-class provenance key (teal, "8R
  Backbone", registry + Data Source Key legend + comp badge). Fixed the
  comp-page heuristic that would have labeled every self-sourced
  property "User input" because it lacks an ALN id.
5 more tests (14 total in tests/test_cutover.py).

## V5.10.0.0.0 — 2026-07-30  ·  P0-3 cutover foundations (spec 7.3)
Three structural blockers to cutover removed:
- **Rent signal v1** (`core/rent_signal.py`): every multifamily backbone
  row gets a HUD-FMR bedroom-blended monthly rent estimate
  (`est_avg_rent`, `rent_source='hud_fmr'`), stamped at the end of every
  backbone build. The P0-2 rent-delta gate now MEASURES something - it
  previously passed vacuously because the backbone had no rent data at
  all. FMR is a 40th-percentile standard, so the first honest delta will
  be large; listings-scraped rents (pullers/listings, already built in
  the ETL repo) are the layer that closes it. Deliberately NOT tuned to
  flatter the gate - deriving a market factor from ALN would defeat the
  ALN-free requirement.
- **Persisted crosswalk** (`property_crosswalk` table): the legacy->8R id
  mapping was built in memory every parity run and thrown away; it now
  materializes with match method + parcel count on every run. This is
  the migration path for deal references at flip time.
- **Cutover read seam** (`config.SPINE_READ_SOURCE`, default "legacy"):
  `data/db.py list_properties/get_property` - the funnel every UI and
  engine read goes through - can serve `properties_8r` adapted to the
  legacy row shape, with legacy ids resolving through the crosswalk.
  Fields the backbone can't source yet are explicit Nones, never
  fabricated; filters it can't answer (management, asset class) return
  empty rather than wrong. Flips ONLY after the P0-2 gates hold.
9 new tests (tests/test_cutover.py). Remaining before flip: comp-page
source-label heuristic treats missing aln_id as "User input"; listings
puller keys rows to ALN ids; provenance key for hud_fmr/listings;
deals.property_id migration via crosswalk (Postgres).

## V5.9.3.0.0 — 2026-07-30  ·  First clean autopilot cycle → tuning round 11
The first fully hands-free cycle landed (discover/pull/phase0 all exit 0,
every report published). Comp overlap 66.8% vs the 90% gate; covered-city
match 86.8%. Fixes from the report:
- **Coordinate backfill by address**: Norfolk's Socrata assessor has no
  geometry, leaving 856 multifamily entities coordinate-blind (all its
  comp subjects skipped). Multifamily rows missing coords now borrow
  verified coordinates from sibling records at the same normalized
  address (building permits, other layers) - same city only, sanitized,
  never invented. New report line `coords backfilled by address`.
- **Portsmouth aliasing**: its zoning strings (UR-M, T4...) were masking
  the real building type; `TYPE_BLDG` now maps to use_code ahead of
  zoning, with `IMPROVEMEN`/`CLAS` as low-priority fallbacks.
- **Norfolk aliases**: dwelling_year_built → year_built;
  finished_living_area / gross_floor_area → sqft; Better-Buildings
  energy-metric noise keys ignored in the unmapped report.
- 2 new backfill regression tests (never invents coords for unknown
  addresses).

## V5.9.2.1.0 — 2026-07-30  ·  Wake the host: the 3 AM run was silently skipped
The first nightly autopilot never executed - the host was asleep at 3 AM
and the schtasks task had neither wake nor missed-run catch-up, so Windows
skipped it without a trace. The task is now registered via PowerShell
`Register-ScheduledTask` with `-WakeToRun` (wakes the machine for the run)
and `-StartWhenAvailable` (a missed run fires as soon as the machine is
back), plus `-MultipleInstances IgnoreNew` and a 3-hour execution limit.
Plain schtasks remains as a fallback if PowerShell registration fails.
Hourly-until-clean and nightly-at-3 both carry the wake/catch-up settings.
Regression test added (test_schedule_command_wakes_and_catches_up).

## V5.9.2.0.0 — 2026-07-29  ·  Run continuously until clean (owner directive)
A "clean run" = every step exits 0 AND the reports publish to GitHub. Until
one lands, the autopilot re-registers its own scheduled task to run HOURLY;
the moment a cycle is verified clean it throttles itself back to nightly
3:00 AM. The cadence is self-managed via schtasks from inside the cycle -
no human touches the schedule in either direction. Claude's analysis
routine runs hourly in lockstep until the clean run, then returns to
nightly, and the next phase (P0-3 cutover) begins on the first clean data.

## V5.9.1.0.0 — 2026-07-29  ·  Near-live autopilot: every step publishes as it completes
Owner wanted to see the log without waiting out the cycle. Each step
(discover / pull / phase0) now publishes its report + the running log the
moment it finishes, so GitHub shows progress within minutes instead of at
cycle end. The final sweep publish remains as the catch-all.

## V5.9.0.2.0 — 2026-07-29  ·  Stranded-commit fix: reports can never be orphaned
The first autopilot cycle committed its reports but the push waited on the
one-time GitHub authorize; the next run would have said "nothing new" and
orphaned that commit forever. `publish()` now ALWAYS syncs and pushes
(pushing an up-to-date branch is a no-op), so any stranded reports ride out
on the next cycle automatically. Regression test added (5 publish
integration tests total). Lesson recorded: early-return paths in delivery
code must prove the remote is caught up, not just that nothing changed
locally.

## V5.9.0.1.0 — 2026-07-29  ·  The updater installs the Autopilot itself
One human action left, total: run `update-workbench.bat` once. It now
registers the nightly Autopilot task automatically (if missing) and kicks
off the first cycle in a background window - no separate installer to
find. `install-autopilot.bat` remains for manual reinstall.

## V5.9.0.0.0 — 2026-07-29  ·  AUTOPILOT: the whole data loop, hands-free
Owner directive: "You run this." Done — the operator is out of the loop.
- **`install-autopilot.bat`** (double-click ONCE): registers a nightly
  3:00 AM Windows scheduled task and runs the first cycle immediately.
- **Each cycle**: self-update to origin/main -> uv sync -> discover feeds
  -> pull Hampton Roads -> rebuild the backbone -> publish every report
  (discover/pull/phase0 + the autopilot log + feeds_extra.json) to GitHub.
  Claude reads the reports and pushes fixes; the next night's cycle picks
  them up automatically. Tuning now needs zero human round-trips.
- **WAL mode on workbench.db**: readers never block the pull - the
  app/service stays up during unattended cycles ("close the app first" is
  gone from pull-muni too).
- Stage 1 (`scripts/autopilot.py`) is a deliberately boring, stable
  updater that also repairs every wedged-git state seen on the host;
  stage 2 (`scripts/autopilot_run.py`) is the evolving pipeline and runs
  on the freshly updated code.
- Publish path proven by 4 integration tests against a real local git
  remote: normal push, detached-HEAD + stale-rebase recovery, idempotent
  re-run, and racing a concurrent code push.

## V5.8.13.1.0 — 2026-07-29  ·  Publish helper made corruption-proof
The brian2 account's publish attempt showed the remaining failure modes in
one screen: no git identity under that Windows account (the inline identity
flags sat on a caret-continued line the LF corruption split), a leftover
cherry-pick wedging the repo, and the missing phase0 report (deleted when
the branch repair abandoned the broken detached commits).
`_push-report.bat` rewritten defensively: repo-local committer identity via
plain `git config` lines, NO caret continuations anywhere, aborts for
rebase AND cherry-pick AND merge debris, and a friendly skip when the
report file does not exist. `update-workbench.bat` gets the same extra
aborts. All CRLF.

## V5.8.13.0.0 — 2026-07-29  ·  Norfolk federation fix + CRLF-safe batch files
The Norfolk-enabled pull surfaced two final plumbing defects:
- **Socrata catalogs federate**: Norfolk's search returned New York City's
  assessment roll ("Tax Classes 1,2,3,4"), whose dataset id then 404'd on
  data.norfolk.gov. Discovery now restricts the catalog search to the
  portal's own domain AND verifies each result's home domain. Requires one
  discover-feeds re-run to replace the two bogus Norfolk specs.
- **Batch files had LF-only line endings**, which Windows cmd misparses -
  the publish step crashed trying to execute the word "new" (split out of
  "Nothing new to publish"). All .bat/.ps1 files rewritten CRLF and pinned
  via .gitattributes (eol=crlf) so every future checkout stays safe.
The stale-feed sweep (239,957 wrong-city/retired rows removed) and the
one-time GitHub sign-in both verified working in the same run.

## V5.8.12.1.0 — 2026-07-29  ·  One-time GitHub sign-in for report publishing
The recovered publish step got as far as the push - which raised GitHub's
sign-in window (the host had never authenticated for pushing; that popup
IS git asking for permission to publish). Made it a one-time event:
- remote re-pointed to the repo's current name (WORKBENCH_v5 - stored
  credentials key on the URL, and the old granite URL forced a redirect),
- Git Credential Manager pinned as the credential store so the browser
  sign-in persists in Windows Credential Manager.
Owner signs in once via "Sign in with your browser"; every future publish
is silent.

## V5.8.12.0.0 — 2026-07-29  ·  Round 10: shared-universe comps + host git recovery + stale-feed sweep
The first auto-pushed report attempt revealed everything at once:
- **Host push finally diagnosed**: the repo sat on a detached HEAD with a
  stale half-finished rebase (debris from the earlier silent failures).
  `_push-report.bat` and `update-workbench.bat` now abort stale rebases,
  clear `.git/rebase-merge`, and re-attach HEAD to main before any sync.
- **Comp overlap was structurally capped**: the 8R backbone knows ~3x more
  real complexes than the 639-row legacy set, so nearest-12 ranking against
  the full pool punished the backbone for BETTER coverage (overlap froze at
  14% even after the pool cleanup). The replay now ranks both sides over
  the SHARED universe (crosswalked entities only) — measuring placement
  agreement, not coverage difference. Subjects in cities with no 8R
  coordinates yet (Norfolk) are counted separately instead of dragging the
  average to zero. Subject cap raised 50 -> 200, spread by city.
- **Stale-feed sweep in the ETL**: rows from feeds no longer in the
  registry are deleted per HR market on every pull. This purges retired
  layers that lingered forever — including 2,908 Chesapeake blast-zone
  parcels filed under HAMPTON from before the wrong-city guard existed,
  and the wrong-city VB Streets_Parcels layer (90,468 rows).
- **Zero-MF cities explain themselves**: a city with parcels but no
  multifamily prints its top use codes (Portsmouth's next report will name
  its apartment coding).
- New aliases (VB STR_TYPE/SUFFIX_TYPE, Portsmouth RESCLSCODE) + ~30 junk
  keys ignored. 3 new tests; suite green.

## V5.8.11.0.0 — 2026-07-29  ·  Phase 0 round 9: evidence-aware comp pool
Driven by the round-8 report (85.2% covered match, Chesapeake 66/66,
comp overlap stuck at 14.3%):
- **Evidence-aware comp pool**: in a city whose feed proves it carries unit
  counts (>= 50 entities at 10+ units), a "Multi Family"-style label with
  NO units is presumed small and stays out of the pool — VB's 15,482
  label-only duplex rows were the entire 14% overlap ceiling. Cities with
  no unit data at all (Norfolk) keep counting labels. The report prints
  exactly what was excluded per city.
- **Unit-mismatch lines now show their composition** ("legacy 280 vs 8R
  1310 [655+655]") so the Allure-at-Edinburgh overcount self-explains on
  the next run instead of needing another guess.
- **New-feed aliases**: Portsmouth TYPE_PROP/SITE_ADDRE/BLDG_TYPE (its
  regenerated feeds had unmapped schemas - matches fell 31->14), Hampton
  TOTVALUE, VB FULL_ADDR/STR_NUM/STR_NAME/PROP_ADDRESS, plus ~35 junk keys
  ignored.
- 3 new tests; suite unchanged otherwise.

## V5.8.10.1.0 — 2026-07-29  ·  Report push shows its errors
No host report ever arrived on GitHub, and the push helper was swallowing
every git message (`>nul 2>&1`) - so the failure had no face.
`_push-report.bat` now prints git's real output; when a push fails, the
window shows the exact error above a "screenshot this" pointer. One
screenshot = one fix.

## V5.8.10.0.0 — 2026-07-29  ·  Socrata discovery: Norfolk's coordinates found a path
The discover-feeds run confirmed the guards work (bad VB layer rejected by
name, junk layers by geography) and exposed the last structural gap:
**Norfolk's GIS is not ArcGIS**, so the ArcGIS walk finds nothing and the
Socrata assessment roll has no coordinates. Fixes:
- **Discovery now probes Socrata catalogs** (`data.norfolk.gov`): searches
  parcel/real-estate/property/address datasets, scores columns with the
  same alias vocabulary, +5 for a coordinate column, geo-verifies 5 sample
  rows against the city bbox, and emits `platform: socrata` feed specs.
- **Structured coordinate values handled safely**: Socrata location dicts
  (`{latitude, longitude}`) and GeoJSON points now map to lat/lng at
  weakest priority — and a dict can never be str()'d into the address
  field. SODA fetches never send the ArcGIS `f=json` param (SODA 400s on
  unknown non-$ params).
- 7 new tests. Next discover-feeds run should finally give Norfolk's
  backbone coordinates, unlocking lat/lng + proximity matching for its
  remaining 68 unmatched legacy rows.

## V5.8.9.1.0 — 2026-07-29  ·  Terminology: "backbone" replaces "spine" in all visible text
Owner directive: the word is **backbone**. Updated everywhere a human sees
it — run-phase0/pull-muni .bat text, the P0-1/P0-2 report output, and UI
copy (comps refresh notes, ETL notice). Internal code identifiers
(`core/spine.py`, `build_spine`) are unchanged to avoid churn; the rule is
recorded in CLAUDE.md so future features use "backbone" from the start.

## V5.8.9.0.0 — 2026-07-29  ·  Phase 0 round 7: comp-pool cleanup + 12 review-confirmed fixes
Driven by the first closed-loop full report (70.7% match, Chesapeake 66/66,
Portsmouth 31/45) plus an adversarial 16-agent review of the diff. Fixes:
- **Comp pool cleanup (the 13.8% overlap blocker)**: a KNOWN unit count now
  decides multifamily for BOTH the P0-1 gate and the P0-2 comp pool
  (shared `is_mf_ten_plus`); VB's 15.7K "Multi Family"-labeled duplexes no
  longer pollute either.
- **Address-point unit derivation is allowlist-guarded**: points only count
  as units when the parcel's code is affirmatively multifamily or absent.
  Kills BOAT SLIP marinas, Shopping Center suites, and every un-enumerable
  single-family spelling ('1 FAM RES', 'R-1', class '101') in one rule.
  Subsidized/public "housing" codes stay in. Derived counts may RAISE a
  frozen units=1 from building-card feeds; larger explicit counts never drop.
- **r8_form fixed twice**: build_row passed year_built into the STORIES
  parameter (near-everything classified "high-rise" - pre-existing, found
  by review), and the upsert could clobber it with a bare row's default.
  r8_form now recomputes from the merged (use_code, units) after all
  feeds + derivation settle.
- **Merge hardening**: multi-feed COALESCE keeps first non-NULL per field
  with a deterministic ORDER BY source_url scan (re-pull history no longer
  changes which feed wins).
- **Parity aggregation dedupe**: identical large counts collapse only when
  centroids agree (~30m) - Allure-at-Edinburgh 5x overcount dies, but four
  real 24-unit phase buildings at one situs still sum to 96.
- **Norfolk joins discovery** (its Socrata roll has zero coordinates -
  parity now flags this per city).
- 12 new tests from confirmed review findings. Suite: 694 passed.

## V5.8.8.0.0 — 2026-07-29  ·  Closed-loop reports: host runs push full results to GitHub
Ends the screenshot workflow. `run-phase0.bat`, `pull-muni.bat`, and
`discover-feeds.bat` now:
- tee their complete console output to `reports\phase0-latest.txt` /
  `pull-latest.txt` / `discover-latest.txt` (UTF-8, live output preserved),
- auto-commit and push the report via the shared `_push-report.bat`
  (safe.directory guard, autostash rebase, one retry, inline identity so a
  host without git user.name still commits),
- discover-feeds also pushes `data\feeds_extra.json`, so the discovered
  feed list itself is visible for review before a pull.
Claude reads the full results from the repo minutes after a double-click —
including everything that scrolls off screen. Push failure never blocks a
run; the .bat says where the file is and screenshots remain the fallback.
(Context: the build environment is firewalled from both the pilot host and
the city portals — code flows out via GitHub; this makes results flow back
the same way.)

## V5.8.7.1.0 — 2026-07-29  ·  Pull resilience: 502 retries + wrong-city layer guard
The clean HR-only pull surfaced two remaining hazards:
- **Transient 502s no longer kill a feed**: a VB layer died at offset
  48,000 on one `502 Bad Gateway`. The ArcGIS puller now retries 5xx /
  timeouts / connection drops up to 3 times with backoff (4xx still fail
  fast — the request itself is wrong).
- **Layers named for another city are disqualified** — in discovery AND at
  pull time. VB's own AGOL org serves `Chesapeake_Norfolk_Streets_Parcels`;
  the bbox sample can miss it because neighboring cities overlap along the
  border, and ingesting it under Virginia Beach would mint wrong-FIPS 8R
  ids. The ETL also skips such feeds from a stale `feeds_extra.json` (with
  a visible `[skipped]` line), so no re-discovery is strictly required.
  ("Hampton Roads" is recognized as the region, not the city of Hampton.)
- Verified: 5 new tests (retry on 502/503, fail-fast on 400, name guard
  incl. Hampton-Roads exception, stale-file skip). Suite: 690 passed
  equivalents (685 passed + the 4 known data-dependent smokes + 1 skip).

## V5.8.7.0.0 — 2026-07-29  ·  Phase 0 round 6: the VB 116K-parcel bug + real coordinates
The first full-data run exposed two structural bugs; both fixed:
- **116,780 "multifamily" parcels in Virginia Beach were single-family.**
  The use-code matcher substring-matched short codes, and VB zoning `R-40`
  (single-family) contains `r-4`. That pollution buried the P0-2 comp pool
  and produced the 20.7% comp overlap. Short codes (`mf`, `405`, `r-4`,
  `apt`) now match whole tokens only; long words (`apartment`,
  `multifamily`) still match anywhere. Duplex/triplex/quadplex no longer
  count as multifamily — the product bar is >= 10 units (spec 7.3).
- **The ETL never stored coordinates for ArcGIS layers**
  (`returnGeometry: false`), which is why Portsmouth matched 0/45 despite
  109K records — no lat/lng, no proximity matching. The puller now probes
  centroid → full-geometry → legacy modes per layer, stamps `geo_lat`/
  `geo_lng` onto every record, and the spine converts stray Web Mercator
  meters to degrees while dropping state-plane feet (a missing coordinate
  matches by address; a wrong one matches the wrong parcel).
- **Chesapeake addresses now assemble**: its layer splits the situs into
  `ST_NUM`/`ST_NAME`/`ST_TYPE` — the last two had no alias, so no
  Chesapeake address ever matched. Plus new aliases from the unmapped-key
  report (`RESYRBLT`, `TOT_SQ_FT`, `BLDG_USE`, `MASTER_GPIN`,
  `RECORDED_GPIN`, `ST_CITY`, `ST_ZIPCODE`) and ~30 junk keys ignored.
- **Self-diagnosing reports**: run-phase0 now prints the top use codes that
  drove each city's multifamily classification (a wrong alias shows up in
  one glance), and the parity by-city table gains a "w/ coords" column.
- Verified: 11 new tests (token matching, Mercator/state-plane guards,
  ArcGIS geometry-mode probing incl. ring-centroid averaging and old-server
  fallback, Chesapeake address assembly). Suite: 685 passed.

## V5.8.6.1.0 — 2026-07-28  ·  Pull hygiene (HR-only, visible extras, lock tolerance)
The pull-output screenshot explained the missing city data three ways, all
fixed:
- **`pull-muni.bat` now pulls Hampton Roads only** (`--hr`): the previous run
  spent most of its time on Raleigh/Atlanta/Nashville — 2M+ records Phase 0
  doesn't use.
- **Discovered feeds are announced**: the ETL prints how many feeds it loaded
  from `data/feeds_extra.json` and each one's city + URL, so "did my
  discovered feeds actually run?" is answered on the first line, not by
  archaeology.
- **`database is locked` (Alexandria [ERR])**: the ETL now opens SQLite with
  a 60 s busy timeout, and `pull-muni.bat` reminds the operator to close the
  app/service first.
- **`run-phase0.bat` lists the assessor feeds actually present per HR city**
  (with record counts) before building — ground truth for every future run.
- Verified offline: extra-feed loading with unknown-key tolerance, HR
  filtering, and the feed listing rendering. Suite: 674 passed; 4
  pre-existing data-dependent failures unchanged.

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
