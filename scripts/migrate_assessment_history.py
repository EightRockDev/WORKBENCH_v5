"""One-shot migration: lift embedded assessment history out of sales.json
notes into the structured `sources.json -> assessmentHistory` block.

Idempotent: safe to re-run. Only writes if the target block is missing OR
contains fewer FY records than the parsed source. Preserves all existing
keys in `sources.json`.

Usage:
    python scripts/migrate_assessment_history.py        # dry run, report only
    python scripts/migrate_assessment_history.py --apply # actually write

Source format expected (in `sales.json` notes):
    "FY19 $6,463,000 | FY20 $6,463,000 | FY23 $10,664,900 ..."
    Optional parcel context: "Norfolk Parcel 41127600, GPIN 1540015241"

Output structure (added to `sources.json`):
    {
      "assessmentHistory": {
        "source": "Migrated from sales.json notes",
        "city": "Norfolk",          (best-effort from notes)
        "parcel_id": "41127600",    (best-effort)
        "gpin": "1540015241",       (best-effort)
        "pull_date": "2026-05-08",
        "records": [
          {"fiscal_year": 2019, "assessed_value": 6463000, ...}
        ]
      }
    }
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path

# Self-contained: avoid importing data.property_io (which pulls pydantic) so
# this script runs under the bare system python OR `uv run`. We re-implement
# the small folder-discovery + load/save assessment helpers inline.

PROPERTIES_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "Properties"
)


def discover_property_folders() -> list[Path]:
    if not PROPERTIES_ROOT.is_dir():
        return []
    return sorted(
        p for p in PROPERTIES_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def load_assessment_history(folder: Path) -> dict | None:
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
    if not isinstance(block, dict) or not block.get("records"):
        return None
    return block


def save_assessment_history(folder: Path, history: dict) -> None:
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

    if isinstance(history.get("records"), list):
        history = {
            **history,
            "records": sorted(history["records"], key=lambda r: r.get("fiscal_year", 0)),
        }
    existing["assessmentHistory"] = history

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(folder),
        delete=False, suffix=".tmp",
    ) as tmp:
        json.dump(existing, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(sources_path)


# Norfolk-style: "Parcel 41127600, GPIN 1540015241"
# Hampton-style: "Tax Acct 2000242, PIN 02C023A00 000000"
# Other VA cities use varied terms: GPIN/PIN/Tax Account/Map/Block
_PARCEL_PATTERNS = [
    re.compile(r"\bParcel\s+([A-Z0-9\-]+)", re.IGNORECASE),
    re.compile(r"\bTax\s+Acct\s+([A-Z0-9\-]+)", re.IGNORECASE),
    re.compile(r"\bAccount\s+#?\s*([A-Z0-9\-]+)", re.IGNORECASE),
]
_GPIN_PATTERNS = [
    re.compile(r"\bGPIN\s+([A-Z0-9\- ]+?)(?=[.,]|\s+\w{3,}|$)", re.IGNORECASE),
    re.compile(r"\bPIN\s+([A-Z0-9\- ]+?)(?=[.,]|\s+\w{3,}|$)", re.IGNORECASE),
]
_FY_PATTERN = re.compile(r"FY(\d{2,4})\s*\$\s*([\d,]+)")
_HR_CITIES = (
    "Norfolk", "Virginia Beach", "Chesapeake", "Portsmouth",
    "Suffolk", "Hampton", "Newport News",
)


def _parse_fy_records(text: str) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = []
    for match in _FY_PATTERN.finditer(text):
        year_token = match.group(1)
        amount_str = match.group(2).replace(",", "")
        try:
            amount = int(amount_str)
        except ValueError:
            continue
        if len(year_token) == 2:
            yr = int(year_token)
            year_4 = 2000 + yr if yr < 50 else 1900 + yr
        else:
            year_4 = int(year_token)
        rows.append({"fiscal_year": year_4, "assessed_value": amount})
    # De-dupe — last wins
    seen: dict[int, int] = {}
    for r in rows:
        seen[r["fiscal_year"]] = r["assessed_value"]
    return [
        {"fiscal_year": fy, "assessed_value": v, "land_value": None,
         "building_value": None, "note": ""}
        for fy, v in sorted(seen.items())
    ]


def _extract_parcel_meta(text: str) -> dict[str, str | None]:
    parcel_id = None
    gpin = None
    city = None
    for p in _PARCEL_PATTERNS:
        m = p.search(text)
        if m:
            parcel_id = m.group(1).strip().rstrip(",.")
            break
    for p in _GPIN_PATTERNS:
        m = p.search(text)
        if m:
            gpin = m.group(1).strip().rstrip(",.")
            # Strip trailing parens / extra chars
            gpin = re.split(r"\s{2,}", gpin)[0].strip()
            break
    for hr_city in _HR_CITIES:
        if hr_city.lower() in text.lower():
            city = hr_city
            break
    return {"parcel_id": parcel_id, "gpin": gpin, "city": city}


def _gather_history_from_sales(sales_path: Path) -> tuple[list[dict], dict[str, str | None]]:
    """Read sales.json, extract FY records + parcel metadata."""
    if not sales_path.is_file():
        return [], {}
    try:
        data = json.loads(sales_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [], {}

    if isinstance(data, dict):
        records = data.get("last_3_apartment_sales") or data.get("sales") or []
    elif isinstance(data, list):
        records = data
    else:
        return [], {}

    merged_fy: dict[int, int] = {}
    parcel_meta: dict[str, str | None] = {"parcel_id": None, "gpin": None, "city": None}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        notes = rec.get("notes") or ""
        for r in _parse_fy_records(notes):
            merged_fy[r["fiscal_year"]] = r["assessed_value"]
        # Parcel meta — first non-empty wins
        meta = _extract_parcel_meta(notes)
        for k, v in meta.items():
            if v and not parcel_meta.get(k):
                parcel_meta[k] = v

    if not merged_fy:
        return [], parcel_meta

    history = [
        {"fiscal_year": fy, "assessed_value": v, "land_value": None,
         "building_value": None, "note": ""}
        for fy, v in sorted(merged_fy.items())
    ]
    return history, parcel_meta


def main(apply: bool = False) -> int:
    folders = discover_property_folders()
    n_scanned = 0
    n_eligible = 0
    n_written = 0
    n_skipped_already_done = 0
    n_skipped_no_data = 0

    print(f"Scanning {len(folders)} property folders...\n")
    for folder_path in folders:
        n_scanned += 1
        sales_path = folder_path / "sales.json"
        if not sales_path.is_file():
            n_skipped_no_data += 1
            continue

        history, parcel_meta = _gather_history_from_sales(sales_path)
        if not history:
            n_skipped_no_data += 1
            continue

        n_eligible += 1
        # Idempotency check - only write if existing block is empty/smaller
        existing = load_assessment_history(folder_path)
        existing_count = len(existing.get("records") or []) if existing else 0
        if existing_count >= len(history):
            n_skipped_already_done += 1
            print(f"  [skip]   {folder_path.name:<45} already has {existing_count} FY records (>= {len(history)} parsed)")
            continue

        block = {
            "source": "Migrated from sales.json notes",
            "city": parcel_meta.get("city"),
            "parcel_id": parcel_meta.get("parcel_id"),
            "gpin": parcel_meta.get("gpin"),
            "pull_date": dt.date.today().isoformat(),
            "records": history,
        }

        if apply:
            save_assessment_history(folder_path, block)
            n_written += 1
            tag = "WROTE"
        else:
            tag = "would write"

        print(
            f"  [{tag}] {folder_path.name:<45} "
            f"FY{history[0]['fiscal_year']}-FY{history[-1]['fiscal_year']}, "
            f"{len(history)} records, "
            f"${history[-1]['assessed_value']:,} latest "
            f"({parcel_meta.get('city') or '?'}, "
            f"parcel {parcel_meta.get('parcel_id') or '?'})"
        )

    print(
        f"\n--- Summary ---\n"
        f"  Scanned:                   {n_scanned}\n"
        f"  Eligible (FY data found):  {n_eligible}\n"
        f"  Skipped (no FY data):      {n_skipped_no_data}\n"
        f"  Skipped (already migrated): {n_skipped_already_done}\n"
        f"  Wrote:                     {n_written if apply else 0}\n"
    )

    if not apply and n_eligible > n_skipped_already_done:
        print("Re-run with `--apply` to write changes.")
    return 0


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    sys.exit(main(apply=apply_flag))
