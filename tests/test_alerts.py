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
    assert alerts.run_sweep(db) == {"new_mf": 0, "units_jump": 0,
                                    "owner_change": 0, "stale_closed": 0}  # seed
    # Next build: a new complex appears and an existing one grows.
    _spine(db, [("8R-a", "1 Main St", 80), ("8R-b", "2 Oak Ave", 32)])
    counts = alerts.run_sweep(db)
    assert counts == {"new_mf": 1, "units_jump": 1, "owner_change": 0,
                      "stale_closed": 0}
    kinds = {a["kind"]: a for a in alerts.open_alerts(db)}
    assert "2 Oak Ave" in kinds["new_mf"]["headline"]
    assert "50 -> 80" in kinds["units_jump"]["detail"]
    # Re-running the same sweep never duplicates.
    assert alerts.run_sweep(db) == {"new_mf": 0, "units_jump": 0,
                                    "owner_change": 0, "stale_closed": 0}
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


# ---------------------------------------------------------------------------
# The sweep report must not contradict itself (2026-08-01)
# ---------------------------------------------------------------------------

def test_open_count_is_the_full_total_not_the_page(tmp_path):
    """`open_alerts` truncates at its limit; the report needs the real total
    so it can say what it is leaving out."""
    import sqlite3
    from core import alerts

    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE alerts (
            id INTEGER PRIMARY KEY, kind TEXT, property_id TEXT, city TEXT,
            headline TEXT, detail TEXT, created_at TEXT,
            status TEXT DEFAULT 'open')""")
        conn.executemany(
            "INSERT INTO alerts (kind, property_id, city, headline, detail,"
            " created_at, status) VALUES (?,?,?,?,?,?,?)",
            [("new_mf", f"p{i}", "Virginia Beach", f"h{i}", "d", "t", "open")
             for i in range(40)]
            + [("units_jump", "px", "Norfolk", "h", "d", "t", "open")]
            + [("new_mf", "pz", "Norfolk", "h", "d", "t", "dismissed")])

    counts = alerts.count_open_alerts(db)
    assert counts["total"] == 41           # the dismissed one is excluded
    assert counts["new_mf"] == 40
    assert counts["units_jump"] == 1
    # the paged view is capped, which is exactly why the total is needed
    assert len(alerts.open_alerts(db, limit=25)) == 25


def test_open_count_on_a_database_with_no_alerts_table(tmp_path):
    import sqlite3
    from core import alerts

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    assert alerts.count_open_alerts(db) == {"total": 0}


def test_stale_alert_closes_when_property_leaves_mf(tmp_path):
    """A corrected misclassification (Richmond 'R-4 Single Family' flood)
    must drain the alert list: when the property no longer qualifies as
    multifamily on a later build, its open alerts close as 'stale'.
    Dismissed alerts keep their own status."""
    import sqlite3
    db = _spine(tmp_path / "wb.db", [("8R-a", "1 Main St", 50)])
    alerts.run_sweep(db)                                        # seed
    _spine(db, [("8R-a", "1 Main St", 50), ("8R-b", "2 Oak Ave", 32)])
    assert alerts.run_sweep(db)["new_mf"] == 1
    # The 'new' property turns out to be single-family (reclassified).
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE properties_8r SET use_code='R-4 Single Family',"
                     " units=NULL WHERE property_id='8R-b'")
    counts = alerts.run_sweep(db)
    assert counts["stale_closed"] == 1
    assert alerts.open_alerts(db) == []
    with sqlite3.connect(db) as conn:
        status = conn.execute("SELECT status FROM alerts WHERE "
                              "property_id='8R-b'").fetchone()[0]
    assert status == "stale"
