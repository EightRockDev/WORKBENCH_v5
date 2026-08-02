# Windows deployment — Eight Rock Workbench v5.0

The Windows path for **Section 9** of the spec. The app dir is **`C:\WORKBENCH_V5`**
(the Windows analog of `/opt/8rw`). Process management is **NSSM**; public HTTPS
is **Caddy**; everything else (Postgres 16, uv, the pilot schema) is identical to
the Linux path.

> **OneDrive rule (Section 9.2):** keep `C:\WORKBENCH_V5` and the PostgreSQL data
> directory on **local disk, never inside a OneDrive-synced folder.** Only the
> nightly closed `pg_dump` file is safe to sync.

## Prerequisites

- Windows Server 2022 (or Windows 10/11) with a reserved public IP.
- Administrator PowerShell.
- `winget` available (install "App Installer" from the Microsoft Store if not).
  The installer falls back to manual notes if winget is missing.

## Install

```powershell
# 1. Put the code at C:\WORKBENCH_V5 (see repo root README / SETUP for options)
cd C:\WORKBENCH_V5

# 2. Run the installer from an ELEVATED PowerShell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install.ps1 -Domain workbench.eight-rock.com
```

`install.ps1` installs Python 3.12, uv, PostgreSQL 16, Caddy, and NSSM; creates
the `workbench` database; applies `db\pilot_schema.sql`; runs `uv sync`;
registers the blue-green pair **WorkbenchBlue** (127.0.0.1:8501) and
**WorkbenchGreen** (127.0.0.1:8502) plus the **Caddy** service; and opens the
firewall for 80/443. It asks for the app passcode once.

Both colours are required: Caddy load-balances them and `deploy-swap.ps1`
restarts one at a time, which is what makes a deploy invisible to users. With
only one installed, a restart is a real outage.

### LAN mode (no Caddy, no domain)

To reach the app directly on the office network instead, skip `install.ps1`
and run the service installer twice — it binds `0.0.0.0` and opens the
firewall for the private profile only:

```powershell
.\deploy\windows\install-lan-service.ps1                                  # blue, 8501
.\deploy\windows\install-lan-service.ps1 -Name WorkbenchGreen -Port 8502  # green
```

## Service management (NSSM)

```powershell
nssm status  WorkbenchBlue      # or: WorkbenchGreen, Caddy
nssm restart WorkbenchBlue
nssm stop    WorkbenchGreen
# logs: C:\WORKBENCH_V5\logs\service-WorkbenchBlue-out.log / -err.log

# Deploy new code with no downtime (restarts each colour in turn,
# waiting for the health check before touching the other):
.\deploy\windows\deploy-swap.ps1
```

## Manual steps only you can do

Concrete values below are the Eight Rock production setup as configured
2026-08-02 (Cloudflare DNS, Cox Business, Technicolor CGA4131TCH gateway).

1. **DNS (done 2026-08-02)** — Cloudflare → eight-rock.com → A record
   `workbench` → the public IP, **Proxy status: DNS only (grey cloud)**.
   An orange-cloud (Proxied) record makes Cloudflare answer Caddy's
   Let's Encrypt HTTP-01 challenge, so no certificate is ever issued and
   there is no error — `install-caddy.ps1` detects and names this.
2. **Router port-forward (Cox CGA4131TCH, `http://192.168.0.1`)** —
   - Log in as **`admin`** (the "Administration / User" header means you are
     in the read-mostly user account; forwarding is admin-only). Try
     `admin`/`password`, then the password on the router label; Cox Business
     support (866-272-5777) can reset it remotely.
   - **Reserve the IP first**: menu → Connection → Local Network → DHCP
     Reservations → reserve this machine's address. An unreserved lease can
     move on renewal and silently break the forward.
   - Menu → Advanced → Port Forwarding → Enable → **+ADD SERVICE** twice:
     TCP 80→80 and TCP 443→443, both to this machine's LAN IP.
   - `install-caddy.ps1` prints the LAN IP to forward to and compares the
     public IP against DNS (MISMATCH line) — rerun it to verify this step.
3. **Auth** — copy `.streamlit\secrets.toml.example` → `.streamlit\secrets.toml`
   and fill the `[auth]` OIDC block (Auth0 recommended, Entra alternative — §9.4).
4. **Backups** — schedule `deploy\windows\backup.ps1` via Task Scheduler (§9.1).
5. **API keys** — enter keys only in the app's Artifact Engine panel (writes
   the gitignored `.env`). A key that has ever been pasted into chat, email,
   or a screenshot is burned: rotate it at the provider console first.

## Verify

```powershell
$env:PGPASSWORD = "<workbench password from .env>"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U workbench -h 127.0.0.1 -d workbench -c "\dt"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U workbench -h 127.0.0.1 -d workbench -c "select count(*) from role_presets;"  # -> 18
```

## If winget isn't available (manual component install)

| Component | Source |
|---|---|
| Python 3.12 | <https://www.python.org/downloads/> |
| uv | `irm https://astral.sh/uv/install.ps1 \| iex` |
| PostgreSQL 16 | EDB installer — <https://www.postgresql.org/download/windows/> |
| Caddy | <https://caddyserver.com/download> (place `caddy.exe` on PATH) |
| NSSM | <https://nssm.cc/download> (place `nssm.exe` on PATH) |

Then re-run `install.ps1` — it skips anything already present.
