"""Listings step (autopilot): scrape favorites' asking rents so the
rent-delta gate has REAL market rents, not just the FMR floor."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import listings_pull  # noqa: E402


def main() -> int:
    """Never fails the cycle - but the failure must be VISIBLE and RETRIED.

    An uncaught exception here both failed the cycle (contradicting the
    intent below) and left the previous success stamp in place, so the next
    seven days of cycles skipped the step entirely.
    """
    try:
        listings_pull.pull_listings()
    except Exception as e:
        import traceback
        print(f"  [listings] FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        # Clear the freshness claim so the NEXT cycle retries instead of
        # reporting "fresh - skipping" over a step that never ran.
        listings_pull.invalidate_freshness()
        print("  [listings] freshness cleared - the next cycle will retry")
    return 0   # reports itself; never fails the cycle


if __name__ == "__main__":
    raise SystemExit(main())
