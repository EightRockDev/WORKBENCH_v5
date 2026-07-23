"""SQLite query helpers for the workbench.

Reads the `properties` table populated by `data/aln_loader.py`. The DB lives
at `python_workbench/data/workbench.db` (gitignored); the loader rebuilds it
from the ALN xlsx whenever needed.

`ensure_db_synced()` is the entry point — it lazily rebuilds the DB if it's
missing OR older than the source xlsx. The Streamlit app calls it once on
startup so the property list is always fresh after a new ALN export drops in.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from data.aln_loader import ALN_FILENAME, sync, multistate_paths, ALN_DATA_DIR

_DATA_DIR = Path(__file__).resolve().parent
DB_PATH = _DATA_DIR / "workbench.db"
SCHEMA_PATH = _DATA_DIR / "schema.sql"
# Back-compat single-file path (workbench root). The multi-state library in
# `ALN Data and Reports/` is the real source now; this is only a fallback.
ALN_PATH = _DATA_DIR.parent.parent / ALN_FILENAME


# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------

def _newest_source_mtime() -> float | None:
    """Most-recent mtime across the multi-state ALN library (or the single
    fallback file). None if no source files exist."""
    paths = multistate_paths()
    if not paths and ALN_PATH.is_file():
        paths = [ALN_PATH]
    if not paths:
        return None
    return max(p.stat().st_mtime for p in paths)


def ensure_db_synced() -> bool:
    """Build or refresh `workbench.db` from the ALN library if needed.

    Resyncs when:
      - DB doesn't exist yet
      - Any source export is newer than the DB (vendor delivered new data)

    Returns True if a sync happened, False if the DB was already current.
    Never raises on missing source: if the DB already exists we keep using
    it; only a missing DB *and* missing sources is an error.
    """
    newest = _newest_source_mtime()

    if not DB_PATH.is_file():
        if newest is None:
            raise FileNotFoundError(
                f"No ALN exports found in {ALN_DATA_DIR} (or fallback "
                f"{ALN_PATH}). Drop the ALN xlsx files and try again."
            )
        sync(None, DB_PATH, SCHEMA_PATH)
        return True

    if newest is not None and newest > DB_PATH.stat().st_mtime:
        sync(None, DB_PATH, SCHEMA_PATH)
        return True

    return False


def force_resync() -> int:
    """Force a full rebuild of `workbench.db` from the full ALN library.
    Returns row count written."""
    return sync(None, DB_PATH, SCHEMA_PATH)


@contextlib.contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection with `Row` factory for dict-like access."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


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
    rebuilding the entire DB from the ALN xlsx. The on-disk source of truth
    for custom properties is `Properties/_custom_props.json` — this function
    keeps the SQLite query layer in sync with that file's latest entry.
    """
    from data.aln_loader import SCHEMA_COLUMNS

    if not prop.get("property_id") or not prop.get("name"):
        raise ValueError("upsert_property requires non-empty property_id + name")

    # Build a row matching SCHEMA_COLUMNS; missing keys → None
    row = {col: prop.get(col) for col in SCHEMA_COLUMNS}
    if not row.get("aln_pull_date"):
        import datetime as dt
        row["aln_pull_date"] = dt.date.today().isoformat()
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
    """Look up a single property by its `property_id` (ALN API Id)."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM properties WHERE property_id = ?",
            (property_id,),
        ).fetchone()
        return dict(row) if row else None


def list_distinct_markets(db_path: Path = DB_PATH) -> list[str]:
    """All distinct ALN market codes present in the DB, alphabetically."""
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
