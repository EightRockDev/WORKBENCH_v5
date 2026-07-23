# Eight Rock Workbench v5.0

Full-loop multifamily acquisition platform for Eight Rock Capital Partners —
the release that converts the working internal underwriting engine (v2.4.1:
Python / Streamlit, underwriting core, GRANITE loan radar, municipal
sale-history, one-click IC-ready export) into a compliant, multi-user,
multi-tenant, publicly-deployable system.

> **New repository.** This is the v5.0 build, seeded from v2.4.1 and developed
> **separately from GRANITE**. Spec: *Eight Rock Workbench v5.0 — Competitive
> Analysis & Technical Specification* (rev. 5, July 2026).

## v5.0 pilot deployment (start here)

Standing the Workbench up as a multi-user pilot reachable over the public
internet — the "what to install / make it publicly accessible" path — is
documented as an executable runbook in **[`docs/SETUP.md`](docs/SETUP.md)** and
automated by **[`deploy/install.sh`](deploy/install.sh)**.

| Layer | Artifact |
|---|---|
| System install (PostgreSQL 16, Caddy/TLS, UFW, systemd, uv) | `deploy/install.sh` |
| Public HTTPS reverse proxy | `deploy/Caddyfile` |
| Process manager (auto-restart) | `deploy/workbench.service` |
| Nightly backups (OneDrive-safe) | `deploy/backup.sh` |
| Tenancy / auth / concurrency / POC schema | `db/pilot_schema.sql` |
| Postgres connection + per-tenant RLS context | `data/pg.py` |
| Secrets template (OIDC + DB) | `.streamlit/secrets.toml.example` |

The pilot schema implements the new v5.0 data contracts: optimistic
concurrency + soft locks (§9.3), the users/admin model (§9.4), organizations /
memberships / the 18-preset role library (§10), row-level-security tenant
isolation (§10.1), the `poc_record` skip-trace contract (§4.5), and the
append-only audit log (§8.1).

---

## The underwriting engine (from v2.4.1)

Local multifamily underwriting workbench. Streamlit port of `Workbench.html`, with a proper 3-tier waterfall and LP IRR.

## Quick start

Double-click `run.bat`, or from a terminal:

```
uv run python -m streamlit run app.py
```

The app opens at <http://localhost:8501>.

> **Why `python -m streamlit` and not bare `streamlit`?** Some Windows
> machines (Smart App Control on Win 11, corporate WDAC/AppLocker policies)
> block the small `streamlit.exe` shim that `uv` drops in `.venv\Scripts\`
> with `os error 4551 — Application Control policy has blocked this file`.
> Invoking via `python -m streamlit` routes the call through `python.exe`
> (on the policy allow-list) and bypasses the block. The `python -m` form
> works identically on machines where the policy isn't active, so it's
> safe as the default.

## Project layout

```
python_workbench/
├── app.py                # Streamlit entrypoint (5 tabs)
├── config.py             # Eight Rock conventions — single source of truth
├── data/
│   ├── aln_loader.py     # ALN xlsx → SQLite
│   ├── property_io.py    # read deal.json / sources.json / sales.json
│   └── schema.sql
├── core/
│   ├── calc.py           # cap, DSCR, CoC, debt schedule, 5-yr CF
│   ├── waterfall.py      # 8% pref + ROC + 70/30 promote
│   ├── irr.py            # project IRR + LP IRR
│   ├── comps.py          # haversine + Bucket 1 / Bucket 2
│   ├── sensitivity.py    # vacancy × rent growth × expense growth grid
│   └── verdict.py        # GO / WATCH / NO-GO
├── ui/                   # Streamlit page modules
└── tests/                # pytest
```

## Conventions

All Eight Rock underwriting constants live in `config.py`. See module docstring for the full list. Highlights:

- **GO bars:** Cap ≥ 7.5%, DSCR ≥ 1.30x, CoC ≥ 6.0%
- **AM fee:** 4% of GPR, $0 in exit year
- **Waterfall:** 8% pref (cumulative, non-compounded) → ROC → 70 LP / 30 GP
- **LP IRR target:** ≥ 15% over 5-year hold
- **Amortization:** 25 years (locked); IO 0–10 years (slider)
- **Comps:** Bucket 1 (≤3mi, same class, ≤8) + Bucket 2 (≤5mi, ≤4)

## Virtual environment location

The venv lives **outside OneDrive** at `%USERPROFILE%\.venvs\eight-rock-workbench` to avoid sync churn on thousands of small files. `run.bat` sets `UV_PROJECT_ENVIRONMENT` accordingly. If you ever rebuild the venv, set that env var first.

## Status

Scaffolding stage — module signatures only, no business logic. See `tests/` for the contract each module is being built against.
