"""Find a join key between two assessor layers by brute force, not by guessing.

Richmond, 2026-08-15. Units live on the COR ownership layer (2,365 of them).
Assessed values live on the rva.gov workbook (76,765 of them). They key on
different id schemes - COR samples `405010001`, the workbook `C0010124002` -
so units and values sit in the same market and never meet. Section 4 taxes
stay blocked and MF count sits at 140 against 108,033 parcels.

Two axes were already tried and are now closed by evidence:
  * apn alias (the PTM_ID fix) - merged the workbook to VDEM, not to COR.
  * address crosswalk - the 2026-08-15 review proved NO Richmond assessor
    source maps a usable address at all, so there is nothing to match on.

Rather than guess a third transform, this scans EVERY field of one layer
against EVERY field of the other and reports which pairs actually share
values. That is how the PTM_ID fix was eventually found: the key was in the
data the whole time under a name nobody looked for. A pair with high overlap
is a join key; no pair with overlap means the layers genuinely share no
identifier and geometry is the only remaining axis.

Read-only. Writes no data, changes no mapping - it reports candidates for a
human to confirm before anything is wired.

Run: uv run python scripts/find_join_key.py [--market Richmond]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0  # noqa: E402

# Enough to characterise a field without loading a whole county into RAM.
MAX_ROWS_PER_SOURCE = 40_000
# A field shared by nearly everything (a constant, a city name) is not a key.
MAX_CARDINALITY_RATIO = 1.01
MIN_DISTINCT = 50
TOP_PAIRS = 12


def _norm(v: object) -> str:
    """Compare ids the way a join would: case- and punctuation-insensitive."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()
    return s if len(s) >= 4 else ""


def _collect(conn, market: str) -> dict[str, dict[str, set]]:
    """source -> field -> set of normalized values."""
    per: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    counts: dict[str, int] = defaultdict(int)
    for src, rec in conn.execute(
            "SELECT source_url, record FROM muni_records "
            "WHERE market = ? AND kind LIKE 'assessor%'", (market,)):
        src = src or "?"
        if counts[src] >= MAX_ROWS_PER_SOURCE:
            continue
        counts[src] += 1
        raw = phase0._decode_muni_record(rec)
        if not isinstance(raw, dict):
            continue
        for k, v in raw.items():
            n = _norm(v)
            if n:
                per[src][k].add(n)
    return per, counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="Richmond")
    args = ap.parse_args()

    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("find-join-key: no workbench.db on this box - skipping")
        return 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        try:
            per, counts = _collect(conn, args.market)
        except sqlite3.Error as e:
            print(f"find-join-key: muni_records not readable ({e})")
            return 0
    finally:
        conn.close()

    print("=" * 68)
    print(f"JOIN-KEY SCAN — {args.market}")
    print("=" * 68)
    if len(per) < 2:
        print(f"\nOnly {len(per)} assessor source(s) present - nothing to join.")
        return 0

    print("\n-- Sources scanned --")
    for src in sorted(per, key=lambda s: -counts[s]):
        print(f"  {counts[src]:>7,} rows  {len(per[src]):>3} fields  {src[:58]}")

    # Keep fields that could plausibly be an identifier.
    usable: dict[str, dict[str, set]] = {}
    for src, fields in per.items():
        keep = {}
        for f, vals in fields.items():
            if len(vals) < MIN_DISTINCT:
                continue
            if len(vals) > counts[src] * MAX_CARDINALITY_RATIO:
                continue
            keep[f] = vals
        usable[src] = keep

    print("\n-- Cross-source field overlap (top candidates) --")
    srcs = sorted(usable, key=lambda s: -counts[s])
    pairs: list[tuple[int, float, str, str, str, str]] = []
    for i, a in enumerate(srcs):
        for b in srcs[i + 1:]:
            for fa, va in usable[a].items():
                for fb, vb in usable[b].items():
                    if not va or not vb:
                        continue
                    inter = len(va & vb)
                    if inter < MIN_DISTINCT:
                        continue
                    frac = inter / min(len(va), len(vb))
                    pairs.append((inter, frac, a, fa, b, fb))
    pairs.sort(key=lambda p: (-p[1], -p[0]))

    if not pairs:
        print("  NONE. No field of any source shares values with any field")
        print("  of another. These layers carry no common identifier, so no")
        print("  alias and no crosswalk can bridge them - the remaining axis")
        print("  is geometry (join the COR feature service's coordinates onto")
        print("  the parcel polygons), or a published parcel-id concordance")
        print("  from the locality.")
        return 0

    seen: set[tuple[str, str]] = set()
    shown = 0
    for inter, frac, a, fa, b, fb in pairs:
        if (a, b) in seen and shown >= TOP_PAIRS:
            break
        if shown >= TOP_PAIRS:
            break
        seen.add((a, b))
        shown += 1
        print(f"  {frac:6.1%} of the smaller side ({inter:,} values)")
        print(f"      {fa:<28} @ {a[-44:]}")
        print(f"      {fb:<28} @ {b[-44:]}")

    best = pairs[0]
    print("\n-- Verdict --")
    if best[1] >= 0.5:
        print(f"  STRONG candidate: {best[3]} <-> {best[5]} "
              f"({best[1]:.1%} overlap).")
        print("  Confirm a handful by eye, then map it in core/spine.py the")
        print("  way PTM_ID was - as an apn alias, not a bespoke join.")
    else:
        print(f"  Weak best overlap ({best[1]:.1%}). Probably coincidental")
        print("  formatting, not a key. Treat geometry as the live axis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
