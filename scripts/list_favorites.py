"""List Brian's favorited properties + their listing-URL config status.

Output is a printable table he can use to populate
``Properties/_favorite_listings.json``. Shows which favorites already have
URLs for which sources, and which still need to be filled in.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

WB_ROOT = Path(__file__).resolve().parent.parent.parent
FAV_PATH = WB_ROOT / "Properties" / "_favorites.json"
URL_PATH = WB_ROOT / "Properties" / "_favorite_listings.json"
DB_PATH = WB_ROOT / "python_workbench" / "data" / "workbench.db"


def main() -> int:
    if not FAV_PATH.is_file():
        print(f"No favorites file at {FAV_PATH}")
        return 1
    try:
        fav_ids = json.loads(FAV_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Couldn't read favorites: {e}")
        return 1
    if not isinstance(fav_ids, list):
        print(f"Favorites file is not a JSON list: {FAV_PATH}")
        return 1

    # Load existing URL config
    urls_config: dict[str, dict] = {}
    if URL_PATH.is_file():
        try:
            urls_config = json.loads(URL_PATH.read_text(encoding="utf-8"))
        except Exception:
            urls_config = {}
    # Strip _comment / _example keys
    urls_config = {k: v for k, v in urls_config.items() if not k.startswith("_")}

    # Look up each favorite in the ALN properties table
    if not DB_PATH.is_file():
        print(f"workbench.db not found at {DB_PATH}")
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(fav_ids))
        rows = conn.execute(
            f"SELECT property_id, aln_id, name, address, city, units, year_built "
            f"FROM properties "
            f"WHERE property_id IN ({placeholders}) OR aln_id IN ({placeholders}) "
            f"ORDER BY name",
            tuple(str(x) for x in fav_ids) * 2,
        ).fetchall()

    if not rows:
        print(f"None of the {len(fav_ids)} favorites matched properties in the DB.")
        return 1

    print(f"\n{'=' * 100}")
    print(f"Eight Rock Favorites — {len(rows)} matched of {len(fav_ids)} favorited")
    print(f"{'=' * 100}")
    print(f"{'#':<3} {'ID':<14} {'Name':<35} {'City':<16} {'Units':<6} {'Built':<6} {'URLs configured'}")
    print(f"{'-' * 3} {'-' * 14} {'-' * 35} {'-' * 16} {'-' * 6} {'-' * 6} {'-' * 30}")

    for i, r in enumerate(rows, start=1):
        key = r["property_id"] or r["aln_id"]
        configured = urls_config.get(str(key), {}) or urls_config.get(str(r["aln_id"] or ""), {})
        sources_set = [s for s, u in configured.items() if u and not s.startswith("_")]
        sources_str = ", ".join(sources_set) if sources_set else "(none — needs setup)"
        print(
            f"{i:<3} {str(key)[:13]:<14} {(r['name'] or '')[:34]:<35} "
            f"{(r['city'] or '')[:15]:<16} "
            f"{r['units'] or '':<6} {r['year_built'] or '':<6} {sources_str}"
        )

    print()
    print(f"Edit {URL_PATH}")
    print("Add an entry per favorite using the property_id or aln_id from column 2.")
    print("Example structure (one source minimum, multiple sources welcome):")
    print('  "133760": {"apartments_com": "https://...", "rentcafe": "https://..."}')
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
