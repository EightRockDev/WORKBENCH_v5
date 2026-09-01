"""List the properties that came from the deal sweep. Read-only."""
from __future__ import annotations
import os, sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("EIGHT_ROCK_DB_PATH", "data/workbench.db"))
if not DB.is_absolute():
    DB = HERE / DB

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

total = con.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
rows = list(con.execute("""
    SELECT name, city, state, units, occupancy_pct, avg_rent, avg_sqft,
           year_built, property_id, source_file
    FROM properties
    WHERE tags LIKE '%8RWB%' OR source_file = '8RWB' OR property_id LIKE '8RWB-%'
    ORDER BY units DESC NULLS LAST, name"""))

added = [r for r in rows if str(r["property_id"]).startswith("8RWB-")]
filled = [r for r in rows if not str(r["property_id"]).startswith("8RWB-")]

print(f"\n  Properties in the Workbench : {total:,}")
print(f"  From the deal sweep         : {len(rows)}")
print(f"     added as new properties  : {len(added)}")
print(f"     existing ones filled in  : {len(filled)}\n")

def table(title, items):
    if not items:
        return
    print(f"  {title} ({len(items)})")
    print(f"    {'PROPERTY':<44} {'CITY':<16} {'ST':<3} {'UNITS':>6} "
          f"{'OCC':>7} {'AVG RENT':>10} {'SQFT':>7} {'BUILT':>6}")
    for r in items:
        occ = r["occupancy_pct"]
        rent, sqft = r["avg_rent"], r["avg_sqft"]
        print(f"    {str(r['name'])[:44]:<44} {str(r['city'] or '')[:16]:<16} "
              f"{str(r['state'] or ''):<3} {r['units'] or '-':>6} "
              f"{(f'{occ*100:.1f}%' if occ else '-'):>7} "
              f"{(f'${rent:,.0f}' if rent else '-'):>10} "
              f"{(f'{sqft:,.0f}' if sqft else '-'):>7} "
              f"{r['year_built'] or '-':>6}")
    print()

table("ADDED AS NEW PROPERTIES", added)
table("EXISTING PROPERTIES FILLED IN", filled)

units = sum(r["units"] or 0 for r in rows)
print(f"  {units:,} units across {len(rows)} deal properties.\n")
con.close()
