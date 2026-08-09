"""pg.database_url() must find DATABASE_URL from .env for headless callers
(2026-08-09 regression: autopilot steps reported 'Postgres not reachable'
because bare scripts never loaded .env). Central fix in data/pg."""

from __future__ import annotations

import importlib

from data import pg


def test_env_var_wins_without_touching_dotenv(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    assert pg.database_url() == "postgresql://u:p@h/db"


def test_headless_reads_dotenv_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    envfile = tmp_path / ".env"
    envfile.write_text("DATABASE_URL=postgresql://from:dotenv@h/db\n")
    # point the loader at our temp .env and reset the once-guard
    import data.pg as pgmod
    monkeypatch.setattr(pgmod, "_ENV_LOADED", False)
    monkeypatch.setattr(
        "pathlib.Path.resolve",
        lambda self: self, raising=False) if False else None
    # simplest: monkeypatch _ensure_env_loaded to load our file
    from dotenv import load_dotenv
    monkeypatch.setattr(pgmod, "_ensure_env_loaded",
                        lambda: load_dotenv(envfile))
    assert pg.database_url() == "postgresql://from:dotenv@h/db"
