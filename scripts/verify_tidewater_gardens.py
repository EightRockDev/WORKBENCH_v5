"""Submit Tidewater Gardens for the spec-16 verified badge and validate it.

Norfolk is a `parcel_roll` city, so the badge can genuinely be earned or
refused here rather than parking as Pending. The claim being verified:

    8522-8528 Chesapeake Blvd, Norfolk    46 units    parcel 21378105

One caveat worth reading before you run this. The parcel id came from the
LoopNet record for the *8528* building alone - 19,440 sf on 0.86 ac - and the
community spans 8522-8528, which may well be two parcels. If Norfolk's roll
says parcel 21378105 holds roughly half of 46 units, validate_property will
correctly return FAILED on the unit check. That is not a bug and not a wasted
run: the evidence it prints tells us the complex is split across parcels, and
the fix is then to submit the parcel that actually carries all 46 units, or to
submit without a parcel id and let the address lookup resolve one.

So this script prints what the Norfolk roll actually holds on Chesapeake Blvd
BEFORE it submits anything. Read that block first - it is the answer either
way.

Run:
    uv run python -u scripts/verify_tidewater_gardens.py
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception as exc:                                    # noqa: BLE001
    print(f"[warn] .env not loaded ({exc}) - continuing with process env")

from core import phase0, user_properties                     # noqa: E402
from core.user_properties import (                           # noqa: E402
    _roll_table,
    city_capability,
    norm_addr,
)

NAME = "Tidewater Gardens"
ADDRESS = "8522-8528 Chesapeake Blvd"
CITY = "Norfolk"
UNITS = 46
PARCEL_ID = "21378105"      # 8528 building per LoopNet; set to None to let
                            # the address lookup resolve a parcel instead.


def _survey(db) -> None:
    """What the municipality actually publishes on this block.

    Targeted at 8500-8560 rather than an alphabetical slice: the first run of
    this script sorted every Chesapeake Blvd row by address and printed 25 of
    them, which stopped at 8489 and never reached the two we care about.
    """
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        table = _roll_table(conn)
        print(f"  roll table        : {table}")
        print(f"  Norfolk capability: {city_capability(conn, CITY)}")
        print()

        rows = conn.execute(
            f"SELECT apn, address, units FROM {table} "
            f"WHERE city = ? AND address LIKE '85%CHESAPEAKE%' "
            f"ORDER BY address", (CITY,)).fetchall()
        print(f"  Norfolk roll rows at 85xx Chesapeake Blvd: {len(rows)}")
        for apn, addr, units in rows:
            mark = ""
            if any(str(n) in str(addr or "") for n in (8522, 8524, 8526, 8528)):
                mark = "   <-- Tidewater Gardens address range"
            print(f"    apn={str(apn):<14} units={str(units):<6} {addr}{mark}")
        print()

        with_units = [r for r in rows if r[2] is not None]
        print(f"  rows on this block carrying a unit count: "
              f"{len(with_units)} of {len(rows)}")
        if not with_units:
            print("  >> Norfolk's roll publishes NO unit counts here. The "
                  "spec-16 unit")
            print("  >> check therefore cannot pass or fail - the submission "
                  "parks as")
            print("  >> PENDING and the nightly sweep re-checks it when unit "
                  "data lands.")
        print()

        hit = conn.execute(
            f"SELECT apn, address, units FROM {table} WHERE city = ? AND apn = ?",
            (CITY, PARCEL_ID or "")).fetchone()
        print(f"  LoopNet parcel {PARCEL_ID!r} present in Norfolk roll: "
              f"{'YES ' + str(hit) if hit else 'NO'}")
        if not hit:
            print("  >> Norfolk APNs on this block look like 1540242676 "
                  "(10 digits).")
            print("  >> 21378105 is LoopNet's own id, not a Norfolk parcel "
                  "number -")
            print("  >> submitting it would eventually FAIL the parcel check. "
                  "Set")
            print("  >> PARCEL_ID from the list above, or leave it None.")
        print()
        print(f"  our address normalizes to: {norm_addr(ADDRESS)!r}")
        exact = [r for r in rows if norm_addr(r[1]) == norm_addr(ADDRESS)]
        verdict = "YES" if exact else "no - set ADDRESS to one the roll uses"
        print(f"  exact address match in roll: {verdict}")


def _write_lock_ok(db) -> bool:
    """workbench.db has no WAL, so an open Workbench or a running autopilot
    cycle holds a lock that blocks every write. Fail here with something
    actionable instead of a traceback out of submit_property."""
    try:
        conn = sqlite3.connect(db, timeout=10)
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        conn.close()
        return True
    except sqlite3.OperationalError as exc:
        print()
        print(f"  CANNOT WRITE: {exc}")
        print("  workbench.db is locked by another process - the Workbench")
        print("  itself, or an autopilot cycle mid-run. Nothing was written.")
        print()
        print("  Close the Workbench tab and stop its service, or wait for the")
        print("  cycle to finish, then run this again. The survey above is")
        print("  read-only and stays valid either way.")
        return False


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None:
        print("no workbench.db found - nothing to validate against")
        return 1

    print("=" * 70)
    print("  SPEC-16 VERIFIED BADGE - Tidewater Gardens")
    print("=" * 70)
    print(f"  workbench.db      : {db}")
    print()
    print("  WHAT THE NORFOLK ROLL HOLDS (read this first)")
    _survey(db)

    if not _write_lock_ok(db):
        return 1

    print("  WILL SUBMIT")
    print(f"    name      {NAME}")
    print(f"    address   {ADDRESS}")
    print(f"    city      {CITY}")
    print(f"    units     {UNITS}")
    print(f"    parcel    {PARCEL_ID}")
    print()
    print("  Then validate against the roll. Expect one of:")
    print("    verified - parcel matches and units are within 10 percent")
    print("    failed   - the roll contradicts the parcel or the unit count")
    print("    pending  - the roll has no unit count for this parcel yet")
    print()
    print("  Validation scans every Norfolk row in the roll table; on a large")
    print("  roll this takes a little while. Nothing else is modified.")
    print()

    try:
        answer = input("  Type YES to submit and validate: ").strip()
    except EOFError:
        answer = ""
    if answer != "YES":
        print("\n  Cancelled. Nothing was written.")
        return 1

    print("\n  submitting...")
    row = user_properties.submit_property(
        name=NAME, address=ADDRESS, city=CITY, units=UNITS,
        parcel_id=PARCEL_ID,
        website=("https://www.apartmenthomeliving.com/apartment-finder/"
                 "Tidewater-Gardens-Norfolk-VA-23503-1947000"),
        db_path=db)
    upid = row["user_property_id"]
    print(f"    user_property_id: {upid}")

    print("  validating against the Norfolk assessor roll...")
    result = user_properties.validate_property(upid, db)

    print()
    print("  RESULT")
    print(f"    status : {result.status.upper()}")
    print(f"    reason : {result.reason}")
    if result.matched_8r_id:
        print(f"    matched: {result.matched_8r_id}")
    print("    evidence:")
    for line in json.dumps(result.checks, indent=6).splitlines():
        print("    " + line)

    print()
    if result.status == user_properties.VERIFIED:
        print("  DONE - Tidewater Gardens carries the verified badge.")
    elif result.status == user_properties.PENDING:
        print("  PENDING - queued. The nightly sweep re-checks it whenever")
        print("  Norfolk's data lands; nothing further to do by hand.")
    else:
        print("  FAILED - read the evidence above. If the roll shows this")
        print("  parcel holding only part of the 46 units, the community is")
        print("  split across parcels: set PARCEL_ID to the one carrying all")
        print("  46, or to None to let the address lookup pick, then re-run.")
        print("  Re-running is safe - the id is deterministic, so it updates")
        print("  the same row instead of creating a second submission.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
