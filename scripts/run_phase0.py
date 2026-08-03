"""Phase 0 runner (spec 7.3) - double-clicked via run-phase0.bat.

P0-1: builds `properties_8r` from muni_records and prints the coverage
report against the 95% gate. Read-only toward everything except the new
properties_8r table.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0  # noqa: E402



def _learn_use_codes(db) -> list[str]:
    """Teach the spine which numeric use codes mean apartments, per city.

    Only touches cities whose roll publishes opaque codes AND that currently
    find no multifamily — where the text rules already work, learning could
    only introduce error. Every decision is printed with its evidence: these
    rules change what the comp engine sees, so they must be auditable rather
    than magic.
    """
    import sqlite3
    from collections import Counter
    from core import use_code_learn as ucl

    out = ["Learning apartment use codes from matched properties:"]
    with sqlite3.connect(db) as conn:
        try:
            crosswalk = conn.execute(
                "SELECT legacy_property_id, r8_property_id "
                "  FROM property_crosswalk").fetchall()
        except sqlite3.Error as e:
            # Print the ERROR, not a guess at its cause. This except once
            # translated a wrong column name into "no crosswalk yet" and the
            # learner silently never ran - Portsmouth sat at 0 multifamily
            # for weeks with a healthy crosswalk right there.
            return out + [f"  (crosswalk unavailable: {e} - "
                          f"nothing to learn from)"]
        if not crosswalk:
            return out + ["  (crosswalk empty - nothing to learn from)"]

        # Cities that found nothing: the only ones worth teaching.
        blind = [c for (c,) in conn.execute(
            """SELECT DISTINCT city FROM properties_8r
                WHERE city IS NOT NULL AND city <> ''""")]
        taught = 0
        for city in sorted(blind):
            rows = conn.execute(
                """SELECT p8.use_code, p8.units
                     FROM property_crosswalk x
                     JOIN properties_8r p8 ON p8.property_id = x.r8_property_id
                     JOIN properties leg   ON leg.property_id = x.legacy_property_id
                    WHERE p8.city = ? AND leg.units >= 10""", (city,)).fetchall()
            mf_codes = [uc for uc, units in rows
                        if units is None and ucl.is_opaque(uc)]
            if not mf_codes:
                continue
            # Citywide tallies come from the FULL roll (parcel_index), not
            # the pruned backbone: "too common to mean apartments" needs the
            # whole city as its denominator, or the prune would shrink the
            # base until a generic code like Chesapeake's 1010 (54% of the
            # real roll) slipped under the ceiling.
            has_index = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='parcel_index'").fetchone()
            roll = "parcel_index" if has_index else "properties_8r"
            citywide = Counter()
            for uc, n in conn.execute(
                    f"""SELECT use_code, count(*) FROM {roll}
                        WHERE city = ? GROUP BY use_code""", (city,)):
                citywide[str(uc or "").strip()] = n
            learning = ucl.learn_city(city, mf_codes, citywide)
            out.extend("  " + line for line in learning.describe())
            if learning.accepted_codes:
                import datetime as _dt
                ucl.save(conn, learning,
                         _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
                taught += len(learning.accepted_codes)
        if not taught:
            out.append("  no new codes met the evidence bar this run")
        else:
            out.append(f"  learned {taught} code(s) - they take effect on the "
                       f"next run-phase0 (re-run to see the new counts)")
    return out


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None:
        print("No workbench.db with municipal data found.")
        print("Looked for data\\workbench.db (or set ER_WORKBENCH_DB=<full path>).")
        print()
        print("The 3.9M-row muni_records table lives in the v2.4.1 machine's")
        print("workbench.db - copy that file to this app's data\\ folder first,")
        print("or run etl_munidata.py here to pull fresh municipal data.")
        return 1

    n = phase0.has_muni_records(db)
    print(f"Database: {db}")
    print(f"Assessor records available: {n:,}")
    if n == 0:
        print()
        print("muni_records is empty here. Copy the v2.4.1 workbench.db into")
        print("data\\ (it holds the 3.9M municipal rows) or run etl_munidata.py.")
        return 1

    # Which feeds are actually IN the database - ends any mystery about
    # whether a pull landed.
    import sqlite3 as _sq
    with _sq.connect(db) as conn:
        rows = conn.execute(
            """SELECT market, source_url, count(*) FROM muni_records
                WHERE kind LIKE 'assessor%' GROUP BY market, source_url
                ORDER BY market""").fetchall()
    hr = ("Norfolk", "Virginia Beach", "Chesapeake", "Hampton",
          "Newport News", "Portsmouth", "Suffolk")
    print()
    print("Assessor feeds present (Hampton Roads):")
    for market, url, n in rows:
        if market in hr:
            tail = url.rsplit("/", 3)
            print(f"  {market:15} {n:>9,}  .../{'/'.join(tail[-3:])}")

    print()
    print("Building the Eight Rock property backbone (properties_8r)...")
    report = phase0.build_spine(db)
    print()
    print(report.summary())
    print()
    if report.gate_passed:
        print("P0-1 GATE PASSED.")
    else:
        print("P0-1 gate not met yet. The 'attribute keys with no mapping'")
        print("list above is what to send back for tuning.")

    # ---- P0-2: shadow parity against the legacy table, when present ------
    import sqlite3
    from core import phase0_parity
    with sqlite3.connect(db) as conn:
        try:
            legacy_rows = conn.execute(
                "SELECT count(*) FROM properties WHERE units >= 10").fetchone()[0]
        except sqlite3.Error:
            legacy_rows = 0
    print()
    if legacy_rows == 0:
        print("P0-2 shadow parity: skipped - no legacy `properties` table in this")
        print("database. Point ER_WORKBENCH_DB at the v2.4.1 workbench.db (which")
        print("holds BOTH tables after this build step) to run the comparison.")
        return 0
    print(f"P0-2 shadow parity: comparing against {legacy_rows:,} legacy rows...")
    parity = phase0_parity.run_parity(db, db)
    print()
    print(parity.summary())

    # ---- Learn opaque use codes, then rebuild what they unlock -----------
    # Parity runs first on purpose: it writes property_crosswalk, which is the
    # only evidence linking a KNOWN apartment property to a specific parcel.
    # Without that link there is nothing to learn "18 means apartments" from.
    print()
    learn_report = _learn_use_codes(db)
    for line in learn_report:
        print(line)

    # Machine-readable gate state for downstream automation (the cutover
    # preflight consumes this instead of parsing the text above).
    import json
    gates = {
        "coverage": report.coverage,
        "p0_1_passed": report.gate_passed,
        "comp_overlap": parity.avg_comp_overlap,
        "rent_delta": parity.avg_rent_delta,
        "rent_pairs": parity.rent_pairs,
        "match_rate": parity.match_rate,
        "covered_match_rate": parity.covered_match_rate,
        "comp_subjects": parity.comp_subjects,
        "crosswalk_rows": len(parity.crosswalk_records),
        "p0_2_passed": parity.gate_passed,
        "rents_stamped": report.rents_stamped,
        "rents_from_listings": report.rents_from_listings,
    }
    out = Path(__file__).resolve().parent.parent / "reports"
    out.mkdir(exist_ok=True)
    (out / "phase0-gates.json").write_text(json.dumps(gates, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
