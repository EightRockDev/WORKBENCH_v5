"""rent_listings schema reconciliation.

Deliberately separate from test_listings.py: that module skips wholesale when
`hampton-roads-etl` is not checked out beside the workbench, and these tests
need nothing but `core.listings_pull`. Filed together, the regression that
killed the 2026-08-01 listings step would have gone unguarded on any machine
without the sibling repo - including CI.
"""

from __future__ import annotations


def test_missing_columns_are_added_to_an_existing_table(tmp_path):
    """The overnight crash: `table rent_listings has no column named name`.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    a machine that first ran the scraper before a column was added keeps
    failing every insert - and it took two successful Zillow scrapes with it,
    the only source that moves the rent-delta gate.
    """
    import sqlite3
    from core.listings_pull import _ROW_COLS, _add_missing_columns

    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        # An old table: property_id + a couple of columns, no `name`.
        conn.execute("""CREATE TABLE rent_listings (
            property_id TEXT, source TEXT, scrape_status TEXT,
            effective_one_br_rent REAL, scraped_at TEXT)""")
        conn.execute("INSERT INTO rent_listings VALUES ('p1','zillow',"
                     "'success', 1500.0, 't')")

    with sqlite3.connect(db) as conn:
        added = _add_missing_columns(conn, "rent_listings")
        assert "name" in added
        cols = {r[1] for r in conn.execute("PRAGMA table_info(rent_listings)")}
        assert set(_ROW_COLS) <= cols
        # the pre-existing row survives the migration
        assert conn.execute("SELECT count(*) FROM rent_listings").fetchone()[0] == 1
        # and the insert that used to crash now works
        conn.execute(
            f"INSERT INTO rent_listings ({', '.join(_ROW_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _ROW_COLS)})",
            tuple(None for _ in _ROW_COLS))


def test_migration_is_idempotent(tmp_path):
    import sqlite3
    from core.listings_pull import _ROW_COLS, _add_missing_columns

    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE rent_listings (property_id TEXT)")
    with sqlite3.connect(db) as conn:
        assert _add_missing_columns(conn, "rent_listings")
        assert _add_missing_columns(conn, "rent_listings") == []


def test_absent_table_is_left_to_create_table(tmp_path):
    """Nothing to migrate before the table exists."""
    import sqlite3
    from core.listings_pull import _add_missing_columns

    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        assert _add_missing_columns(conn, "rent_listings") == []
