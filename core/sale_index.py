"""Offline sale-history index (owner report 2026-08-09: "Streamlit is running
too slow").

Root cause: ``sale_history_for`` scanned EVERY muni row for the property's
market at render time — Virginia Beach carries ~355K rows, so first-open of
any VB property meant seconds of json.loads + normalize per page. The
in-process memo only helped the second view of the same property.

This module extracts sale records ONCE per data refresh into a real indexed
table (``sale_records``) inside workbench.db; the card and radar tenure then
answer from an indexed lookup. The autopilot step (scripts/run_sale_index.py)
rebuilds only when muni_records actually changed (row count + max id stamp).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sale_records (
    market    TEXT,
    state     TEXT,
    apn_norm  TEXT,
    addr_norm TEXT,
    addr_core TEXT,
    date      TEXT,
    price     REAL,
    grantor   TEXT,
    grantee   TEXT,
    notes     TEXT,
    source_url TEXT
);
CREATE INDEX IF NOT EXISTS ix_sale_apn  ON sale_records(apn_norm);
CREATE INDEX IF NOT EXISTS ix_sale_addr ON sale_records(addr_norm);
CREATE TABLE IF NOT EXISTS sale_index_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    muni_stamp TEXT NOT NULL,
    built_at   TEXT NOT NULL
);
"""


def _muni_stamp(conn: sqlite3.Connection) -> str:
    # max(pulled_at) is load-bearing (2026-08-15): count+max(id) are both
    # blind to CONTENT. A puller that refreshes rows in place - Norfolk's
    # FY stack dedupes on (gpin, transfer_date) and replaces, Chesapeake's
    # LandBook re-lands the same parcels - leaves the row count and the max
    # id identical, so this stamp never changed and the index skipped its
    # rebuild indefinitely while the underlying sales moved on. Any refresh
    # bumps pulled_at, so a re-pull now always re-indexes.
    row = conn.execute(
        "SELECT count(*), COALESCE(max(id),0), COALESCE(max(pulled_at),'') "
        "FROM muni_records "
        "WHERE kind LIKE 'assessor%' OR kind LIKE 'sales%'").fetchone()
    # v2 suffix (2026-08-11): rows from scraped-file feeds now store the
    # resolved workbook URL (record's _file) instead of the "files:..."
    # tag. Bumping the stamp forces ONE rebuild so existing rows get real
    # links even when muni_records itself is unchanged.
    return f"{row[0]}:{row[1]}:{row[2]}:v3"


def index_present(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            ok = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE name IN "
                "('sale_records','sale_index_meta')").fetchone()[0] == 2
            return ok and conn.execute(
                "SELECT count(*) FROM sale_index_meta").fetchone()[0] == 1
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def build(db_path: Path, *, force: bool = False) -> dict:
    """(Re)build sale_records from muni_records. Returns a stats dict; a
    no-op (fresh stamp) returns {'skipped': True, ...}. Runs in the autopilot
    (single writer, WAL) — never from a page render."""
    from core import phase0, sale_history

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        # Migration (2026-08-11, clickable sale sources): older indexes lack
        # source_url. Add it AND force a rebuild this run so every row gets
        # its link - otherwise the fresh-stamp gate would keep link-less rows
        # alive until the next muni change.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sale_records)")}
        if "source_url" not in cols:
            conn.execute("ALTER TABLE sale_records ADD COLUMN source_url TEXT")
            force = True
        # 2026-08-15: looser house-number+street-name key, so a vendor's
        # "3000 S. Cape Henry" still finds the assessor's "3000 CAPE HENRY
        # AVE". Existing indexes have no such column; add it and rebuild once
        # so every row gets one.
        if "addr_core" not in cols:
            conn.execute("ALTER TABLE sale_records ADD COLUMN addr_core TEXT")
            force = True
        # Created here rather than in the schema block: the schema runs before
        # the migration above, and an index over a column a pre-existing table
        # does not have yet fails outright.
        conn.execute("CREATE INDEX IF NOT EXISTS ix_sale_core "
                     "ON sale_records(market, addr_core)")
        stamp = _muni_stamp(conn)
        prev = conn.execute(
            "SELECT muni_stamp FROM sale_index_meta WHERE id=1").fetchone()
        if prev and prev[0] == stamp and not force:
            return {"skipped": True, "stamp": stamp}

        rows_in = 0
        sales = []
        cur = conn.execute(
            "SELECT market, state, record, source_url FROM muni_records "
            "WHERE kind LIKE 'assessor%' OR kind LIKE 'sales%'")
        for market, state, record, src in cur:
            rows_in += 1
            try:
                raw = json.loads(record) if record else {}
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(raw, dict):
                continue
            recs = sale_history.extract_sale_records(raw)
            if not recs:
                continue
            # Scraped-file rows (rva.gov workbooks) carry the resolved file
            # URL in _file; the muni source_url is only the "files:..."
            # tag, which is not clickable in the sale-history card.
            f = raw.get("_file")
            if isinstance(f, str) and f.startswith("http"):
                src = f
            norm = phase0.normalize_record(market or "", state or "", raw)
            apn_n = sale_history._norm_apn(norm.get("apn"))
            addr_n = sale_history._norm_addr(norm.get("address"))
            core_n = sale_history._addr_core(norm.get("address"))
            if not (apn_n or addr_n):
                continue
            for r in recs:
                sales.append((market, state, apn_n, addr_n, core_n, r["date"],
                              r["price"], r["grantor"], r["grantee"],
                              r["notes"], src))

        with conn:                                    # one atomic txn
            conn.execute("DELETE FROM sale_records")
            conn.executemany(
                "INSERT INTO sale_records (market,state,apn_norm,addr_norm,"
                "addr_core,date,price,grantor,grantee,notes,source_url) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                sales)
            conn.execute(
                "INSERT INTO sale_index_meta (id, muni_stamp, built_at) "
                "VALUES (1, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET muni_stamp=excluded.muni_stamp,"
                "built_at=excluded.built_at", (stamp,))
        return {"skipped": False, "stamp": stamp, "scanned": rows_in,
                "sales": len(sales)}
    finally:
        conn.close()


def lookup(db_path: Path, *, apn_norm: str, addr_norm: str,
           addr_core: str = "", market: str = "") -> list[dict] | None:
    """Indexed sale lookup. None = index unavailable (caller falls back to
    the live scan); [] = index present, genuinely no sales."""
    if not index_present(db_path):
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        # source_url may be absent on a not-yet-migrated index; COALESCE via
        # try/except keeps lookup working either way.
        cols = "date, price, grantor, grantee, notes, source_url"
        try:
            conn.execute(f"SELECT {cols} FROM sale_records LIMIT 0")
        except sqlite3.OperationalError:
            cols = "date, price, grantor, grantee, notes, NULL AS source_url"
        rows = []
        if apn_norm:
            rows = conn.execute(
                f"SELECT {cols} FROM sale_records"
                " WHERE apn_norm = ?", (apn_norm,)).fetchall()
        if not rows and addr_norm:
            rows = conn.execute(
                f"SELECT {cols} FROM sale_records"
                " WHERE addr_norm = ?", (addr_norm,)).fetchall()
        # Last resort: house number + street name, within this market only.
        if not rows and addr_core and market:
            try:
                rows = conn.execute(
                    f"SELECT {cols} FROM sale_records"
                    " WHERE market = ? AND addr_core = ?",
                    (market, addr_core)).fetchall()
            except sqlite3.OperationalError:
                rows = []      # column predates this build
            if not rows:
                # The addr_core COLUMN is only filled by a rebuild, and the
                # app never rebuilds - only the nightly cycle does. Waiting
                # for that would mean the fix does nothing until tomorrow.
                # Derive the same key at query time from addr_norm instead:
                # the house number narrows it to a handful of rows, and the
                # comparison happens here. Works the moment the app restarts.
                from core import sale_history as _sh
                number = addr_core.split("|", 1)[0]
                try:
                    cand = conn.execute(
                        f"SELECT {cols}, addr_norm FROM sale_records"
                        " WHERE market = ? AND addr_norm LIKE ?",
                        (market, f"{number} %")).fetchall()
                except sqlite3.Error:
                    cand = []
                rows = [r for r in cand
                        if _sh._addr_core(r["addr_norm"]) == addr_core]
        out, seen = [], set()
        for r in rows:
            key = (r["date"], r["price"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"date": r["date"], "price": r["price"],
                        "grantor": r["grantor"] or "",
                        "grantee": r["grantee"] or "",
                        "notes": r["notes"] or "",
                        "source_url": r["source_url"] or "",
                        "source": "assessor transfer record"})
        out.sort(key=lambda x: (x.get("date") or ""), reverse=True)
        return out
    except sqlite3.Error:
        return None
    finally:
        conn.close()
