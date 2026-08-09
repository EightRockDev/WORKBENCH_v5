"""Hampton Roads ETL — main entrypoint.

Pulls 8 free public data sources useful for Class C multifamily underwriting,
writes everything to a single SQLite file (`hampton_roads.db`).

Phase 1 (Census ACS, BLS LAUS, FRED, HUD FMR): macro + demographic context
Phase 2 (BAH, Census BPS, HUD LIHTC, FFIEC HMDA): supply, financing, military floor

Each puller is wrapped in try/except so one source failing doesn't kill the run.
Tables are dropped + replaced on each run (idempotent re-runs). Each table
carries a `pull_date` column.

Usage:
    cp .env.example .env                                  # add your free API keys
    python hampton_roads_etl.py                           # all 8 sources
    python hampton_roads_etl.py --only=fred,lihtc         # just these two
    python hampton_roads_etl.py --skip=bps,hmda           # everything but those
    python hampton_roads_etl.py --list                    # show source names
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from db import write
from pullers.bah import pull_bah
from pullers.bls import pull_bls_laus
from pullers.census_acs import pull_census_acs
from pullers.census_bps import pull_census_bps
from pullers.fred import pull_fred
from pullers.hmda import pull_hmda
from pullers.hud_fmr import pull_hud_fmr
from pullers.lihtc import pull_lihtc
from pullers.listings import pull_listings
from pullers.va_assessors import pull_va_assessors

# Load .env file from the script directory
load_dotenv(Path(__file__).resolve().parent / ".env")


# Source registry: short name → (display name, fn, table names)
SOURCES = {
    "acs":  ("Census ACS",  pull_census_acs, ("census_acs",)),
    "bls":  ("BLS LAUS",    pull_bls_laus,   ("bls_laus",)),
    "fred": ("FRED",        pull_fred,       ("fred_series",)),
    "fmr":  ("HUD FMR",     pull_hud_fmr,    ("hud_fmr",)),
    "bps":  ("Census BPS",  pull_census_bps, ("census_bps",)),
    "hmda": ("FFIEC HMDA",  pull_hmda,       ("hmda_originations", "hmda_lender_summary")),
    "lihtc":("HUD LIHTC",   pull_lihtc,      ("hud_lihtc",)),
    "bah":  ("DoD BAH",     pull_bah,        ("bah_rates", "bah_zip_mha")),
    "asr":  ("VA City Assessors", pull_va_assessors,
             ("va_multifamily_inventory", "va_assessment_history")),
    "listings": ("Rent Listings (in-house scraper)", pull_listings,
                 ("rent_listings",)),
}

# Sources known to take >1 minute. Listed for reference; --skip them if testing.
# Listings is slow because it's per-property HTTP at 3-sec/req politeness.
SLOW_SOURCES = {"bps", "hmda", "listings"}


def _run(name: str, fn, *table_names: str) -> None:
    """Run one puller, time it, write each returned DataFrame to a table."""
    print(f"\n=== {name} ===")
    t0 = time.time()
    try:
        result = fn()
    except KeyboardInterrupt:
        print(f"  INTERRUPTED after {time.time() - t0:.1f}s")
        raise
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        return

    if not isinstance(result, tuple):
        result = (result,)

    for df, table in zip(result, table_names):
        if df is None or df.empty:
            print(f"  {table}: no rows")
            continue
        n = write(df, table)
        print(f"  {table}: wrote {n:,} rows")

    print(f"  done in {time.time() - t0:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hampton_roads_etl",
        description="Pull free public data sources for HR Class C underwriting.",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated source short names to run (e.g. 'fred,lihtc'). "
             f"Available: {','.join(SOURCES.keys())}",
    )
    parser.add_argument(
        "--skip",
        help="Comma-separated source short names to skip (e.g. 'bps,hmda'). "
             f"Slow sources: {','.join(SLOW_SOURCES)}",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List source short names and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if args.list:
        print("Available sources (short name -> display name):")
        for short, (display, _, tables) in SOURCES.items():
            slow = "  [SLOW]" if short in SLOW_SOURCES else ""
            print(f"  {short:<6} -> {display:<14} (tables: {', '.join(tables)}){slow}")
        return 0

    only = set((args.only or "").split(",")) - {""}
    skip = set((args.skip or "").split(",")) - {""}
    bad = (only | skip) - set(SOURCES.keys())
    if bad:
        print(f"ERROR: unknown source name(s): {', '.join(sorted(bad))}")
        print(f"  Available: {','.join(SOURCES.keys())}")
        return 2

    start = dt.datetime.now()
    print(f"Hampton Roads ETL — run started {start.isoformat(timespec='seconds')}")
    if only:
        print(f"  --only filter: {','.join(sorted(only))}")
    if skip:
        print(f"  --skip filter: {','.join(sorted(skip))}")

    try:
        for short, (display, fn, tables) in SOURCES.items():
            if only and short not in only:
                continue
            if short in skip:
                print(f"\n=== {display} === SKIPPED")
                continue
            _run(display, fn, *tables)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Sources that finished before this point "
              "are already committed to hampton_roads.db.")
        return 130

    elapsed = (dt.datetime.now() - start).total_seconds()
    print(f"\nDone in {elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
