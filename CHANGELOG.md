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
