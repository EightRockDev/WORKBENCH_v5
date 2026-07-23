# Eight Rock Workbench v5.0 — Pilot Deployment Runbook

This is the executable version of **Section 9** of the v5.0 spec: moving the
Workbench off `localhost` onto a dedicated server with a reserved IP so external
pilot users can log in from anywhere over HTTPS.

> **This repository is the new v5.0 build**, seeded from the working v2.4.1
> underwriting engine. It is a **separate repo from GRANITE** by design.

---

## What this machine needs installed (Section 9.1)

| Component | Choice | Why |
|---|---|---|
| OS | Ubuntu Server 24.04 LTS | Host OS (Windows Server 2022 also supported via NSSM + Caddy) |
| Python + pkg mgr | Python 3.12 + `uv` | Runs the existing app; `uv.lock` present |
| Database | PostgreSQL 16 | True concurrent writes for multi-user (Sections 9.2–9.3) |
| Reverse proxy + TLS | Caddy | Public HTTPS with automatic Let's Encrypt certs |
| Process manager | systemd | Keeps the app running, auto-restart on crash/reboot |
| Firewall | UFW | Public 80/443 only; 8501 stays bound to localhost |
| Domain | e.g. `workbench.eight-rock.com` | A-record → reserved IP (Let's Encrypt won't cert a bare IP) |
| Auth | Streamlit native OIDC + Auth0 **or** Entra External ID | Google / Microsoft / email-password sign-in (Section 9.4) |

## One-command install (fresh Ubuntu 24.04 box)

```bash
git clone <this-repo-url> /opt/8rw
cd /opt/8rw
sudo DOMAIN=workbench.eight-rock.com ADMIN_SSH_CIDR=<your-office-ip>/32 \
     APP_DIR=/opt/8rw ./deploy/install.sh
```

`install.sh` is idempotent and installs Python/uv, PostgreSQL 16, Caddy, UFW;
creates the database; applies `db/pilot_schema.sql`; installs the systemd
service and Caddy config; and enables the firewall. It prints the manual steps
that only you can do (DNS, router port-forward, OIDC credentials).

## Manual setup sequence (Section 9.1, expanded)

1. **Provision the box** and reserve a static internal IP on your router **plus**
   the public reserved IP, so port-forwarding stays stable.
2. **Run `deploy/install.sh`** (above).
3. **App code on local disk** at `/opt/8rw` — **never** OneDrive (Section 9.2,
   the single most likely way to lose data). Distribute updates via `git pull`.
4. **Point DNS**: A-record `workbench.eight-rock.com` → reserved public IP.
5. **Port-forward** public `80` + `443` to the server; keep `8501` localhost-only.
6. **Auth**: copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`,
   fill the `[auth]` OIDC block (Auth0 recommended; Entra alternative).
7. **Test an external login** over HTTPS. First authenticated user is
   auto-promoted to admin (FR-9.4.3); everyone after lands on the
   pending-approval screen until approved.
8. **Backups**: schedule `deploy/backup.sh` nightly (cron). A closed `pg_dump`
   file is safe to sync to OneDrive; the live DB is not.

## The OneDrive rule (Section 9.2 — read this first)

- **Live database** → server local disk only (Postgres data dir). Never synced.
- **App code** → local disk (`/opt/8rw`). Updates via `git pull`, not sync.
- **OneDrive is fine for**: read-only document inputs (copy in, don't run from
  it), nightly DB **dumps** (written once, closed, then synced), and delivered
  artifacts.

## Concurrency (Section 9.3)

`db/pilot_schema.sql` ships the primitives:
- **`row_version` + `bump_row_version()` trigger** — optimistic concurrency
  (FR-9.3.1). Saves run `UPDATE … WHERE id=? AND row_version=?`; a stale write
  updates zero rows and the app raises the conflict dialog (FR-9.3.2).
- **`edit_locks`** — short-lived, heartbeat-refreshed advisory soft locks with a
  ~5-min TTL for the "🔒 Jane is editing" presence banner (FR-9.3.3). Read-only
  viewing is never blocked.

## Multi-tenancy (Section 10)

- **Shared reference layer** (property spine, comps, GRANITE, municipal history)
  is global and carries no `org_id`.
- **Org-private tables** (`poc_records`, `edit_locks`, `skiptrace_spend`,
  `memberships`) enforce **row-level security** keyed to `org_id`. The app sets
  `SET app.current_org_id = '<uuid>'` per request (see `data/pg.py`
  `org_connection`). Cross-org read is impossible at the DB layer (AC-10.1).
- **Role-preset library** (18 presets, Section 10.3) is seeded into
  `role_presets`. Admins pick a key; permissions/masks/scope come pre-wired.

## Verify

```bash
# Postgres reachable + schema present
psql "$DATABASE_URL" -c "\dt"
psql "$DATABASE_URL" -c "select count(*) from role_presets;"   # -> 18

# Services
systemctl status workbench caddy
journalctl -u workbench -f
```
