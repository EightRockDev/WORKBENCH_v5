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
    assert alerts.run_sweep(db) == {"new_mf": 0, "units_jump": 0, "owner_change": 0}  # seed
    # Next build: a new complex appears and an existing one grows.
    _spine(db, [("8R-a", "1 Main St", 80), ("8R-b", "2 Oak Ave", 32)])
    counts = alerts.run_sweep(db)
    assert counts == {"new_mf": 1, "units_jump": 1, "owner_change": 0}
    kinds = {a["kind"]: a for a in alerts.open_alerts(db)}
    assert "2 Oak Ave" in kinds["new_mf"]["headline"]
    assert "50 -> 80" in kinds["units_jump"]["detail"]
    # Re-running the same sweep never duplicates.
    assert alerts.run_sweep(db) == {"new_mf": 0, "units_jump": 0, "owner_change": 0}
    assert len(alerts.open_alerts(db)) == 2


def test_dismiss_hides_an_alert(tmp_path):
    db = _spine(tmp_path / "wb.db", [("8R-a", "1 Main St", 50)])
    alerts.run_sweep(db)
    _spine(db, [("8R-a", "1 Main St", 50), ("8R-b", "2 Oak Ave", 32)])
    alerts.run_sweep(db)
    a = alerts.open_alerts(db)[0]
    alerts.dismiss(db, a["id"])
    assert alerts.open_alerts(db) == []


def test_owner_change_fires_the_traded_alert(tmp_path):
    """An owner-name flip on the assessor roll = the property traded -
    recorded silently to ownership_changes (deed-chain history for
    the radar tenure score) - never an alert. Case noise is not a trade."""
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(_SPINE_SCHEMA)
        conn.execute(
            """INSERT INTO properties_8r
               (property_id, fips, address, city, units, use_code,
                owner_name, built_at)
               VALUES ('8R-a','51710','1 Main St','Norfolk',50,
                       'APARTMENT','OLD OWNER LLC','t')""")
    alerts.run_sweep(db)                               # seed
    with sqlite3.connect(db) as conn:                  # same owner, recased
        conn.execute("UPDATE properties_8r SET owner_name='Old Owner llc '")
    assert alerts.run_sweep(db)["owner_change"] == 0
    with sqlite3.connect(db) as conn:                  # real trade
        conn.execute("UPDATE properties_8r SET owner_name='NEW CAPITAL LP'")
    counts = alerts.run_sweep(db)
    assert counts["owner_change"] == 1
    # Recorded, NOT alerted (owner ruling): history table only.
    assert not [x for x in alerts.open_alerts(db)
                if x["kind"] == "owner_change"]
    row = sqlite3.connect(db).execute(
        "SELECT old_owner, new_owner FROM ownership_changes").fetchone()
    assert row == ("Old Owner llc ", "NEW CAPITAL LP")


def test_alert_routes_to_outreach_queue(tmp_path):
    """Spec 6.1: alert routing to the Outreach Engine. Routing moves the
    alert out of the open list and into the dial queue; working it
    clears the queue; both idempotent."""
    db = _spine(tmp_path / "wb.db", [("8R-a", "1 Main St", 50)])
    alerts.run_sweep(db)
    _spine(db, [("8R-a", "1 Main St", 50), ("8R-b", "2 Oak Ave", 32)])
    alerts.run_sweep(db)
    a = alerts.open_alerts(db)[0]
    alerts.queue_for_outreach(db, a["id"])
    alerts.queue_for_outreach(db, a["id"])          # idempotent
    assert alerts.open_alerts(db) == []             # routed, not open
    q = alerts.outreach_queue(db)
    assert len(q) == 1 and q[0]["property_id"] == "8R-b"
    alerts.mark_worked(db, a["id"])
    assert alerts.outreach_queue(db) == []
