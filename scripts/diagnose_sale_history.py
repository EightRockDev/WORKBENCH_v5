"""Sale-history diagnostic — run ON THE SERVER, where the real data lives.

Owner report (2026-08-05): "I don't see sale history" on any property. The
V5.24.9 fix corrected the function-name bug, but the card can still read empty
for a real data reason. This script says WHICH reason, per property, without a
manual DB dig:

  1. Does the folder have a curated `sales.json`? (primary source)
  2. Does `sources.json` carry an `assessmentHistory` block? (FY assessed
     values — what the va_assessors ETL actually provides; NOT deed/transfer)
  3. Does `muni_records` (workbench.db) hold assessor rows for this locality,
     and do ANY of them carry sale price/date fields? (the fallback source)

Usage (on the box, from C:\\WORKBENCH_V5):
    uv run python scripts/diagnose_sale_history.py                # scans all
    uv run python scripts/diagnose_sale_history.py "East Beach"   # one folder

Read-only. Nothing is written.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import phase0                                   # noqa: E402
from core import sale_history as sh                       # noqa: E402
from data.property_io import (                            # noqa: E402
    PROPERTIES_ROOT,
    discover_property_folders,
    load_sales,
    load_sources,
)


def _muni_stats(city: str) -> None:
    path = phase0.find_workbench_db()
    print("\n=== muni_records (the sale_history fallback source) ===")
    print(f"workbench.db: {path}")
    if not path or not pathlib.Path(path).exists():
        print("  ✗ workbench.db not found — the fallback can never return data.")
        return
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        if "muni_records" not in tabs:
            print("  ✗ no muni_records table — assessor feed never landed here.")
            return
        total = conn.execute("SELECT count(*) FROM muni_records").fetchone()[0]
        assr = conn.execute(
            "SELECT count(*) FROM muni_records WHERE kind LIKE 'assessor%'"
        ).fetchone()[0]
        print(f"  rows: {total} total, {assr} assessor")
        markets = conn.execute(
            "SELECT market, count(*) c FROM muni_records "
            "WHERE kind LIKE 'assessor%' GROUP BY market ORDER BY c DESC LIMIT 12"
        ).fetchall()
        print("  assessor markets:", [(m["market"], m["c"]) for m in markets])
        # Sample a locality row and report whether it carries ANY sale keys.
        rows = conn.execute(
            "SELECT record FROM muni_records WHERE kind LIKE 'assessor%' "
            "AND (market = ? OR market = ?) LIMIT 200", (city, city)).fetchall()
        print(f"  assessor rows for '{city}': {len(rows)} (sampled up to 200)")
        with_sale = 0
        sample_keys: list[str] = []
        for r in rows:
            try:
                raw = json.loads(r["record"]) if r["record"] else {}
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            if not sample_keys:
                sample_keys = sorted(raw.keys())
            recs = sh.extract_sale_records(raw)
            if recs and any(x.get("price") or x.get("date") for x in recs):
                with_sale += 1
        print(f"  ...of those, {with_sale} carry a sale price/date")
        if sample_keys:
            print(f"  sample assessor record keys ({len(sample_keys)}): "
                  f"{sample_keys}")
        if with_sale == 0:
            print("  → CONCLUSION: this locality's assessor feed has assessment "
                  "values but NO transfer/sale fields. Sale history needs a "
                  "deed/clerk source, which we don't currently ingest.")
    finally:
        conn.close()


def _folder_report(name_filter: str | None) -> str:
    print("=== property folders (primary + assessment sources) ===")
    print(f"Properties root: {PROPERTIES_ROOT}")
    folders = discover_property_folders(PROPERTIES_ROOT)
    city_seen = ""
    for f in folders:
        if name_filter and name_filter.lower() not in f.folder_name.lower():
            continue
        sales = load_sales(f.path)
        sources = load_sources(f.path) or {}
        ah = sources.get("assessmentHistory") or {}
        ah_recs = ah.get("records") or []
        n_sales = 0
        if isinstance(sales, dict):
            n_sales = len(sales.get("last_3_apartment_sales")
                          or sales.get("sales") or [])
        elif isinstance(sales, list):
            n_sales = len(sales)
        print(f"\n• {f.folder_name}")
        print(f"    sales.json           : {'yes' if sales else 'MISSING'}"
              f" ({n_sales} records)")
        print(f"    assessmentHistory    : "
              f"{'yes' if ah_recs else 'none'} ({len(ah_recs)} FY records)")
        # Remember a city to scope the muni check (first match wins).
        if not city_seen:
            parts = f.folder_name.rsplit("-", 1)
            city_seen = parts[-1] if len(parts) == 2 else ""
    return city_seen


def main() -> None:
    name_filter = sys.argv[1] if len(sys.argv) > 1 else None
    city = _folder_report(name_filter)
    # City for the muni scope: prefer the folder suffix; default Norfolk.
    _muni_stats(city or "Norfolk")
    print("\nDone. Paste this whole output back to Claude to pinpoint the fix.")


if __name__ == "__main__":
    main()
