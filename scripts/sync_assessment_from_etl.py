"""Sync `sources.json -> assessmentHistory` blocks from `hampton_roads.db`.

Reads the `va_multifamily_inventory` + `va_assessment_history` tables
populated by the va_assessors ETL puller, matches each property folder to
its parcel by GPIN, and writes the fresh FY-by-FY data into the structured
block.

Matching strategy (in order):
  1. GPIN match — if the folder already has `assessmentHistory.gpin` set
     (true for the 6 migrated folders), look up that GPIN in the ETL.
  2. Parcel ID match — fallback when the structured block has parcel_id
     but no GPIN.
  3. Address match — fuzzy match on street number + name. Disabled by
     default (false-positive risk); re-enable with --address-fallback.

Idempotent: only writes when the ETL data has more (or different) records
than what's currently in the folder. Preserves all other keys in
sources.json.

Usage:
    python scripts/sync_assessment_from_etl.py             # dry run
    python scripts/sync_assessment_from_etl.py --apply     # write changes
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ETL_DB = (
    Path(__file__).resolve().parent.parent.parent
    / "hampton-roads-etl" / "hampton_roads.db"
)
PROPERTIES_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "Properties"
)


def _load_assessment_history(folder: Path) -> dict | None:
    sources_path = folder / "sources.json"
    if not sources_path.is_file():
        return None
    try:
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(sources, dict):
        return None
    block = sources.get("assessmentHistory")
    return block if isinstance(block, dict) else None


def _save_assessment_history(folder: Path, block: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    sources_path = folder / "sources.json"
    if sources_path.is_file():
        try:
            existing = json.loads(sources_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    if isinstance(block.get("records"), list):
        block = {**block, "records": sorted(
            block["records"], key=lambda r: r.get("fiscal_year", 0),
        )}
    existing["assessmentHistory"] = block

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(folder),
        delete=False, suffix=".tmp",
    ) as tmp:
        json.dump(existing, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(sources_path)


def _fetch_etl_history(
    db: sqlite3.Connection,
    *,
    gpin: str | None = None,
    parcel_id: str | None = None,
) -> tuple[dict | None, list[dict]]:
    """Look up one parcel in the ETL DB. Returns (inventory_row, history_rows)
    or (None, []) if not found."""
    inv_row = None
    if gpin:
        cur = db.execute(
            "SELECT * FROM va_multifamily_inventory WHERE gpin = ? LIMIT 1",
            (gpin,),
        )
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if row:
            inv_row = dict(zip(cols, row))
    if not inv_row and parcel_id:
        cur = db.execute(
            "SELECT * FROM va_multifamily_inventory WHERE parcel_id = ? LIMIT 1",
            (parcel_id,),
        )
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if row:
            inv_row = dict(zip(cols, row))
    if not inv_row:
        return None, []

    cur = db.execute(
        """
        SELECT fiscal_year, assessed_value, land_value, improvement_value
        FROM va_assessment_history
        WHERE city = ? AND parcel_id = ?
        ORDER BY fiscal_year
        """,
        (inv_row["city"], inv_row["parcel_id"]),
    )
    history = [
        {
            "fiscal_year": int(r[0]),
            "assessed_value": int(r[1]) if r[1] else 0,
            "land_value": int(r[2]) if r[2] else None,
            "building_value": int(r[3]) if r[3] else None,
            "note": "",
        }
        for r in cur.fetchall()
        if r[0] is not None
    ]
    return inv_row, history


def _build_block(inv_row: dict, history: list[dict]) -> dict:
    return {
        "source": "City Assessor (auto-pulled via va_assessors ETL)",
        "city": inv_row.get("city"),
        "parcel_id": inv_row.get("parcel_id"),
        "gpin": inv_row.get("gpin"),
        "pull_date": inv_row.get("pull_date") or dt.date.today().isoformat(),
        "records": history,
    }


def main(apply: bool = False) -> int:
    if not ETL_DB.is_file():
        print(f"ERROR: ETL database not found at {ETL_DB}")
        print("Run `python hampton_roads_etl.py --only=asr` first.")
        return 1
    if not PROPERTIES_ROOT.is_dir():
        print(f"ERROR: Properties folder not found at {PROPERTIES_ROOT}")
        return 1

    db = sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True)
    folders = sorted(
        p for p in PROPERTIES_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )

    print(f"Scanning {len(folders)} property folders against ETL DB...\n")
    n_matched = 0
    n_unmatched = 0
    n_written = 0
    n_skipped = 0
    n_no_existing = 0

    for folder in folders:
        existing = _load_assessment_history(folder)
        existing_gpin = (existing or {}).get("gpin")
        existing_parcel = (existing or {}).get("parcel_id")

        if not existing_gpin and not existing_parcel:
            n_no_existing += 1
            continue

        inv_row, history = _fetch_etl_history(
            db, gpin=existing_gpin, parcel_id=existing_parcel,
        )
        if not inv_row or not history:
            n_unmatched += 1
            print(f"  [no match] {folder.name:<45} GPIN={existing_gpin} parcel={existing_parcel}")
            continue

        n_matched += 1
        existing_n = len(existing.get("records") or []) if existing else 0
        new_n = len(history)
        # Only write if ETL has MORE records than what's stored, or the values differ.
        # Compare as (fy, value) tuples — if any difference, refresh.
        existing_pairs = {
            (r.get("fiscal_year"), r.get("assessed_value"))
            for r in (existing.get("records") or [])
        } if existing else set()
        new_pairs = {(r["fiscal_year"], r["assessed_value"]) for r in history}
        if existing_pairs == new_pairs:
            n_skipped += 1
            print(f"  [skip]    {folder.name:<45} {new_n} records, identical to ETL — no write")
            continue

        block = _build_block(inv_row, history)
        if apply:
            _save_assessment_history(folder, block)
            n_written += 1
            tag = "WROTE  "
        else:
            tag = "would  "

        diff = f"({existing_n} → {new_n})" if existing_n != new_n else "(values changed)"
        print(
            f"  [{tag}]  {folder.name:<45} {inv_row['city']:<10} "
            f"FY{history[0]['fiscal_year']}-FY{history[-1]['fiscal_year']} "
            f"{diff} latest ${history[-1]['assessed_value']:,}"
        )

    db.close()

    print(
        f"\n--- Summary ---\n"
        f"  Total folders:                {len(folders)}\n"
        f"  No existing block (skipped):  {n_no_existing}\n"
        f"  Matched in ETL:               {n_matched}\n"
        f"  Unmatched (parcel not in ETL): {n_unmatched}\n"
        f"  Already in sync (no-op):      {n_skipped}\n"
        f"  Wrote:                        {n_written if apply else 0}\n"
    )

    if not apply and n_matched > n_skipped:
        print("Re-run with `--apply` to write changes.")
    return 0


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    sys.exit(main(apply=apply_flag))
