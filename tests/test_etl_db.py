"""ETL database location resolution.

The bug this locks down: the path was hard-coded three levels above the module,
which was right in the v2.4.1 layout (`<root>/python_workbench/core/`) but a
level too high in v5, where `core/` sits directly under the app root. On the
pilot host it resolved to `C:\\hampton-roads-etl\\`, so the database could never
be found and every market panel reported "ETL database not loaded".
"""

from __future__ import annotations

import sqlite3

import pytest

from core import etl_db, market_data


def _make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE hud_fmr (zip TEXT, rent INTEGER)")
        db.execute("INSERT INTO hud_fmr VALUES ('23504', 1400)")
    return path


def test_preferred_location_is_inside_the_app_not_above_it():
    """The old expression pointed above the app root (C:\\ on Windows)."""
    preferred = etl_db.preferred_location()
    assert etl_db.APP_ROOT in preferred.parents
    assert preferred.name == "hampton_roads.db"
    # `core/` is directly under the app root in v5.
    assert (etl_db.APP_ROOT / "core" / "etl_db.py").is_file()


def test_missing_database_resolves_to_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ER_ETL_DB", str(tmp_path / "nope.db"))
    monkeypatch.setattr(etl_db, "APP_ROOT", tmp_path)
    assert etl_db.resolve_etl_db() is None
    assert market_data.is_etl_available() is False


def test_env_override_is_honored(monkeypatch, tmp_path):
    db = _make_db(tmp_path / "elsewhere" / "hampton_roads.db")
    monkeypatch.setenv("ER_ETL_DB", str(db))
    monkeypatch.setattr(etl_db, "APP_ROOT", tmp_path)
    assert etl_db.resolve_etl_db() == db
    assert market_data.is_etl_available() is True


@pytest.mark.parametrize("subdir", ["data", "hampton-roads-etl"])
def test_found_in_each_supported_app_relative_location(monkeypatch, tmp_path, subdir):
    monkeypatch.delenv("ER_ETL_DB", raising=False)
    monkeypatch.setattr(etl_db, "APP_ROOT", tmp_path)
    db = _make_db(tmp_path / subdir / "hampton_roads.db")
    assert etl_db.resolve_etl_db() == db
    assert market_data.is_etl_available() is True


def test_legacy_sibling_layout_still_found(monkeypatch, tmp_path):
    """A v2.4.1 install keeps the ETL project beside the app, not inside it."""
    monkeypatch.delenv("ER_ETL_DB", raising=False)
    app = tmp_path / "python_workbench"
    app.mkdir()
    monkeypatch.setattr(etl_db, "APP_ROOT", app)
    db = _make_db(tmp_path / "hampton-roads-etl" / "hampton_roads.db")
    assert etl_db.resolve_etl_db() == db


def test_availability_is_rechecked_not_frozen_at_import(monkeypatch, tmp_path):
    """Dropping the file in and restarting must be enough - and even without a
    restart the next call sees it, because nothing caches the import-time path."""
    monkeypatch.delenv("ER_ETL_DB", raising=False)
    monkeypatch.setattr(etl_db, "APP_ROOT", tmp_path)
    assert market_data.is_etl_available() is False
    _make_db(tmp_path / "data" / "hampton_roads.db")
    assert market_data.is_etl_available() is True


def test_no_ui_panel_tells_the_operator_to_run_the_undeployed_etl_script():
    """The standalone ETL project is not part of this deployment, so an
    instruction to run it is a dead end for the operator."""
    import pathlib

    offenders = []
    for path in (etl_db.APP_ROOT / "ui").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "hampton_roads_etl.py" in line and "Run " in line:
                offenders.append(f"{path.name}:{i}")
    assert offenders == [], f"dead-end instructions remain: {offenders}"
