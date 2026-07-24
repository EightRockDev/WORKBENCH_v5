"""Pytest bootstrap — load .env so DATABASE_URL is available to the pilot tests.

The app loads .env at startup via app.py; pytest does not run app.py, so we load
it here. This lets `uv run pytest` find the Postgres connection written by
deploy/windows/setup-db.ps1 (or deploy/install.sh) without exporting env vars by
hand. Harmless when no .env exists.
"""

from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv always present in this project, but be safe
    pass
