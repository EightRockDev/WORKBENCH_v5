"""One-shot LEI → lender-name resolver.

Walks every NULL `lender_name` in `hmda_lender_summary`, looks up the LEI via
the GLEIF public API (no auth, ~3s per call), and UPDATEs the row in place.
Runs in a few minutes; idempotent — re-running only resolves still-NULL rows.

Usage:
    python resolve_leis.py
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import requests

DB = Path(__file__).resolve().parent / "hampton_roads.db"
GLEIF = "https://api.gleif.org/api/v1/lei-records/"
HEADERS = {"Accept": "application/vnd.api+json"}


def resolve(lei: str) -> str | None:
    try:
        r = requests.get(GLEIF + lei, timeout=15, headers=HEADERS)
        if r.status_code != 200:
            return None
        return (
            r.json()
            .get("data", {})
            .get("attributes", {})
            .get("entity", {})
            .get("legalName", {})
            .get("name")
        )
    except (requests.RequestException, ValueError, KeyError):
        return None


def main() -> int:
    db = sqlite3.connect(DB)
    cur = db.execute(
        "SELECT DISTINCT lei FROM hmda_lender_summary "
        "WHERE (lender_name IS NULL OR lender_name = '') AND lei IS NOT NULL "
        "ORDER BY lei"
    )
    unresolved = [r[0] for r in cur.fetchall() if r[0]]
    n = len(unresolved)
    print(f"Resolving {n} LEIs via GLEIF…")

    resolved_count = 0
    failed_count = 0
    for i, lei in enumerate(unresolved, 1):
        name = resolve(lei)
        if name:
            db.execute(
                "UPDATE hmda_lender_summary SET lender_name = ? WHERE lei = ?",
                (name, lei),
            )
            resolved_count += 1
            mark = "OK"
        else:
            failed_count += 1
            mark = "MISS"
        if i % 5 == 0 or i == n:
            print(f"  [{i}/{n}] {mark}  {lei[:12]}…  {name or '(no match)'}")
        # Modest rate-limiting to stay under GLEIF's free-tier soft cap
        time.sleep(0.2)

    db.commit()
    db.close()
    print(f"\nDone. Resolved {resolved_count}, missed {failed_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
