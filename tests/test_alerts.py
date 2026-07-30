"""Alert sweep (spec 6.1): durable, deduped, seeds silently."""

from __future__ import annotations

import sqlite3

from core import alerts
from core.phase0 import _SPINE_SCHEMA


def _spine(path, rows):
    with sqlite3.connect(path) as conn:
        conn.executescript(_SPINE_SCHEMA)
        conn.executemany(
            """INSERT OR REPLACE INTO properties_8r
               (property_id, fips, address, city, units, use_code, built_at)
               VALUES (?,?,?,?,?,?,?)""",
            [(pid, "51710", addr, "Norfolk", units, "APARTMENT", "t")
             for pid, addr, units in rows])
    return path


def test_first_sweep_seeds_silently_then_detects_changes(tmp_path):
    db = _spine(tmp_path / "wb.db", [("8R-a", "1 Main St", 50)])
    assert alerts.run_sweep(db) == {"new_mf": 0, "units_jump": 0}  # seed
    # Next build: a new complex appears and an existing one grows.
    _spine(db, [("8R-a", "1 Main St", 80), ("8R-b", "2 Oak Ave", 32)])
    counts = alerts.run_sweep(db)
    assert counts == {"new_mf": 1, "units_jump": 1}
    kinds = {a["kind"]: a for a in alerts.open_alerts(db)}
    assert "2 Oak Ave" in kinds["new_mf"]["headline"]
    assert "50 -> 80" in kinds["units_jump"]["detail"]
    # Re-running the same sweep never duplicates.
    assert alerts.run_sweep(db) == {"new_mf": 0, "units_jump": 0}
    assert len(alerts.open_alerts(db)) == 2


def test_dismiss_hides_an_alert(tmp_path):
    db = _spine(tmp_path / "wb.db", [("8R-a", "1 Main St", 50)])
    alerts.run_sweep(db)
    _spine(db, [("8R-a", "1 Main St", 50), ("8R-b", "2 Oak Ave", 32)])
    alerts.run_sweep(db)
    a = alerts.open_alerts(db)[0]
    alerts.dismiss(db, a["id"])
    assert alerts.open_alerts(db) == []
