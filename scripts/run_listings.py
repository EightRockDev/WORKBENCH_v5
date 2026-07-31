"""Listings step (autopilot): scrape favorites' asking rents so the
rent-delta gate has REAL market rents, not just the FMR floor."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import listings_pull  # noqa: E402


def main() -> int:
    listings_pull.pull_listings()
    return 0   # reports itself; never fails the cycle


if __name__ == "__main__":
    raise SystemExit(main())
