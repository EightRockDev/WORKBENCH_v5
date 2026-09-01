"""
Promote swept deals into the Workbench property list.

Deals arrive from SharePoint into deal_sweep_inbox. This turns them into rows
in the property table so they show up in the Workbench like any other property.

    promote-deals.bat          look only - shows exactly what it would do
    promote-deals.bat GO       actually write

DEFAULT IS PREVIEW. Nothing is written without GO.

What it does
------------
A deal that is NOT already in the property list is added as a new row.
A deal that MATCHES a property already there has its details filled in -
units, occupancy, average rent, square footage, year built - from the rent
roll and T-12. Existing values are only replaced where the sweep actually
established a number; nothing is blanked out.

Everything it touches is tagged 8RWB so you can tell deal-sourced rows and
fields apart from the rest.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("EIGHT_ROCK_DB_PATH", "data/workbench.db"))
if not DB.is_absolute():
    DB = HERE / DB

SOURCE_TAG = "8RWB"
ID_PREFIX = "8RWB-"
TAG = "8RWB Deal"


def say(*a) -> None:
    print(*a, flush=True)


def norm(s: str | None) -> str:
    """Squash a property name to something comparable across sources.

    Deal folders are named like "Miars Farm - Chesapeake" or
    "River's Edge-56u-Elizabeth City", while ALN carries just "Miars Farm".
    Strip the unit counts and filler so the two can be lined up.
    """
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\b\d+\s*(u|units?)\b", " ", s)          # "56u", "140 Units"
    s = re.sub(r"\b(apartments?|apts?|townhomes?|townhouses?|"
               r"community|communities|the|at|of|llc|lp|inc)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def same_property(a: str, b: str, city_a: str = "", city_b: str = "") -> bool:
    """True when two normalised names plainly refer to the same asset.

    A deal folder carries the city or unit count on the end - "Tivoli
    Apartments - 140 Units - Virginia Beach" against ALN's "Tivoli
    Apartments" - so an exact match is too strict. A prefix match is the
    right test, but on how many words?

    Two words is safe on its own. One word is only safe when the city also
    agrees, otherwise "Tivoli" or "The Cove" would collide with every
    same-named property in the state.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = a.split(), b.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if long_[:len(short)] != short:
        return False
    if len(short) >= 2:
        return True
    return bool(city_a) and city_a == city_b


def find_match(key: str, table: dict[str, str]) -> str | None:
    if key in table:
        return table[key]
    for other, value in table.items():
        if same_property(key, other):
            return value
    return None


def columns(con: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {r["name"]: r for r in con.execute(f'PRAGMA table_info("{table}")')}


def pick_target(con: sqlite3.Connection) -> str:
    """The main property list - the one the Workbench counts."""
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "properties" not in tables:
        raise SystemExit("No 'properties' table in this database.")
    return "properties"


def build_row(deal: dict, cols: dict, today: str) -> dict:
    """Map a swept deal onto whatever columns the target table actually has.

    Only fields the sweep established are set. A metric it could not read is
    left NULL rather than written as zero.
    """
    m = deal.get("metrics") or {}
    occ = m.get("occupancy_pct")

    wanted = {
        "property_id": ID_PREFIX + deal["deal_key"][-16:],
        "id": None,
        "name": deal.get("deal_name"),
        "city": deal.get("city"),
        "state": deal.get("state"),
        "address": deal.get("address"),
        "units": m.get("units"),
        "year_built": m.get("year_built"),
        # The table stores occupancy as a fraction 0.0-1.0, the sweep carries
        # 0-100. Convert, or the whole column becomes nonsense.
        "occupancy_pct": (occ / 100.0) if isinstance(occ, (int, float)) else None,
        "avg_rent": m.get("avg_in_place_rent"),
        "avg_sqft": m.get("avg_unit_sqft"),
        "rent_per_sqft": (
            round(m["avg_in_place_rent"] / m["avg_unit_sqft"], 5)
            if m.get("avg_in_place_rent") and m.get("avg_unit_sqft") else None),
        "asset_type": "Multifamily",
        "tags": TAG,
        "status": "8RWB Deal Pipeline",
        "source_file": SOURCE_TAG,
        "pull_date": today,
        "website": deal.get("sharepoint_url"),
        "raw_row": json.dumps(deal, separators=(",", ":")),
    }
    row = {k: v for k, v in wanted.items() if k in cols and v is not None}

    # Anything required that we have no value for gets a safe placeholder.
    for name, info in cols.items():
        if info["notnull"] and info["dflt_value"] is None and name not in row \
           and not info["pk"]:
            row[name] = ""
    return row


def run_promote(go: bool = False, quiet: bool = False,
                db_path: Path | None = None) -> dict:
    """Add new deals to the property list and fill in existing ones.

    Returns a summary dict. Safe to call repeatedly - a second run with
    nothing new finds nothing to do.
    """
    global DB
    if db_path is not None:
        DB = db_path
    _quiet = quiet

    def say(*a):
        if not _quiet:
            print(*a, flush=True)

    if not DB.exists():
        say(f"Database not found: {DB}")
        return {"ok": False, "error": f"database not found: {DB}"}

    say("\n  8RWB - put swept deals into the property list")
    say("  " + "=" * 62)
    say(f"  database : {DB}")
    say(f"  mode     : {'WRITE' if go else 'PREVIEW - nothing will be changed'}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")

    target = pick_target(con)
    cols = columns(con, target)
    before = con.execute(f'SELECT COUNT(*) FROM "{target}"').fetchone()[0]
    say(f"  table    : {target}  ({before:,} rows now)")

    deals = [dict(r) for r in con.execute(
        "SELECT deal_key, deal_name, state, city, sharepoint_url, completeness, "
        "payload_json FROM deal_sweep_inbox ORDER BY deal_name")]
    for d in deals:
        d.update(json.loads(d.pop("payload_json")))
    say(f"\n  {len(deals)} deals waiting.")

    # Index the property list by normalised name so a deal can be matched to a
    # property that is already there.
    existing: list[tuple[str, str, sqlite3.Row]] = []
    seen_keys: set[str] = set()
    for r in con.execute(f'SELECT * FROM "{target}"'):
        key = norm(r["name"])
        if key and key not in seen_keys:
            seen_keys.add(key)
            existing.append((key, norm(r["city"] if "city" in r.keys() else ""), r))

    today = date.today().isoformat()
    to_insert: list[dict] = []
    to_update: list[tuple[str, str, dict, list[str]]] = []

    # Fields the sweep can fill in on a property that already exists.
    FILLABLE = ("units", "year_built", "occupancy_pct", "avg_rent",
                "avg_sqft", "rent_per_sqft")

    for deal in deals:
        key = norm(deal["deal_name"])
        deal_city = norm(deal.get("city"))
        hit = None
        for other, other_city, row in existing:
            if same_property(key, other, deal_city, other_city):
                hit = row
                break

        row = build_row(deal, cols, today)

        if hit is None:
            to_insert.append(row)
            continue

        # Already in the list - fill in what the documents told us.
        changes = {k: v for k, v in row.items()
                   if k in FILLABLE and v is not None}
        changed_desc = []
        for k, v in list(changes.items()):
            old = hit[k] if k in hit.keys() else None
            if old is not None and isinstance(old, float) and isinstance(v, float) \
               and abs(old - v) < 1e-9:
                changes.pop(k)
                continue
            if old == v:
                changes.pop(k)
                continue
            changed_desc.append(f"{k}: {old} -> {v}")
        if changes:
            if "tags" in cols:
                t = (hit["tags"] or "") if "tags" in hit.keys() else ""
                if TAG not in t:
                    changes["tags"] = (t + "; " + TAG).strip("; ")
            if "source_file" in cols:
                changes["source_file"] = SOURCE_TAG
            if "pull_date" in cols:
                changes["pull_date"] = today
            pid = hit["property_id"] if "property_id" in hit.keys() else hit["id"]
            to_update.append((str(pid), deal["deal_name"], changes, changed_desc))

    say(f"\n  new properties to add      : {len(to_insert)}")
    say(f"  existing ones to fill in   : {len(to_update)}")
    say(f"  already current, no change : "
        f"{len(deals) - len(to_insert) - len(to_update)}")

    if to_insert:
        say("\n  " + ("WOULD ADD:" if not go else "ADDING:"))
        say(f"    {'NAME':<46} {'ST':<3} {'UNITS':>6} {'OCC':>7} {'AVG RENT':>10}")
        for r in to_insert:
            occ, rent = r.get("occupancy_pct"), r.get("avg_rent")
            say(f"    {str(r.get('name'))[:46]:<46} {str(r.get('state') or ''):<3} "
                f"{r.get('units') or '-':>6} "
                f"{(f'{occ*100:.1f}%' if occ else '-'):>7} "
                f"{(f'${rent:,.0f}' if rent else '-'):>10}")

    if to_update:
        say("\n  " + ("WOULD FILL IN:" if not go else "FILLING IN:"))
        for pid, name, changes, desc in to_update[:40]:
            say(f"    {name[:52]:<52}")
            for d in desc:
                say(f"        {d}")
        if len(to_update) > 40:
            say(f"    ... and {len(to_update) - 40} more")

    if not go:
        say("\n  " + "=" * 62)
        say("  PREVIEW ONLY - nothing was changed.")
        say("  If that looks right, double-click:  promote-deals-GO.bat")
        say("  " + "=" * 62 + "\n")
        con.close()
        return {"ok": True, "mode": "preview", "would_add": len(to_insert),
                "would_update": len(to_update)}

    if not to_insert and not to_update:
        say("\n  Nothing to do - the property list is already current.\n")
        con.close()
        return {"ok": True, "added": 0, "updated": 0, "failed": 0,
                "total_rows": before}

    inserted = updated = failed = 0
    try:
        with con:  # one transaction - all of it lands, or none of it
            for row in to_insert:
                fields = ", ".join(f'"{k}"' for k in row)
                marks = ", ".join("?" for _ in row)
                try:
                    con.execute(
                        f'INSERT INTO "{target}" ({fields}) VALUES ({marks})',
                        list(row.values()))
                    inserted += 1
                except sqlite3.Error as exc:
                    failed += 1
                    say(f"    FAILED to add {row.get('name')}: {exc}")

            pk = "property_id" if "property_id" in cols else "id"
            for pid, name, changes, _ in to_update:
                sets = ", ".join(f'"{k}" = ?' for k in changes)
                try:
                    con.execute(f'UPDATE "{target}" SET {sets} WHERE "{pk}" = ?',
                                list(changes.values()) + [pid])
                    updated += 1
                except sqlite3.Error as exc:
                    failed += 1
                    say(f"    FAILED to update {name}: {exc}")
    except sqlite3.Error as exc:
        say(f"\n  WRITE FAILED, nothing was changed: {exc}\n")
        con.close()
        return {"ok": False, "error": str(exc)}

    after = con.execute(f'SELECT COUNT(*) FROM "{target}"').fetchone()[0]
    say(f"\n  Added {inserted} properties, filled in {updated} existing ones"
        f"{f', {failed} failed' if failed else ''}.")
    say(f"  {target}: {before:,} -> {after:,} rows")
    say(f"\n  Everything touched is tagged '{TAG}'. To find them:")
    say(f"    SELECT name, city, units FROM {target} WHERE tags LIKE '%{TAG}%';\n")
    con.close()
    return {"ok": failed == 0, "added": inserted, "updated": updated,
            "failed": failed, "rows_before": before, "rows_after": after}


def main() -> int:
    result = run_promote(go=any(a.strip().upper() == "GO" for a in sys.argv[1:]))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
