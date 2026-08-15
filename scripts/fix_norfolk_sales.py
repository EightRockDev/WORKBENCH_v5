"""Make Norfolk sale history appear — or say exactly what is blocking it.

Owner, 2026-08-15: "I need to see sale history in norfolk now. All properties."

Norfolk sales ARE being pulled (the FY-stack puller reports fresh data), so the
break is downstream of the pull. Two candidates, and this script settles which:

  1. STALE INDEX. Until V5.43.0.0.0 the sale index's freshness stamp was
     count(*) + max(id) - both blind to CONTENT. Norfolk's puller dedupes on
     (gpin, transfer_date) and REPLACES rows, which changes neither, so a
     re-pull could leave the stamp identical and the index never rebuilt.
     This script forces a rebuild, which fixes that case outright.

  2. KEY MISMATCH. Norfolk sales key on `gpin`; if the backbone's Norfolk
     properties carry a different id, the indexed sales never join to a
     property and the card stays empty no matter how fresh the index is.
     Section 3 measures the actual overlap, so this stops being a guess.

Run it directly (fix-norfolk-sales.bat) - it does not wait for the 3 AM cycle.
Read-only apart from rebuilding the index, which the autopilot rebuilds anyway.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0, sale_history, sale_index  # noqa: E402

MARKET = "Norfolk"


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("norfolk-sales: no workbench.db on this box - nothing to do")
        return 1
    db = Path(db)
    print("=" * 68)
    print(f"NORFOLK SALE HISTORY — repair + diagnose   ({db})")
    print("=" * 68)

    # ---- 1. force the index to rebuild -------------------------------
    print("\n-- 1. Rebuilding the sale index (forced) --")
    try:
        stats = sale_index.build(db, force=True)
    except sqlite3.Error as e:
        msg = str(e).lower()
        print(f"   REBUILD FAILED: {e}")
        if "locked" in msg or "busy" in msg:
            print("   The database has another writer. Close the workbench")
            print("   (and let any running autopilot cycle finish), re-run.")
        elif "no such table" in msg:
            print("   This workbench.db carries no municipal records at all -")
            print("   it is a fresh/empty database, not the one the autopilot")
            print("   populates. Check ER_WORKBENCH_DB, or run a full cycle.")
        return 1
    print(f"   scanned {stats.get('scanned', 0):,} muni rows -> "
          f"{stats.get('sales', 0):,} sale records indexed")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # ---- 2. what landed, by market -------------------------------
        print("\n-- 2. Indexed sales per market --")
        rows = conn.execute(
            "SELECT market, COUNT(*) n FROM sale_records "
            "GROUP BY market ORDER BY n DESC LIMIT 12").fetchall()
        for r in rows:
            mark = "  <-- Norfolk" if r["market"] == MARKET else ""
            print(f"   {r['n']:>8,}  {r['market'] or '(none)'}{mark}")
        nf_indexed = conn.execute(
            "SELECT COUNT(*) n FROM sale_records WHERE market = ?",
            (MARKET,)).fetchone()["n"]
        if not nf_indexed:
            print(f"\n   Norfolk has ZERO indexed sales. The rows are being")
            print("   pulled, so the loss is in extraction: no price AND no")
            print("   date field was recognised on any Norfolk row, or no row")
            print("   yielded an apn or an address to key on. Section 3 below")
            print("   shows the field names actually present.")

        # ---- 3. do indexed sales JOIN to Norfolk properties? ----------
        print("\n-- 3. Do those sales reach Norfolk properties? --")
        try:
            props = conn.execute(
                "SELECT apn, address FROM properties_8r "
                "WHERE COALESCE(r8_market, city) = ?", (MARKET,)).fetchall()
        except sqlite3.Error as e:
            print(f"   backbone not readable ({e})")
            props = []
        sale_apns = {r[0] for r in conn.execute(
            "SELECT DISTINCT apn_norm FROM sale_records WHERE market = ? "
            "AND apn_norm <> ''", (MARKET,))}
        sale_addrs = {r[0] for r in conn.execute(
            "SELECT DISTINCT addr_norm FROM sale_records WHERE market = ? "
            "AND addr_norm <> ''", (MARKET,))}
        hit_apn = hit_addr = 0
        for p in props:
            if sale_history._norm_apn(p["apn"]) in sale_apns:
                hit_apn += 1
            elif sale_history._norm_addr(p["address"]) in sale_addrs:
                hit_addr += 1
        total = len(props)
        covered = hit_apn + hit_addr
        print(f"   Norfolk properties on the backbone: {total:,}")
        print(f"   distinct sale keys indexed: {len(sale_apns):,} apn / "
              f"{len(sale_addrs):,} address")
        if total:
            print(f"   properties that WILL show sale history: {covered:,} "
                  f"({covered / total:.1%})   "
                  f"[{hit_apn:,} by apn, {hit_addr:,} by address]")

        # ---- 4. field names actually present on Norfolk sales ---------
        print("\n-- 4. Fields on Norfolk sale rows (sampled) --")
        seen: Counter = Counter()
        sample_apn = sample_addr = None
        n = 0
        for (rec,) in conn.execute(
                "SELECT record FROM muni_records WHERE market = ? "
                "AND kind LIKE 'sales%' LIMIT 400", (MARKET,)):
            raw = phase0._decode_muni_record(rec)
            if not isinstance(raw, dict):
                continue
            n += 1
            seen.update(raw.keys())
            if sample_apn is None:
                m = phase0.normalize_record(MARKET, "VA", raw)
                sample_apn = str(m.get("apn") or "(none)")
                sample_addr = str(m.get("address") or "(none)")
        if not n:
            print(f"   No muni_records rows with kind='sales' for {MARKET}.")
            print("   The puller has not landed Norfolk sales on this box -")
            print("   run the arcgissales step (or a full autopilot cycle).")
        else:
            print(f"   sampled {n} rows; most common fields:")
            for k, c in seen.most_common(14):
                print(f"     {c:>4}x  {k}")
            print(f"   normalized apn on the first row: {sample_apn}")
            print(f"   normalized address:              {sample_addr}")
            pa = conn.execute(
                "SELECT apn, address FROM properties_8r WHERE "
                "COALESCE(r8_market, city) = ? AND apn IS NOT NULL LIMIT 1",
                (MARKET,)).fetchone()
            if pa:
                print(f"   a Norfolk PROPERTY's apn:        {pa['apn']}")
                print(f"   a Norfolk PROPERTY's address:    {pa['address']}")
                print("   ^ if those two apn formats differ, that is the bug:")
                print("     the sales and the properties key on different ids.")

        # ---- 5. verdict ----------------------------------------------
        print("\n-- 5. Verdict --")
        if total and covered / total >= 0.5:
            print(f"   FIXED. {covered:,} of {total:,} Norfolk properties now")
            print("   resolve sale history. Restart the workbench (the app")
            print("   caches lookups per process) and open any Norfolk deal.")
        elif nf_indexed and total and covered == 0:
            print("   Sales are indexed but NONE join to a Norfolk property.")
            print("   This is a key mismatch, not a freshness problem - compare")
            print("   the two apn formats printed in section 4. The fix is an")
            print("   apn alias in core/spine.py, the way Richmond's PTM_ID was")
            print("   handled. Send me section 4 and I will wire it.")
        elif not nf_indexed:
            print("   Nothing indexed for Norfolk - see section 4 for which")
            print("   field names the rows actually carry, and send it to me.")
        else:
            print(f"   Partial: {covered:,} of {total:,} properties covered.")
            print("   Sale history will appear on those and not the rest.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
