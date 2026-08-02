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


# ---------------------------------------------------------------------------
# Favorites must resolve the same way the UI resolves them (2026-08-01)
# ---------------------------------------------------------------------------

def _fav_db(tmp_path, monkeypatch, favorites, rows):
    """A properties table plus a _favorites.json, wired to temp paths."""
    import json
    import sqlite3

    import data.db as dbmod
    import core.listings_pull as lp

    db = tmp_path / "workbench.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE properties (
            property_id TEXT PRIMARY KEY, legacy_id TEXT, name TEXT,
            address TEXT, city TEXT, state TEXT)""")
        conn.executemany("INSERT INTO properties VALUES (?,?,?,?,?,?)", rows)

    props = tmp_path / "Properties"
    props.mkdir()
    (props / "_favorites.json").write_text(json.dumps(favorites))

    monkeypatch.setattr(dbmod, "DB_PATH", db)
    monkeypatch.setattr(lp, "_properties_root", lambda: props)
    return lp


def test_favorite_saved_under_the_old_id_prefix_still_scrapes(
        tmp_path, monkeypatch):
    """The regression this guards: the de-identification changed synthesized
    ids from `aln-<n>` to `legacy-<n>`. property_io normalizes, so the UI kept
    showing these starred — while the scraper matched exactly and skipped
    them. A star that yields no scrape looks like the source was tried."""
    lp = _fav_db(
        tmp_path, monkeypatch,
        favorites=["aln-134263"],
        rows=[("legacy-134263", "134263", "Miars Farm", "1 Main St",
               "Norfolk", "VA")])
    universe = lp.favorite_universe()
    assert [p["name"] for p in universe] == ["Miars Farm"]


def test_modern_uuid_favorites_still_resolve(tmp_path, monkeypatch):
    lp = _fav_db(
        tmp_path, monkeypatch,
        favorites=["8R-51710-aaaaaaaaaaaa"],
        rows=[("8R-51710-aaaaaaaaaaaa", None, "Acclaim", "2 Bay Ave",
               "Norfolk", "VA")])
    assert len(lp.favorite_universe()) == 1


def test_distinct_native_ids_are_not_merged(tmp_path, monkeypatch):
    """Normalization must not make two different 8R ids collide."""
    lp = _fav_db(
        tmp_path, monkeypatch,
        favorites=["8R-51710-aaaaaaaaaaaa"],
        rows=[("8R-51710-aaaaaaaaaaaa", None, "Wanted", "1 A St", "Norfolk", "VA"),
              ("8R-51710-bbbbbbbbbbbb", None, "Other", "2 B St", "Norfolk", "VA")])
    assert [p["name"] for p in lp.favorite_universe()] == ["Wanted"]


def test_no_favorites_is_not_an_error(tmp_path, monkeypatch):
    lp = _fav_db(tmp_path, monkeypatch, favorites=[], rows=[])
    assert lp.favorite_universe() == []


# ---------------------------------------------------------------------------
# Freshness must not outlive its reasons (2026-08-02)
# ---------------------------------------------------------------------------

def _etl_db(tmp_path, *, pulled_at, description="Scraped rent listings",
            rows=5):
    import sqlite3

    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE etl_metadata (
            table_name TEXT PRIMARY KEY, display_name TEXT, description TEXT,
            source_url TEXT, fetch_method TEXT, row_count INTEGER,
            last_pull_at TEXT, last_pull_date TEXT)""")
        conn.execute("INSERT INTO etl_metadata VALUES (?,?,?,?,?,?,?,?)",
                     ("rent_listings", "Scraped rent listings", description,
                      "etl_listings", "api", rows, pulled_at, pulled_at[:10]))
    return db


def test_a_changed_favourite_set_defeats_the_freshness_skip(tmp_path):
    """Starring a property is an instruction to scrape it. Waiting out a
    7-day window to honour that makes the feature look broken, and the rent
    gate cannot move without new rows."""
    import datetime as dt

    from core.listings_pull import _favorites_fingerprint, _fingerprint_unchanged

    yesterday = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    old = _favorites_fingerprint([{"property_id": "a"}])
    db = _etl_db(tmp_path, pulled_at=yesterday,
                 description=f"Scraped rent listings; favset={old}")

    assert _fingerprint_unchanged(db, old)          # same set -> may skip
    new = _favorites_fingerprint([{"property_id": "a"}, {"property_id": "b"}])
    assert not _fingerprint_unchanged(db, new)      # a new star -> must run


def test_the_fingerprint_ignores_ordering(tmp_path):
    """Favourite order is not meaningful; re-scraping on it would be churn."""
    from core.listings_pull import _favorites_fingerprint

    a = _favorites_fingerprint([{"property_id": "x"}, {"property_id": "y"}])
    b = _favorites_fingerprint([{"property_id": "y"}, {"property_id": "x"}])
    assert a == b


def test_a_failed_attempt_clears_its_own_freshness(tmp_path):
    """The 2026-08-01 crash was STICKY: the stamp from the previous success
    kept the step skipping, so the failure stayed invisible and the fix
    shipped for it could not run."""
    import datetime as dt

    from core.listings_pull import (_favorites_fingerprint,
                                    _fingerprint_unchanged,
                                    invalidate_freshness)

    fp = _favorites_fingerprint([{"property_id": "a"}])
    today = dt.datetime.now().isoformat(timespec="seconds")
    db = _etl_db(tmp_path, pulled_at=today,
                 description=f"Scraped rent listings; favset={fp}")

    assert _fingerprint_unchanged(db, fp)     # would skip
    invalidate_freshness(db)
    assert not _fingerprint_unchanged(db, fp)  # now retries


def test_invalidate_is_safe_on_a_database_with_no_metadata(tmp_path):
    import sqlite3

    from core.listings_pull import invalidate_freshness

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    invalidate_freshness(db)          # must not raise


def test_the_runner_catches_and_invalidates():
    """An uncaught exception both failed the cycle - contradicting the
    runner's own docstring - and left the stale stamp in place."""
    import inspect

    from scripts import run_listings

    src = inspect.getsource(run_listings.main)
    assert "except Exception" in src
    assert "invalidate_freshness" in src
