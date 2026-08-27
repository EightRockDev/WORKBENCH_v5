"""One-shot: add Tidewater Gardens (Norfolk, 46 units) as a custom property.

Sourced from the ApartmentHomeLiving listing 2026-08-27 plus the 8528 parcel
record. The property is NOT in the ALN 3/10/2026 export (all 2,528 VA rows
checked), so it can only arrive through the custom-property path.

This script calls the SAME two functions the "Add property" dialog calls
(ui/sidebar.py:_show_add_property_dialog), in the same order, with no
parallel logic of its own:

    add_custom_property(prop)  -> Properties/_custom_props.json  (source of truth)
    upsert_property(prop)      -> properties table in data/workbench.db (query layer)

Differences from the dialog, both deliberate:
  * property_id is a deterministic uuid5, so re-running is idempotent. The
    dialog mints a fresh uuid4 every time, which means clicking it twice
    creates two rows for one building.
  * occupancy_pct stays NULL. The dialog's slider floors at a value and
    cannot express "unknown"; a default written as though it were data is
    what put Eastwyk Village at Norfolk's coordinates.

Run:
    uv run python -u scripts/add_tidewater_gardens.py
"""

from __future__ import annotations

import pathlib
import sys
import uuid

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# .env BEFORE importing property_io / db: PROPERTIES_ROOT and the storage
# backend are resolved at import time from the environment. A headless script
# that imports first and loads .env second silently writes to the wrong root
# (CLAUDE.md, 2026-07-31, third recurrence). load_dotenv never overrides a
# variable that is already set.
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception as exc:                                    # noqa: BLE001
    print(f"[warn] .env not loaded ({exc}) - continuing with process env")

from data.db import DB_PATH, get_connection, upsert_property   # noqa: E402
from data.property_io import (                                 # noqa: E402
    PROPERTIES_ROOT,
    add_custom_property,
    load_custom_props,
)

NAME = "Tidewater Gardens"
ADDRESS = "8522-8528 Chesapeake Blvd"
CITY = "Norfolk"

# Deterministic id -> re-running this script updates one row instead of
# minting a second building.
_KEY = f"{NAME}|{ADDRESS}|{CITY}".lower()
PROPERTY_ID = "custom-" + str(uuid.uuid5(uuid.NAMESPACE_URL, _KEY))

AVG_RENT = 1159.0      # Bay View 2BR submarket average - ESTIMATE, see tags
AVG_SQFT = 750.0       # midpoint of the listed 700-800 sf 2BR/1BA range

PROP = {
    "property_id": PROPERTY_ID,
    "name": NAME,
    "address": ADDRESS,
    "city": CITY,
    "state": "VA",
    "zip": "23503",
    "county": "Norfolk",
    "units": 46,
    "year_built": 1964,
    "occupancy_pct": None,          # unknown - deliberately not defaulted
    "avg_sqft": AVG_SQFT,
    "avg_rent": AVG_RENT,
    "rent_per_sqft": AVG_RENT / AVG_SQFT,
    "asset_class": "D",
    "property_type": "Garden (2)",
    "market": "VA - Norfolk",
    "submarket": "SNO",
    "latitude": 36.9306,
    "longitude": -76.2434,
    "owner": None,                  # not published; parcel held since 7/28/2005
    "manager": None,
    "status": "Custom",
    "asset_or_fee": "Asset",
    "website": ("https://www.apartmenthomeliving.com/apartment-finder/"
                "Tidewater-Gardens-Norfolk-VA-23503-1947000"),
    "source_file": "ApartmentHomeLiving 2026-08-27",
    "tags": ("NON-ALN | src:ApartmentHomeLiving 2026-08-27 | 100% 2BR/1BA "
             "700-800sf | 2 stories | no pets | parcel 21378105 (8528 bldg, "
             "19440sf, 0.86ac, R13) | 2025 assess $1,830,700 | 2025 tax "
             "$22,518 | last recorded sale 7/28/2005 | EST: avg_rent=Bay View "
             "2BR avg, asset_class from vintage + adjacent comps, occupancy "
             "unknown"),
}

# assessed_value_per_unit and last_sold_* are left NULL on purpose: the
# $1,830,700 assessment and the 2005 sale cover the 8528 parcel only, one of
# the two buildings. Spreading either across all 46 units invents a number.


def _existing() -> dict | None:
    for cp in load_custom_props(PROPERTIES_ROOT):
        if cp.get("property_id") == PROPERTY_ID:
            return cp
    return None


def _sqlite_row() -> dict | None:
    with get_connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT property_id, name, address, city, units, year_built, "
            "asset_class, avg_rent, latitude, longitude, status "
            "FROM properties WHERE property_id = ?", (PROPERTY_ID,)).fetchone()
    return dict(row) if row else None


def main() -> int:
    print("=" * 68)
    print("  ADD CUSTOM PROPERTY - Tidewater Gardens")
    print("=" * 68)
    print(f"  Properties root : {PROPERTIES_ROOT}")
    print(f"  SQLite DB       : {DB_PATH}")
    print(f"  property_id     : {PROPERTY_ID}")
    print()

    print("  BEFORE")
    print(f"    _custom_props.json entries : {len(load_custom_props(PROPERTIES_ROOT))}")
    print(f"    this property in JSON      : {'YES' if _existing() else 'no'}")
    print(f"    this property in SQLite    : {'YES' if _sqlite_row() else 'no'}")
    print()

    print("  WILL WRITE")
    for k in ("name", "address", "city", "state", "zip", "county", "units",
              "year_built", "occupancy_pct", "avg_sqft", "avg_rent",
              "rent_per_sqft", "asset_class", "property_type", "market",
              "submarket", "latitude", "longitude", "status"):
        v = PROP[k]
        print(f"    {k:<16} {'(null)' if v is None else v}")
    print()
    print("  Estimates, not source data: avg_rent, rent_per_sqft, avg_sqft,")
    print("  asset_class. Occupancy is left null rather than defaulted.")
    print()

    try:
        answer = input("  Type YES to write, anything else to cancel: ").strip()
    except EOFError:
        answer = ""
    if answer != "YES":
        print("\n  Cancelled. Nothing was written.")
        return 1

    print("\n  writing...")
    if _existing():
        # add_custom_property appends unconditionally, so for a re-run we
        # rewrite the list in place instead of growing a duplicate.
        import json

        from core.storage import get_storage
        from data.property_io import _rel
        props = [PROP if cp.get("property_id") == PROPERTY_ID else cp
                 for cp in load_custom_props(PROPERTIES_ROOT)]
        get_storage().write_text(f"{_rel(PROPERTIES_ROOT)}/_custom_props.json",
                                 json.dumps(props, indent=2))
        print("    _custom_props.json : updated existing entry")
    else:
        add_custom_property(dict(PROP))
        print("    _custom_props.json : appended")

    upsert_property(dict(PROP))
    print("    workbench.db       : upserted into properties")

    print("\n  AFTER (read back from disk)")
    row = _sqlite_row()
    print(f"    _custom_props.json entries : {len(load_custom_props(PROPERTIES_ROOT))}")
    print(f"    this property in JSON      : {'YES' if _existing() else 'NO  <-- problem'}")
    if row:
        print(f"    SQLite row                 : {row['name']} | {row['address']} | "
              f"{row['city']} | {row['units']}u | {row['year_built']} | "
              f"class {row['asset_class']} | {row['latitude']},{row['longitude']}")
    else:
        print("    SQLite row                 : NOT FOUND  <-- problem")

    ok = bool(row) and bool(_existing())
    print("\n  " + ("DONE - open the Workbench and search 'Tidewater'."
                    if ok else "FAILED - see the problem lines above."))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
