"""Measure how far the HUD FMR blend sits below OBSERVED market rent.

Why this exists (2026-08-14). Rent coverage is now 100% of MF rows, but the
P0-2 rent-delta gate is stuck at 31.6% and did not move when coverage went
9.2% -> 100%. That is the tell: the error is not missing data, it is a
SYSTEMATIC bias. HUD Fair Market Rent is a 40th-percentile subsidy benchmark,
so it sits structurally below market asking rent - stamping more of it cannot
close a gap that more of it creates.

The fix is a calibration factor. This script does NOT apply one: it measures
the bias, per city, from observed rents, and reports whether the bias is
stable enough to trust. Two rules keep this honest:

  * Measure against a source INDEPENDENT of the gate. The gate scores the
    backbone estimate against the legacy survey's avg_rent. So calibration
    evidence comes from the SCRAPED LISTINGS instead. Fitting a factor on the
    same rents the gate scores would move the number without improving a
    single estimate - that is gaming the gate, not underwriting.
  * A factor nobody can trace is worse than no factor. Every ratio here is
    reported with its sample size, and a city under MIN_SAMPLE is named as
    unusable rather than quietly folded into a global average.

Run: uv run python scripts/measure_rent_bias.py
"""

from __future__ import annotations

import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import phase0, rent_signal          # noqa: E402
from core.market_data import CITY_TO_COUNTY_FIPS_5  # noqa: E402

# Below this, a city's ratio is noise reported as a number - name it unusable.
MIN_SAMPLE = 5
# A ratio outside this band is a unit mismatch or a bad scrape, not a market.
SANE_RATIO = (0.7, 3.0)


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("rent-bias: no workbench.db on this box - skipping")
        return 0

    blend = rent_signal.county_fmr_blend()
    if not blend:
        print("rent-bias: no hud_fmr table yet - run publicdata first")
        return 0

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            obs = conn.execute(
                """SELECT p.city AS city, p.est_avg_rent AS rent
                     FROM properties_8r p
                    WHERE p.rent_source = 'listings'
                      AND p.est_avg_rent IS NOT NULL
                      AND p.est_avg_rent > 0""").fetchall()
        except sqlite3.Error as e:
            print(f"rent-bias: backbone not readable ({e})")
            return 0

        print("=" * 64)
        print("RENT BIAS — observed market rent vs the HUD FMR blend")
        print("=" * 64)
        print(f"\nObserved (rent_source='listings') rows: {len(obs):,}")
        if not obs:
            print("\nNo observed rents on the backbone yet, so the FMR bias")
            print("cannot be measured. This is the real blocker on the rent")
            print("delta gate - not FMR coverage, which is already 100%.")
            print("\nUnblock: more listings reaching the crosswalk. See the")
            print("[rent-signal] funnel lines in phase0-latest.txt for which")
            print("stage is dropping them.")
            return 0

        by_city: dict[str, list[float]] = {}
        for r in obs:
            city = r["city"]
            fips = CITY_TO_COUNTY_FIPS_5.get(city)
            fmr = blend.get(fips) if fips else None
            if not fmr:
                continue
            ratio = float(r["rent"]) / fmr
            if not (SANE_RATIO[0] <= ratio <= SANE_RATIO[1]):
                continue
            by_city.setdefault(city, []).append(ratio)

        print("\n-- Per-city ratio (observed ÷ FMR blend) --")
        usable: list[float] = []
        for city, ratios in sorted(by_city.items(), key=lambda kv: -len(kv[1])):
            med = statistics.median(ratios)
            flag = "" if len(ratios) >= MIN_SAMPLE else "   <- sample too small"
            print(f"  {city:<16} n={len(ratios):>4}  median={med:5.2f}x{flag}")
            if len(ratios) >= MIN_SAMPLE:
                usable.extend(ratios)

        print("\n-- Verdict --")
        if not usable:
            print(f"  No city clears n>={MIN_SAMPLE}. There is not enough")
            print("  observed rent to justify a calibration factor yet, and")
            print("  inventing one from thin data would move the gate without")
            print("  improving a single estimate.")
            return 0

        med = statistics.median(usable)
        spread = (statistics.pstdev(usable) if len(usable) > 1 else 0.0)
        print(f"  Pooled median ratio: {med:.3f}x  (n={len(usable)}, "
              f"sd={spread:.3f})")
        implied = abs(1 - 1 / med) if med else 0
        print(f"  A flat {med:.2f}x on the FMR blend would cut the average")
        print(f"  estimate error by roughly {implied:.0%}.")
        if spread > 0.25:
            print("  BUT the spread is wide - the bias is not a single")
            print("  constant across markets. Prefer per-city factors, and")
            print("  only for cities that clear the sample floor.")
        else:
            print("  The spread is tight enough for a single factor to be")
            print("  defensible, recorded as its own rent_source so every")
            print("  calibrated number stays traceable.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
