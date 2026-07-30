"""Alert sweep (spec 6.1): continuous monitoring, not pull-based panels.

Runs inside every autopilot cycle on the host, AFTER the backbone
rebuild, and persists findings to the `alerts` table so the GRANITE
Alerts tab (and later, Outreach routing) works from a durable hit list
instead of ephemeral widget renders.

v1 alert kinds:
  new_mf       - a multifamily entity appeared on the backbone that was
                 not there on the previous sweep (new construction,
                 conversion, or a data-coverage win - either way, look)
  units_jump   - an existing entity's unit count changed materially
                 (>= 10 units delta): renovation, expansion, or a feed
                 correction worth re-underwriting against

Dedup contract: one open alert per (kind, property_id); re-running a
sweep never duplicates. The sweep is pure SQL over workbench.db - no
network, no LLM (spec 11).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

UNITS_JUMP_MIN = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    property_id TEXT NOT NULL,
    city        TEXT,
    headline    TEXT NOT NULL,
    detail      TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    UNIQUE (kind, property_id)
);
CREATE TABLE IF NOT EXISTS alert_snapshot (
    property_id TEXT PRIMARY KEY,
    units       INTEGER,
    seen_at     TEXT NOT NULL
);
"""


def run_sweep(db_path: Path) -> dict[str, int]:
    """Compare the freshly built backbone against the last sweep's
    snapshot; write alerts for what changed; refresh the snapshot.
    Returns counts per alert kind (first-ever sweep seeds the snapshot
    silently - everything would be "new")."""
    from core.phase0 import is_mf_ten_plus
    now = dt.datetime.now().isoformat(timespec="seconds")
    counts = {"new_mf": 0, "units_jump": 0}
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.executescript(_SCHEMA)
        prior = dict(conn.execute(
            "SELECT property_id, units FROM alert_snapshot"))
        current: dict[str, tuple] = {}
        for pid, city, addr, uc, units in conn.execute(
                "SELECT property_id, city, address, use_code, units "
                "  FROM properties_8r"):
            if is_mf_ten_plus(uc, units):
                current[pid] = (city, addr, units)
        seeding = not prior
        for pid, (city, addr, units) in current.items():
            if seeding:
                continue
            if pid not in prior:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO alerts
                       (kind, property_id, city, headline, detail, created_at)
                       VALUES ('new_mf', ?, ?, ?, ?, ?)""",
                    (pid, city,
                     f"New multifamily on the backbone: {addr or pid}",
                     f"{units or '?'} units · {city}", now))
                counts["new_mf"] += max(cur.rowcount, 0)
            else:
                old_u = prior[pid]
                if (units and old_u
                        and abs(int(units) - int(old_u)) >= UNITS_JUMP_MIN):
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO alerts
                           (kind, property_id, city, headline, detail,
                            created_at)
                           VALUES ('units_jump', ?, ?, ?, ?, ?)""",
                        (pid, city,
                         f"Unit count moved: {addr or pid}",
                         f"{old_u} -> {units} units · {city}", now))
                    counts["units_jump"] += max(cur.rowcount, 0)
        conn.execute("DELETE FROM alert_snapshot")
        conn.executemany(
            "INSERT INTO alert_snapshot VALUES (?,?,?)",
            [(pid, u, now) for pid, (_, _, u) in current.items()])
        conn.commit()
    return counts


def open_alerts(db_path: Path, limit: int = 200) -> list[dict]:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, kind, property_id, city, headline, detail,
                          created_at FROM alerts
                    WHERE status = 'open'
                    ORDER BY created_at DESC, id DESC LIMIT ?""",
                (limit,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def dismiss(db_path: Path, alert_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE alerts SET status = 'dismissed' WHERE id = ?",
                     (alert_id,))
        conn.commit()
