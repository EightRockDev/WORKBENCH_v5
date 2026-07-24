"""Seed a small set of realistic DEMO multifamily properties for validation.

Purpose: until the Phase 0 "8R spine" (self-sourced assessor/deed/listing data)
is built, a fresh v5.0 deployment has an empty inventory and nothing to click.
This script loads a dozen realistic Hampton Roads properties so the whole engine
(browse -> select -> Subject -> Underwriting -> Returns -> Comps -> Exec Summary)
can be exercised end to end.

These rows are DEMO DATA, not real and NOT ALN-sourced:
  * property_id uses the 8R-DEMO- prefix; aln_id is NULL; source_file='DEMO-SEED'.
  * They are safe to delete wholesale and will be superseded by the real 8R
    spine in Phase 0.

Run:
    uv run python scripts/seed_demo_properties.py           # insert/refresh demo rows
    uv run python scripts/seed_demo_properties.py --clear   # remove demo rows only
"""

from __future__ import annotations

import pathlib
import sys

# Allow running as a plain script (python scripts/seed_demo_properties.py):
# put the repo root on sys.path so `data` imports resolve.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import db  # noqa: E402

SOURCE_TAG = "DEMO-SEED"

# Clustered around the Hampton Roads core so the comp finder (Haversine radius,
# same asset class) returns neighbors. A mix of Class B/C, 12-84 units.
_DEMO: list[dict] = [
    dict(property_id="8R-DEMO-001", name="Crossroads Townhomes", address="1200 Ballentine Blvd",
         city="Norfolk", county="Norfolk City", zip="23504", units=26, year_built=1974,
         occupancy_pct=0.92, avg_sqft=880, avg_rent=1195, asset_class="C",
         property_type="Townhome", latitude=36.8620, longitude=-76.2650,
         owner="Cleghorn Capital LLC", management_company="Drucker + Falk"),
    dict(property_id="8R-DEMO-002", name="Ghent Court Apartments", address="900 Colonial Ave",
         city="Norfolk", county="Norfolk City", zip="23507", units=48, year_built=1968,
         occupancy_pct=0.94, avg_sqft=760, avg_rent=1320, asset_class="B",
         property_type="Garden", latitude=36.8720, longitude=-76.2980,
         owner="Ghent Holdings LLC", management_company="Harbor Group"),
    dict(property_id="8R-DEMO-003", name="Wards Corner Flats", address="7500 Granby St",
         city="Norfolk", county="Norfolk City", zip="23505", units=64, year_built=1985,
         occupancy_pct=0.90, avg_sqft=820, avg_rent=1280, asset_class="B",
         property_type="Garden", latitude=36.9020, longitude=-76.2870,
         owner="Granby Street Partners LLC", management_company="Lawson"),
    dict(property_id="8R-DEMO-004", name="Berkley Commons", address="300 E Berkley Ave",
         city="Norfolk", county="Norfolk City", zip="23523", units=18, year_built=1962,
         occupancy_pct=0.88, avg_sqft=720, avg_rent=995, asset_class="C",
         property_type="Garden", latitude=36.8280, longitude=-76.2740,
         owner="Berkley Rentals LLC", management_company="Self-managed"),
    dict(property_id="8R-DEMO-005", name="Town Center Lofts", address="200 Central Park Ave",
         city="Virginia Beach", county="Virginia Beach City", zip="23462", units=84, year_built=2006,
         occupancy_pct=0.96, avg_sqft=910, avg_rent=1650, asset_class="A",
         property_type="Mid-Rise", latitude=36.8410, longitude=-76.1350,
         owner="Central Park VB LLC", management_company="Bonaventure"),
    dict(property_id="8R-DEMO-006", name="Kempsville Garden", address="5100 Providence Rd",
         city="Virginia Beach", county="Virginia Beach City", zip="23464", units=52, year_built=1979,
         occupancy_pct=0.93, avg_sqft=840, avg_rent=1385, asset_class="B",
         property_type="Garden", latitude=36.8210, longitude=-76.1780,
         owner="Kempsville Investors LLC", management_company="Drucker + Falk"),
    dict(property_id="8R-DEMO-007", name="Greenbrier Pointe", address="1400 Eden Way N",
         city="Chesapeake", county="Chesapeake City", zip="23320", units=72, year_built=1998,
         occupancy_pct=0.95, avg_sqft=930, avg_rent=1495, asset_class="B",
         property_type="Garden", latitude=36.7680, longitude=-76.2320,
         owner="Greenbrier MF LLC", management_company="S.L. Nusbaum"),
    dict(property_id="8R-DEMO-008", name="South Norfolk Court", address="1100 Poindexter St",
         city="Chesapeake", county="Chesapeake City", zip="23324", units=22, year_built=1965,
         occupancy_pct=0.89, avg_sqft=700, avg_rent=1050, asset_class="C",
         property_type="Garden", latitude=36.8000, longitude=-76.2870,
         owner="Poindexter Holdings LLC", management_company="Self-managed"),
    dict(property_id="8R-DEMO-009", name="Olde Towne Portsmouth", address="500 London St",
         city="Portsmouth", county="Portsmouth City", zip="23704", units=34, year_built=1971,
         occupancy_pct=0.91, avg_sqft=780, avg_rent=1125, asset_class="C",
         property_type="Garden", latitude=36.8360, longitude=-76.2980,
         owner="London Street LLC", management_company="Lawson"),
    dict(property_id="8R-DEMO-010", name="Phoebus Square", address="20 E Mellen St",
         city="Hampton", county="Hampton City", zip="23663", units=40, year_built=1983,
         occupancy_pct=0.92, avg_sqft=800, avg_rent=1210, asset_class="B",
         property_type="Garden", latitude=37.0190, longitude=-76.3300,
         owner="Phoebus Partners LLC", management_company="Abbitt"),
    dict(property_id="8R-DEMO-011", name="Hilton Village Apartments", address="10400 Warwick Blvd",
         city="Newport News", county="Newport News City", zip="23601", units=58, year_built=1990,
         occupancy_pct=0.94, avg_sqft=860, avg_rent=1295, asset_class="B",
         property_type="Garden", latitude=37.0560, longitude=-76.4680,
         owner="Warwick MF Holdings LLC", management_company="Drucker + Falk"),
    dict(property_id="8R-DEMO-012", name="Downtown Suffolk Lofts", address="150 N Main St",
         city="Suffolk", county="Suffolk City", zip="23434", units=16, year_built=1959,
         occupancy_pct=0.87, avg_sqft=740, avg_rent=1075, asset_class="C",
         property_type="Mid-Rise", latitude=36.7290, longitude=-76.5830,
         owner="Main Street Suffolk LLC", management_company="Self-managed"),
]

_COMMON = dict(
    state="VA", asset_type="Multifamily", property_segment="Conventional",
    market="Hampton Roads", market_description="Hampton Roads MSA (Virginia Beach-Norfolk-Newport News)",
    asset_or_fee="Asset", status="Active", source_file=SOURCE_TAG,
)


def _rows() -> list[dict]:
    out = []
    for d in _DEMO:
        r = dict(_COMMON)
        r.update(d)
        r["submarket"] = r["city"]
        r["rent_per_sqft"] = round(r["avg_rent"] / r["avg_sqft"], 2)
        r["aln_pull_date"] = None
        out.append(r)
    return out


def clear() -> int:
    db.ensure_db_synced()
    with db.get_connection() as conn:
        cur = conn.execute("DELETE FROM properties WHERE source_file = ?", (SOURCE_TAG,))
        conn.commit()
        return cur.rowcount


def seed() -> int:
    db.ensure_db_synced()  # creates empty schema-only DB if needed
    rows = _rows()
    cols = sorted({k for r in rows for k in r})
    placeholders = ", ".join(":" + c for c in cols)
    sql = f"INSERT OR REPLACE INTO properties ({', '.join(cols)}) VALUES ({placeholders})"
    with db.get_connection() as conn:
        for r in rows:
            conn.execute(sql, {c: r.get(c) for c in cols})
        conn.commit()
    return len(rows)


if __name__ == "__main__":
    if "--clear" in sys.argv:
        n = clear()
        print(f"Removed {n} demo properties (source_file={SOURCE_TAG}).")
    else:
        n = seed()
        total = len(db.list_properties(limit=10000))
        print(f"Seeded {n} demo properties. Inventory now has {total} rows.")
        print("Launch:  uv run python -m streamlit run app.py")
