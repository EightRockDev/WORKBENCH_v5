"""Correct specific fields on existing custom properties.

Custom properties are entered through a modal whose numeric widgets cannot
express "unknown" - latitude/longitude default to Norfolk (36.85, -76.29) and
are saved as though they were data whenever the ZIP autofill does not fire.
Eastwyk Village was stored that way: a Virginia Beach property sitting 5.37
miles from its own address, which corrupts its map pin and every radius comp
drawn around it.

Each PATCH below names the property, the fields to change, and why. Add an
entry to fix another one; the machinery does not change.

Writes to BOTH stores the Add-property dialog writes to, in the same order:
    Properties/_custom_props.json   (source of truth)
    properties table in workbench.db (query layer)

The SQLite write is INSERT OR REPLACE over the full SCHEMA_COLUMNS tuple, so
the row handed to it must be COMPLETE. A partial dict would null every column
it omitted. Rows are therefore merged: existing SQLite row, then the JSON
entry, then the patch on top.

Run:
    uv run python -u scripts/patch_custom_props.py
"""

from __future__ import annotations

import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# .env before the imports below - PROPERTIES_ROOT and the storage backend are
# resolved at import time from the environment.
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception as exc:                                    # noqa: BLE001
    print(f"[warn] .env not loaded ({exc}) - continuing with process env")

from data.db import DB_PATH, get_connection, upsert_property   # noqa: E402
from data.property_io import (                                 # noqa: E402
    PROPERTIES_ROOT,
    _rel,
    load_custom_props,
)

PATCHES: list[dict] = [
    {
        "property_id": "custom-00e5a62b-d26e-4310-a613-7712b7ee2210",
        "label": "Eastwyk Village Apartments",
        "why": ("stored at the modal's Norfolk default 36.85,-76.29 - 5.37 mi "
                "from 1201 Edenham Court. Corrected against ALN record 133867 "
                "'Eastwyck Village', same address / 96 units / built 1994."),
        "set": {
            "latitude": 36.800621,
            "longitude": -76.214958,
            "zip": "23464",
            "county": "Virginia Beach",
            "address": "1201 Edenham Court",   # was stored with a double space
        },
    },
]


def _sqlite_row(pid: str) -> dict | None:
    with get_connection(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM properties WHERE property_id = ?",
                           (pid,)).fetchone()
    return dict(row) if row else None


def main() -> int:
    print("=" * 68)
    print("  PATCH CUSTOM PROPERTIES")
    print("=" * 68)
    print(f"  Properties root : {PROPERTIES_ROOT}")
    print(f"  SQLite DB       : {DB_PATH}")
    print()

    custom = load_custom_props(PROPERTIES_ROOT)
    by_id = {cp.get("property_id"): cp for cp in custom}

    planned: list[tuple[dict, dict]] = []
    for patch in PATCHES:
        pid = patch["property_id"]
        current = by_id.get(pid)
        print(f"  {patch['label']}  [{pid}]")
        if current is None:
            print("    NOT FOUND in _custom_props.json - skipping\n")
            continue
        print(f"    why: {patch['why']}")
        changes = {k: v for k, v in patch["set"].items() if current.get(k) != v}
        if not changes:
            print("    already correct - nothing to do\n")
            continue
        for k, v in changes.items():
            print(f"    {k:<12} {current.get(k)!r}  ->  {v!r}")
        print()
        planned.append((patch, current))

    if not planned:
        print("  Nothing to change.")
        return 0

    try:
        answer = input("  Type YES to write, anything else to cancel: ").strip()
    except EOFError:
        answer = ""
    if answer != "YES":
        print("\n  Cancelled. Nothing was written.")
        return 1

    print("\n  writing...")
    for patch, current in planned:
        pid = patch["property_id"]
        current.update(patch["set"])

        # Merge so INSERT OR REPLACE cannot blank a column that only one
        # store happens to hold.
        merged = {}
        merged.update(_sqlite_row(pid) or {})
        merged.update({k: v for k, v in current.items() if v is not None
                       or k in patch["set"]})
        merged.update(patch["set"])
        merged["property_id"] = pid
        merged.setdefault("name", current.get("name"))

        upsert_property(merged)
        print(f"    {patch['label']}: workbench.db upserted")

    from core.storage import get_storage
    get_storage().write_text(f"{_rel(PROPERTIES_ROOT)}/_custom_props.json",
                             json.dumps(custom, indent=2))
    print("    _custom_props.json rewritten")

    print("\n  AFTER (read back from disk)")
    fresh = {cp.get("property_id"): cp for cp in load_custom_props(PROPERTIES_ROOT)}
    ok = True
    for patch in PATCHES:
        pid = patch["property_id"]
        j, s = fresh.get(pid), _sqlite_row(pid)
        if not j or not s:
            print(f"    {patch['label']}: MISSING  <-- problem")
            ok = False
            continue
        for k, v in patch["set"].items():
            jm = j.get(k) == v
            sm = s.get(k) == v
            flag = "" if (jm and sm) else "   <-- problem"
            ok = ok and jm and sm
            print(f"    {patch['label'][:22]:<22} {k:<10} json={j.get(k)!r} "
                  f"sqlite={s.get(k)!r}{flag}")

    print("\n  " + ("DONE - reopen the Workbench to see the corrected pin."
                    if ok else "FAILED - see the problem lines above."))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
