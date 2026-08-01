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
Ownership changes are RECORDED, not alerted (owner ruling
2026-07-30): a fresh trade is a poor outreach target, but the deed-chain
history feeds the radar's tenure score - `ownership_changes` keeps every
observed transition for later pulls.

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
CREATE TABLE IF NOT EXISTS ownership_changes (
    id          INTEGER PRIMARY KEY,
    property_id TEXT NOT NULL,
    old_owner   TEXT,
    new_owner   TEXT,
    city        TEXT,
    units       INTEGER,
    observed_at TEXT NOT NULL,
    UNIQUE (property_id, old_owner, new_owner)
);
CREATE TABLE IF NOT EXISTS alert_snapshot (
    property_id TEXT PRIMARY KEY,
    units       INTEGER,
    owner_name  TEXT,
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
    counts = {"new_mf": 0, "units_jump": 0, "owner_change": 0}
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.executescript(_SCHEMA)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(alert_snapshot)")}
        if "owner_name" not in cols:   # pre-owner-change snapshots migrate
            conn.execute(
                "ALTER TABLE alert_snapshot ADD COLUMN owner_name TEXT")
        prior = {pid: (u, o) for pid, u, o in conn.execute(
            "SELECT property_id, units, owner_name FROM alert_snapshot")}
        current: dict[str, tuple] = {}
        for pid, city, addr, uc, units, owner in conn.execute(
                "SELECT property_id, city, address, use_code, units, "
                "       owner_name FROM properties_8r"):
            if is_mf_ten_plus(uc, units):
                current[pid] = (city, addr, units, owner)
        seeding = not prior
        for pid, (city, addr, units, owner) in current.items():
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
                old_u, old_owner = prior[pid]
                if (owner and old_owner
                        and owner.strip().upper()
                        != old_owner.strip().upper()):
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO ownership_changes
                           (property_id, old_owner, new_owner, city, units,
                            observed_at) VALUES (?,?,?,?,?,?)""",
                        (pid, old_owner, owner, city, units, now))
                    counts["owner_change"] += max(cur.rowcount, 0)
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
            "INSERT INTO alert_snapshot VALUES (?,?,?,?)",
            [(pid, u, o, now)
             for pid, (_, _, u, o) in current.items()])
        conn.commit()
    return counts


def count_open_alerts(db_path: Path) -> dict[str, int]:
    """{kind: n} plus 'total', across ALL open alerts.

    `open_alerts` truncates, so a caller that prints its result cannot say how
    many it left out. Reporting "25 shown" beside a real total is the
    difference between a summary and a silent cap.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT kind, count(*) FROM alerts WHERE status='open' "
                "GROUP BY kind").fetchall()
    except sqlite3.Error:
        return {"total": 0}
    out = {str(k): int(n) for k, n in rows}
    out["total"] = sum(out.values())
    return out


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


def queue_for_outreach(db_path: Path, alert_id: int) -> None:
    """Route a sweep alert into the outreach dial queue (spec 6.1:
    'alert routing to the Outreach Engine'). Idempotent per alert."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS outreach_queue (
            alert_id    INTEGER PRIMARY KEY,
            property_id TEXT NOT NULL,
            headline    TEXT,
            detail      TEXT,
            city        TEXT,
            status      TEXT NOT NULL DEFAULT 'queued',
            queued_at   TEXT NOT NULL)""")
        row = conn.execute(
            "SELECT property_id, headline, detail, city FROM alerts "
            " WHERE id = ?", (alert_id,)).fetchone()
        if row:
            conn.execute(
                """INSERT OR IGNORE INTO outreach_queue
                   (alert_id, property_id, headline, detail, city, queued_at)
                   VALUES (?,?,?,?,?,?)""",
                (alert_id, *row, dt.datetime.now().isoformat(
                    timespec="seconds")))
            conn.execute(
                "UPDATE alerts SET status = 'routed' WHERE id = ?",
                (alert_id,))
        conn.commit()


def outreach_queue(db_path: Path, limit: int = 100) -> list[dict]:
    """The dial list: queued sweep targets, oldest first (work the
    backlog down)."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT alert_id, property_id, headline, detail, city,
                          queued_at FROM outreach_queue
                    WHERE status = 'queued'
                    ORDER BY queued_at ASC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def mark_worked(db_path: Path, alert_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE outreach_queue SET status = 'worked' WHERE alert_id = ?",
            (alert_id,))
        conn.commit()


def dismiss(db_path: Path, alert_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE alerts SET status = 'dismissed' WHERE id = ?",
                     (alert_id,))
        conn.commit()
