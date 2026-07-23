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
registers the **Workbench** service (Streamlit on 127.0.0.1:8501) and the
**Caddy** service; and opens the firewall for 80/443.

## Service management (NSSM)

```powershell
nssm status  Workbench      # or: Caddy
nssm restart Workbench
nssm stop    Workbench
# logs: C:\WORKBENCH_V5\logs\workbench.out.log / .err.log
```

## Manual steps only you can do

1. **DNS** — point the A-record for your domain at the reserved public IP
   (Let's Encrypt will not certify a bare IP).
2. **Router** — port-forward public 80 + 443 to this box; keep 8501 local.
3. **Auth** — copy `.streamlit\secrets.toml.example` → `.streamlit\secrets.toml`
   and fill the `[auth]` OIDC block (Auth0 recommended, Entra alternative — §9.4).
4. **Backups** — schedule `deploy\windows\backup.ps1` via Task Scheduler (§9.1).

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
