"""SQLite -> Postgres migration and its verifier (build order 2, P0.5).

Skipped automatically when DATABASE_URL / [postgres].url is unset, like the
other Postgres suites. To run them:

    DATABASE_URL=postgresql://... uv run pytest tests/test_migrate_sqlite_to_pg.py

The negative cases matter more than the positive one. A verifier that only
ever reports OK is worse than no verifier: it converts "we did not check" into
"we checked and it was fine", which is exactly how a silent data loss ships.
"""

from __future__ import annotations

import sqlite3

import pytest

from data import pg

pytestmark = pytest.mark.skipif(not pg.is_reachable(),
                                reason="Postgres not reachable (DATABASE_URL unset or server down)")

from scripts.migrate_sqlite_to_pg import (  # noqa: E402
    copy_table,
    create_table,
    sqlite_columns,
    sqlite_primary_key,
    verify_table,
)


def _sqlite(tmp_path, rows=(("a", "Alpha", 1.5), ("b", "Beta", 2.5))):
    db = tmp_path / "src.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY, label TEXT, v REAL)")
    conn.executemany("INSERT INTO t VALUES (?,?,?)", rows)
    conn.commit()
    return conn


@pytest.fixture()
def pg_table():
    """A scratch table dropped after the test, whatever the outcome."""
    name = "mig_test_t"
    with pg.connection() as c:
        with c.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS "{name}"')
        c.commit()
    yield name
    with pg.connection() as c:
        with c.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS "{name}"')
        c.commit()


def _migrate(sq, table):
    pk = sqlite_primary_key(sq, "t")
    cols = sqlite_columns(sq, "t")
    with pg.connection() as pgc:
        create_table(pgc, table, cols, pk)
        n = copy_table(sq, pgc, table, cols, pk, sq_table="t")
    return pk, cols, n


def _verify(sq, table, pk, cols):
    with pg.connection() as pgc:
        return verify_table(sq, pgc, table, cols, pk, sq_table="t")


def test_primary_key_is_read_from_the_schema(tmp_path):
    """Hard-coding it got calibration_current wrong; derive it instead."""
    sq = _sqlite(tmp_path)
    assert sqlite_primary_key(sq, "t") == "k"


def test_composite_key_is_refused_rather_than_silently_mishandled(tmp_path):
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a TEXT, b TEXT, PRIMARY KEY (a, b))")
    with pytest.raises(ValueError, match="exactly one primary-key"):
        sqlite_primary_key(conn, "t")


def test_round_trip_copies_and_verifies(tmp_path, pg_table):
    sq = _sqlite(tmp_path)
    pk, cols, n = _migrate(sq, pg_table)
    assert n == 2
    rep = _verify(sq, pg_table, pk, cols)
    assert rep.ok
    assert rep.sqlite_rows == rep.pg_rows == 2


def test_rerunning_upserts_instead_of_duplicating(tmp_path, pg_table):
    """A partial failure must be safe to re-run."""
    sq = _sqlite(tmp_path)
    pk, cols, _ = _migrate(sq, pg_table)
    _migrate(sq, pg_table)
    rep = _verify(sq, pg_table, pk, cols)
    assert rep.ok and rep.pg_rows == 2


def test_updated_source_row_is_carried_over_on_rerun(tmp_path, pg_table):
    sq = _sqlite(tmp_path)
    pk, cols, _ = _migrate(sq, pg_table)
    sq.execute("UPDATE t SET label='Changed' WHERE k='a'")
    sq.commit()
    _migrate(sq, pg_table)
    with pg.connection() as c, c.cursor() as cur:
        cur.execute(f'SELECT label FROM "{pg_table}" WHERE k=%s', ("a",))
        assert cur.fetchone()["label"] == "Changed"


def test_verifier_catches_a_missing_row(tmp_path, pg_table):
    sq = _sqlite(tmp_path)
    pk, cols, _ = _migrate(sq, pg_table)
    with pg.connection() as c, c.cursor() as cur:
        cur.execute(f'DELETE FROM "{pg_table}" WHERE k=%s', ("b",))
        c.commit()
    rep = _verify(sq, pg_table, pk, cols)
    assert not rep.ok
    assert "b" in rep.missing_keys


def test_verifier_catches_an_extra_row(tmp_path, pg_table):
    sq = _sqlite(tmp_path)
    pk, cols, _ = _migrate(sq, pg_table)
    with pg.connection() as c, c.cursor() as cur:
        cur.execute(f'INSERT INTO "{pg_table}" (k, label, v) VALUES (%s,%s,%s)',
                    ("ghost", "Ghost", 9.0))
        c.commit()
    rep = _verify(sq, pg_table, pk, cols)
    assert not rep.ok
    assert "ghost" in rep.extra_keys


def test_verifier_catches_a_changed_value(tmp_path, pg_table):
    """Row counts and keys would both still match here — the value check is
    the only thing standing between a corrupted copy and a green report."""
    sq = _sqlite(tmp_path)
    pk, cols, _ = _migrate(sq, pg_table)
    with pg.connection() as c, c.cursor() as cur:
        cur.execute(f'UPDATE "{pg_table}" SET label=%s WHERE k=%s', ("WRONG", "a"))
        c.commit()
    rep = _verify(sq, pg_table, pk, cols)
    assert not rep.ok
    assert rep.sqlite_rows == rep.pg_rows          # counts alone would pass
    assert not rep.missing_keys and not rep.extra_keys   # keys alone would pass
    assert any("label" in m for m in rep.value_mismatches)


def test_float_precision_does_not_trip_the_comparison(tmp_path, pg_table):
    """SQLite REAL -> Postgres DOUBLE PRECISION must not read as a mismatch."""
    sq = _sqlite(tmp_path, rows=(("a", "Alpha", 1.0 / 3.0),))
    pk, cols, _ = _migrate(sq, pg_table)
    assert _verify(sq, pg_table, pk, cols).ok


def test_nulls_round_trip(tmp_path, pg_table):
    sq = _sqlite(tmp_path, rows=(("a", None, None),))
    pk, cols, _ = _migrate(sq, pg_table)
    assert _verify(sq, pg_table, pk, cols).ok
