"""Autopilot step: national top-50 metro feed discovery, runs EVERY cycle by default (owner: attack).

Owner directive 2026-08-09: discover parcel feeds for the top-50 metros (free
sources). Discovery probes ~44 metros' ArcGIS/Socrata portals - cheap metadata,
but too much to hammer hourly - so this runs at most once every
ER_DISCOVERY_EVERY_DAYS (default 0 = every cycle). It writes candidates to
data/feeds_extra.json (which the pull step ingests); a metro only BUILDS into
the backbone once its FIPS is added in core/market_data.py, so nothing is
blindly activated. Host-only (build env firewalled).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVERY_DAYS = int(os.environ.get("ER_DISCOVERY_EVERY_DAYS", "0"))
_STAMP = ROOT / "reports" / ".national-discovery-stamp"


def _fresh(now: dt.datetime) -> bool:
    # EVERY_DAYS<=0 -> run EVERY cycle (owner directive 2026-08-09: attack,
    # don't throttle). Set ER_DISCOVERY_EVERY_DAYS>0 only if AGOL starts
    # rate-limiting - a ban would stop the fetch entirely.
    if EVERY_DAYS <= 0:
        return False
    try:
        last = dt.datetime.fromisoformat(_STAMP.read_text().strip())
    except (OSError, ValueError):
        return False
    return (now - last) < dt.timedelta(days=EVERY_DAYS)


def main() -> int:
    now = dt.datetime.now()
    if _fresh(now) and not os.environ.get("ER_DISCOVERY_FORCE"):
        print(f"[national-discovery] ran within {EVERY_DAYS}d - skipping "
              "(ER_DISCOVERY_FORCE=1 to override)")
        return 0
    from scripts import discover_feeds
    print("[national-discovery] probing top-50 metros for free parcel feeds...")
    rc = discover_feeds.main([])          # national default; writes feeds_extra
    try:
        _STAMP.parent.mkdir(exist_ok=True)
        _STAMP.write_text(now.isoformat())
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
