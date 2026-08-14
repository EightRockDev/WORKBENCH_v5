"""SQLite query helpers for the workbench.

Reads the `properties` table populated by `data/legacy_loader.py`. The DB lives
at `python_workbench/data/workbench.db` (gitignored); the loader rebuilds it
from the the licensed xlsx whenever needed.

`ensure_db_synced()` is the entry point — it lazily rebuilds the DB if it's
missing OR older than the source xlsx. The Streamlit app calls it once on
startup so the property list is always fresh after a new licensed export drops in.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from data.legacy_loader import LEGACY_FILENAME, sync, multistate_paths, LEGACY_DATA_DIR

_DATA_DIR = Path(__file__).resolve().parent
DB_PATH = _DATA_DIR / "workbench.db"
SCHEMA_PATH = _DATA_DIR / "schema.sql"
# Back-compat single-file path (workbench root). The multi-state library in
# `Property Data and Reports/` is the real source now; this is only a fallback.
LEGACY_PATH = _DATA_DIR.parent.parent / LEGACY_FILENAME


# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------

def _newest_source_mtime() -> float | None:
    """Most-recent mtime across the multi-state licensed export library (or the single
    fallback file). None if no source files exist."""
    paths = multistate_paths()
    if not paths and LEGACY_PATH.is_file():
        paths = [LEGACY_PATH]
    if not paths:
        return None
    return max(p.stat().st_mtime for p in paths)


# Columns renamed by the Phase-0 de-identification (spec §7.3). A workbench.db
# built before that carries the old names; the loader would fix them on its
# next full rebuild, but the rebuild only runs when a source export is newer
# than the DB. Without this, an existing install pulls the new code and every
# write against `properties` fails on a missing column.
_RENAMED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("aln_id", "legacy_id"),
    ("aln_pull_date", "pull_date"),
)


def migrate_legacy_columns(db_path: Path = DB_PATH) -> list[str]:
    """Rename any pre-Phase-0 columns still present. Returns what was renamed.

    Idempotent and safe on a fresh DB: if the old name is absent, or the new
    name already exists, the rename is skipped.
    """
    if not Path(db_path).is_file():
        return []
    done: list[str] = []
    with get_connection(db_path) as conn:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "properties" not in tables:
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(properties)")}
        for old, new in _RENAMED_COLUMNS:
            if old in cols and new not in cols:
                conn.execute(
                    f"ALTER TABLE properties RENAME COLUMN {old} TO {new}"
                )
                done.append(f"{old} -> {new}")
        if done:
            conn.commit()
    return done


def ensure_db_synced() -> bool:
    """Build or refresh `workbench.db` from the licensed export library if needed.

    Resyncs when:
      - DB doesn't exist yet
      - Any source export is newer than the DB (vendor delivered new data)

    Returns True if a sync happened, False if the DB was already current.
    Never raises on missing source: if the DB already exists we keep using
    it; only a missing DB *and* missing sources is an error.

    Always runs the Phase-0 column migration first so an existing install
    keeps working after pulling the de-identified schema.
    """
    migrate_legacy_columns()
    newest = _newest_source_mtime()

    if not DB_PATH.is_file():
        if newest is None:
            # No licensed source present. In v5.0 the property spine is being replaced by
            # the self-sourced 8R property spine (Phase 0 / Module F), so a clean
            # deployment legitimately has no the licensed xlsx. Rather than crash, create
            # an empty schema-only DB so the app boots with an empty inventory;
            # properties arrive via the spine or manual entry.
            init_empty_db()
            return True
        sync(None, DB_PATH, SCHEMA_PATH)
        return True

    if newest is not None and newest > DB_PATH.stat().st_mtime:
        sync(None, DB_PATH, SCHEMA_PATH)
        return True

    return False


def init_empty_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    """Create a schema-only (empty) SQLite DB. Used when no licensed source exists so
    the app boots with an empty property inventory instead of raising."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()


def force_resync() -> int:
    """Force a full rebuild of `workbench.db` from the full licensed export library.
    Returns row count written."""
    return sync(None, DB_PATH, SCHEMA_PATH)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Make the file safe for several processes at once.

    The pilot runs a blue-green service PAIR against this one file, and the
    hourly autopilot writes to it too. Under SQLite's default rollback
    journal a single writer blocks every reader for the length of its
    transaction, which surfaces as "database is locked" in the UI. WAL lets
    readers carry on through a write, which is exactly this access pattern.

    WAL is a persistent property of the database file, so this is a no-op
    after the first connection. It is unavailable on network shares — the
    deploy scripts already refuse to run from OneDrive/Dropbox for the same
    reason — so a failure here degrades to the old journal rather than
    raising.
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # Wait rather than fail instantly when another process holds a lock.
        conn.execute("PRAGMA busy_timeout=10000")
        # Safe with WAL: survives process crash, only risks the last commits
        # on a full power loss, and avoids an fsync per transaction.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error:
        pass


@contextlib.contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection with `Row` factory for dict-like access."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# P0-3 cutover read seam (spec 7.3)
#
# Every UI/engine read funnels through list_properties/get_property, so the
# spine cutover has exactly one seam: when config.SPINE_READ_SOURCE is
# "8r", these two functions serve the self-sourced backbone
# (properties_8r) adapted to the legacy row shape, and legacy ids keep
# resolving through the property_crosswalk table. Default stays "legacy"
# until the P0-2 gates hold.
# ---------------------------------------------------------------------------

def _read_source() -> str:
    import config
    return getattr(config, "SPINE_READ_SOURCE", "legacy")


def _r8_to_legacy_shape(row: dict[str, Any]) -> dict[str, Any]:
    """Adapt one backbone row to the dict shape the read sites expect.

    Consumers were written against legacy `properties` columns
    (latitude/longitude, avg_rent, property_type...). The backbone keeps
    its own names; this is the ONLY translation point. Fields the
    backbone cannot source yet are explicit Nones - never fabricated.
    """
    units = row.get("units")
    sqft = row.get("sqft")
    avg_sqft = (sqft / units) if sqft and units else None
    avg_rent = row.get("est_avg_rent")
    return {
        "property_id": row["property_id"],
        "legacy_id": None,                     # 8R-native rows have no vendor id
        "name": row.get("address"),         # rolls carry no marketing name
        "address": row.get("address"),
        "city": row.get("city"),
        "state": row.get("state"),
        "zip": row.get("zip"),
        "units": units,
        "year_built": row.get("year_built"),
        "avg_sqft": avg_sqft,
        "avg_rent": avg_rent,
        "rent_per_sqft": (avg_rent / avg_sqft)
                         if avg_rent and avg_sqft else None,
        "occupancy_pct": None,              # awaits self-sourced survey data
        "asset_class": None,                # awaits 8r_class (spec 7.2)
        "property_type": row.get("r8_form"),
        "market": row.get("r8_market"),
        "submarket": row.get("r8_submarket"),
        "owner": row.get("owner_name"),
        # The owner's MAILING address off the assessor roll - the skip-trace
        # pipeline's S1 anchor and S4 trace input (2026-08-13). Without it
        # every trace ran on the property address, which for an LLC-owned
        # building is where the owner is not.
        "owner_address": row.get("owner_address"),
        "management_company": None,         # awaits Module A resolution
        "latitude": row.get("lat"),
        "longitude": row.get("lng"),
        "assessed_value": row.get("assessed_value"),
        "use_code": row.get("use_code"),
        "apn": row.get("apn"),              # parcel id — lets sale_history match
        "source_file": "properties_8r",
        "provenance": row.get("provenance") or "8r",
        "rent_source": row.get("rent_source"),
    }


def _list_properties_8r(
    *, city=None, state=None, cities=None, units_min=None, units_max=None,
    search=None, management_company=None, require_latlng=False,
    market=None, asset_class=None, limit=500, db_path=DB_PATH,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if city:
        where.append("city = ?")
        params.append(city)
    if state:
        where.append("state = ?")
        params.append(state)
    if cities:
        where.append(f"city IN ({', '.join('?' for _ in cities)})")
        params.extend(cities)
    if units_min is not None:
        where.append("units >= ?")
        params.append(units_min)
    if units_max is not None:
        where.append("units <= ?")
        params.append(units_max)
    if search:
        where.append("(address LIKE ? OR city LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if market:
        where.append("r8_market = ?")
        params.append(market)
    if management_company:
        # The backbone has no management data until Module A resolves it;
        # a management filter can honestly match nothing.
        return []
    if asset_class:
        # 8r_class not derived yet (spec 7.2) - same honest empty result.
        return []
    if require_latlng:
        where.append("lat IS NOT NULL AND lng IS NOT NULL")
    sql = "SELECT * FROM properties_8r"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY address LIMIT ?"
    params.append(limit)
    with get_connection(db_path) as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
    return [_r8_to_legacy_shape(dict(r)) for r in rows]


def _get_property_8r(property_id: str,
                     db_path: Path = DB_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT * FROM properties_8r WHERE property_id = ?",
                (property_id,)).fetchone()
            if row is None and not property_id.startswith("8R-"):
                # A legacy id (deal reference, saved favorite) resolves
                # through the crosswalk so nothing breaks at flip time.
                row = conn.execute(
                    """SELECT p.* FROM properties_8r p
                         JOIN property_crosswalk x
                           ON x.r8_property_id = p.property_id
                        WHERE x.legacy_property_id = ?""",
                    (property_id,)).fetchone()
        except sqlite3.Error:
            return None
        return _r8_to_legacy_shape(dict(row)) if row else None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_properties(
    *,
    market: str | None = None,
    asset_class: str | None = None,
    city: str | None = None,
    state: str | None = None,
    cities: list[str] | None = None,
    units_min: int | None = None,
    units_max: int | None = None,
    search: str | None = None,
    management_company: str | None = None,
    require_latlng: bool = False,
    limit: int = 500,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return properties matching the filters, ordered by name.

    `search` does a case-insensitive substring match against name, address,
    and city. `management_company` does a substring match on the
    `management_company` column (e.g. "Drucker" matches "Drucker + Falk, LLC").
    `units_min`/`units_max` are inclusive. `require_latlng` filters out
    properties without geocoded coordinates (useful for the comps map).
    """
    if _read_source() == "8r":
        return _list_properties_8r(
            city=city, state=state, cities=cities, units_min=units_min,
            units_max=units_max, search=search,
            management_company=management_company,
            require_latlng=require_latlng, market=market,
            asset_class=asset_class, limit=limit, db_path=db_path)
    where: list[str] = []
    params: list[Any] = []

    if market:
        where.append("market = ?")
        params.append(market)
    if asset_class:
        where.append("asset_class = ?")
        params.append(asset_class)
    if city:
        where.append("city = ?")
        params.append(city)
    if state:
        where.append("state = ?")
        params.append(state)
    if cities:
        # Multi-city IN-list (e.g., "All Hampton Roads" preset)
        placeholders = ", ".join("?" for _ in cities)
        where.append(f"city IN ({placeholders})")
        params.extend(cities)
    if units_min is not None:
        where.append("units >= ?")
        params.append(units_min)
    if units_max is not None:
        where.append("units <= ?")
        params.append(units_max)
    if search:
        # SQLite LIKE is case-insensitive for ASCII by default
        where.append("(name LIKE ? OR address LIKE ? OR city LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if management_company:
        where.append("management_company LIKE ?")
        params.append(f"%{management_company}%")
    if require_latlng:
        where.append("latitude IS NOT NULL AND longitude IS NOT NULL")

    sql = "SELECT * FROM properties"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def list_management_companies(
    *,
    min_count: int = 1,
    db_path: Path = DB_PATH,
) -> list[tuple[str, int]]:
    """Return all distinct management companies in the DB with a count of
    properties they manage, ordered by count desc then name asc.

    `min_count` filters out one-off entries (defaults to 1, i.e. include all).
    Bump to 2+ in the sidebar to keep the list short.
    """
    sql = (
        "SELECT management_company, COUNT(*) AS n "
        "FROM properties "
        "WHERE management_company IS NOT NULL AND TRIM(management_company) != '' "
        "GROUP BY management_company "
        "HAVING n >= ? "
        "ORDER BY n DESC, management_company ASC"
    )
    with get_connection(db_path) as conn:
        return [(row[0], row[1]) for row in conn.execute(sql, (min_count,)).fetchall()]


def upsert_property(
    prop: dict[str, Any],
    db_path: Path = DB_PATH,
) -> None:
    """Insert or replace a single row in the `properties` table.

    Used to surface a freshly-added custom property in the sidebar without
    rebuilding the entire DB from the the licensed xlsx. The on-disk source of truth
    for custom properties is `Properties/_custom_props.json` — this function
    keeps the SQLite query layer in sync with that file's latest entry.
    """
    from data.legacy_loader import SCHEMA_COLUMNS

    if not prop.get("property_id") or not prop.get("name"):
        raise ValueError("upsert_property requires non-empty property_id + name")

    # Build a row matching SCHEMA_COLUMNS; missing keys → None
    row = {col: prop.get(col) for col in SCHEMA_COLUMNS}
    if not row.get("pull_date"):
        import datetime as dt
        row["pull_date"] = dt.date.today().isoformat()
    if not row.get("raw_row"):
        import json
        row["raw_row"] = json.dumps(prop, default=str)

    cols = ", ".join(SCHEMA_COLUMNS)
    placeholders = ", ".join("?" for _ in SCHEMA_COLUMNS)
    values = tuple(row[c] for c in SCHEMA_COLUMNS)

    with get_connection(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO properties ({cols}) VALUES ({placeholders})",
            values,
        )
        conn.commit()


def get_property(
    property_id: str,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """Look up a single property by its `property_id` (provider API Id, or an
    8R backbone id / crosswalked legacy id when the cutover flag is on)."""
    if _read_source() == "8r":
        return _get_property_8r(property_id, db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM properties WHERE property_id = ?",
            (property_id,),
        ).fetchone()
        return dict(row) if row else None


def list_distinct_markets(db_path: Path = DB_PATH) -> list[str]:
    """All distinct legacy market codes present in the DB, alphabetically."""
    with get_connection(db_path) as conn:
        return [
            row[0] for row in conn.execute(
                "SELECT DISTINCT market FROM properties "
                "WHERE market IS NOT NULL AND market != '' "
                "ORDER BY market"
            )
        ]


def list_distinct_cities(
    state: str | None = None,
    db_path: Path = DB_PATH,
) -> list[str]:
    """All distinct cities present in the DB, alphabetically. When `state`
    is given, only cities in that state are returned."""
    sql = "SELECT DISTINCT city FROM properties WHERE city IS NOT NULL AND city != ''"
    params: list[Any] = []
    if state:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY city"
    with get_connection(db_path) as conn:
        return [row[0] for row in conn.execute(sql, params)]


# Eight Rock's target-state footprint (VA·NC·SC·GA, plus TN in the data).
# Ordered so the home markets surface first in dropdowns.
TARGET_STATES: tuple[tuple[str, str], ...] = (
    ("VA", "Virginia"),
    ("NC", "North Carolina"),
    ("SC", "South Carolina"),
    ("GA", "Georgia"),
    ("TN", "Tennessee"),
)


def list_distinct_states(
    target_first: bool = True,
    db_path: Path = DB_PATH,
) -> list[str]:
    """All distinct state codes in the DB. When `target_first`, the Eight
    Rock target states (VA/NC/SC/GA/TN) are ordered first, then the rest
    alphabetically."""
    with get_connection(db_path) as conn:
        rows = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT state FROM properties "
                "WHERE state IS NOT NULL AND state != '' AND LENGTH(state) = 2 "
                "ORDER BY state"
            )
        ]
    if not target_first:
        return rows
    targets = [s for s, _ in TARGET_STATES]
    head = [s for s in targets if s in rows]
    tail = [s for s in rows if s not in targets]
    return head + tail


def city_counts_for_state(
    state: str,
    db_path: Path = DB_PATH,
) -> list[tuple[str, int]]:
    """Cities in a state with property counts, most properties first."""
    with get_connection(db_path) as conn:
        return [
            (row[0], row[1]) for row in conn.execute(
                "SELECT city, COUNT(*) n FROM properties "
                "WHERE state = ? AND city IS NOT NULL AND city != '' "
                "GROUP BY city ORDER BY n DESC, city ASC",
                (state,),
            )
        ]


def count_properties(
    *,
    market: str | None = None,
    asset_class: str | None = None,
    city: str | None = None,
    state: str | None = None,
    units_min: int | None = None,
    units_max: int | None = None,
    search: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Count properties matching the same filters as `list_properties`."""
    where: list[str] = []
    params: list[Any] = []

    if market:
        where.append("market = ?")
        params.append(market)
    if asset_class:
        where.append("asset_class = ?")
        params.append(asset_class)
    if city:
        where.append("city = ?")
        params.append(city)
    if state:
        where.append("state = ?")
        params.append(state)
    if units_min is not None:
        where.append("units >= ?")
        params.append(units_min)
    if units_max is not None:
        where.append("units <= ?")
        params.append(units_max)
    if search:
        where.append("(name LIKE ? OR address LIKE ? OR city LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    sql = "SELECT COUNT(*) FROM properties"
    if where:
        sql += " WHERE " + " AND ".join(where)

    with get_connection(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]
