"""One multifamily rule for every count (2026-10-05).

Virginia Beach's parcel feeds reached the backbone on 10-04 and the
headline jumped 11,333 -> 29,589: 16,064 VB parcels labeled "Multi
Family" with no unit count (duplexes, per the comp-pool rule's own
note). The comp pool already presumed such rows small in any city whose
feed proves it carries unit counts; the headline, screener metrics, rent
stamping, alerts and preflight did not.
"""
from __future__ import annotations

import sqlite3

from core import phase0


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE properties_8r (property_id TEXT, city TEXT, "
                 "use_code TEXT, units REAL)")
    conn.executemany("INSERT INTO properties_8r VALUES (?,?,?,?)", rows)
    return conn


def _rows():
    rows = []
    # A unit-rich city: 60 parcels with real counts, 500 label-only.
    rows += [(f"vb{i}", "Virginia Beach", "Multi Family", 40)
             for i in range(60)]
    rows += [(f"vbl{i}", "Virginia Beach", "Multi Family", None)
             for i in range(500)]
    # A city that publishes no counts at all: the label is the evidence.
    rows += [(f"nf{i}", "Norfolk", "Apartment", None) for i in range(30)]
    return rows


def test_unit_rich_cities_needs_the_threshold():
    conn = _db(_rows() + [("nn1", "Newport News", "x", 12)])
    rich = phase0.unit_rich_cities(conn)
    assert rich == {"Virginia Beach"}       # 60 >= 50; Newport News has 1


def test_label_only_rows_do_not_count_where_units_exist():
    conn = _db(_rows())
    rich = phase0.unit_rich_cities(conn)
    counted = sum(
        phase0.is_mf_ten_plus_for_city(c, uc, u, None, rich)
        for c, uc, u in conn.execute(
            "SELECT city, use_code, units FROM properties_8r"))
    assert counted == 60 + 30               # VB verified + all of Norfolk


def test_a_known_count_always_wins():
    rich = {"Virginia Beach"}
    assert phase0.is_mf_ten_plus_for_city(
        "Virginia Beach", "Multi Family", 12, None, rich)
    assert not phase0.is_mf_ten_plus_for_city(
        "Virginia Beach", "Multi Family", 4, None, rich)


def test_callers_without_the_set_keep_the_old_behaviour():
    assert phase0.is_mf_ten_plus_for_city(
        "Virginia Beach", "Multi Family", None)


def test_comp_pool_and_headline_share_one_threshold():
    from core import phase0_parity
    assert phase0_parity.UNIT_RICH_MIN is phase0.UNIT_RICH_MIN


def test_screener_metrics_applies_the_rule(tmp_path):
    from core import screener_metrics
    db = tmp_path / "workbench.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE properties_8r (property_id TEXT, city TEXT, "
                 "r8_market TEXT, r8_submarket TEXT, use_code TEXT, "
                 "units REAL)")
    conn.executemany(
        "INSERT INTO properties_8r VALUES (?,?,?,?,?,?)",
        [(p, c, None, None, uc, u) for p, c, uc, u in _rows()])
    conn.commit()
    conn.close()
    out = screener_metrics.market_breakdown(db)
    assert out.total == 90, out
