"""Quick inspect of the rent_listings table — shows counts + recent rows."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "hampton-roads-etl" / "hampton_roads.db"

with sqlite3.connect(DB) as c:
    print("\nStatus counts:")
    for r in c.execute("SELECT scrape_status, COUNT(*) FROM rent_listings GROUP BY scrape_status ORDER BY 2 DESC"):
        print(f"  {r[0]:<15} {r[1]}")

    print("\nBy source:")
    for r in c.execute("SELECT source, scrape_status, COUNT(*) FROM rent_listings GROUP BY source, scrape_status ORDER BY source, scrape_status"):
        print(f"  {r[0]:<18} {r[1]:<15} {r[2]}")

    print("\nFirst 10 rows with ANY data (success/blocked/error):")
    rows = c.execute(
        "SELECT prop_name, source, scrape_status, one_br_rent_low, "
        "effective_one_br_rent, concession_text "
        "FROM rent_listings "
        "WHERE scrape_status != 'not_found' "
        "ORDER BY scrape_status LIMIT 10"
    ).fetchall()
    if not rows:
        print("  (none yet — add URLs to Properties/_favorite_listings.json)")
    else:
        for r in rows:
            print(f"  {r}")
