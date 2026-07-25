"""Finding and adopting an existing hampton_roads.db from a previous install."""

from __future__ import annotations

import sqlite3

from core import etl_db, etl_locate, market_data


def _make_db(path, rows: int = 1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE hud_fmr (zip TEXT, rent INTEGER, pad TEXT)")
        db.executemany("INSERT INTO hud_fmr VALUES ('23504', 1400, ?)",
                       [("x" * 500,) for _ in range(rows)])
    return path


def test_finds_the_db_in_a_previous_install(tmp_path):
    old = _make_db(tmp_path / "python_workbench_old" / "hampton-roads-etl" / "hampton_roads.db")
    hits = etl_locate.find_existing_db([tmp_path])
    assert old.resolve() in [h.resolve() for h in hits]


def test_prefers_the_largest_candidate(tmp_path):
    """A tiny file is usually an aborted run; the real ETL output is large."""
    _make_db(tmp_path / "aborted" / "hampton_roads.db", rows=1)
    real = _make_db(tmp_path / "real" / "hampton_roads.db", rows=400)
    hits = etl_locate.find_existing_db([tmp_path])
    assert hits[0].resolve() == real.resolve()


def test_skips_heavy_directories(tmp_path):
    buried = _make_db(tmp_path / "node_modules" / "hampton_roads.db")
    assert buried.is_file()
    assert etl_locate.find_existing_db([tmp_path]) == []


def test_does_not_descend_past_the_depth_limit(tmp_path):
    deep = tmp_path
    for i in range(etl_locate.MAX_DEPTH + 2):
        deep = deep / f"lvl{i}"
    _make_db(deep / "hampton_roads.db")
    assert etl_locate.find_existing_db([tmp_path]) == []


def test_adopt_copies_into_place_and_leaves_the_original(monkeypatch, tmp_path):
    monkeypatch.delenv("ER_ETL_DB", raising=False)
    app = tmp_path / "WORKBENCH_V5"
    app.mkdir()
    monkeypatch.setattr(etl_db, "APP_ROOT", app)
    source = _make_db(tmp_path / "old" / "hampton-roads-etl" / "hampton_roads.db", rows=50)

    assert market_data.is_etl_available() is False
    target = etl_locate.adopt(source)

    assert target == app / "data" / "hampton_roads.db"
    assert target.is_file()
    assert source.is_file(), "the previous workbench must keep working"
    assert target.stat().st_size == source.stat().st_size
    assert market_data.is_etl_available() is True


def test_adopt_is_a_no_op_when_the_file_is_already_in_place(monkeypatch, tmp_path):
    """Copying a file onto itself would truncate it to zero bytes."""
    monkeypatch.delenv("ER_ETL_DB", raising=False)
    app = tmp_path / "WORKBENCH_V5"
    app.mkdir()
    monkeypatch.setattr(etl_db, "APP_ROOT", app)
    target = _make_db(app / "data" / "hampton_roads.db", rows=20)
    size = target.stat().st_size

    assert etl_locate.adopt(target) == target
    assert target.stat().st_size == size
    assert market_data.is_etl_available() is True
