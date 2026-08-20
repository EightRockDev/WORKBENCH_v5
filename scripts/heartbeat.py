"""First step of every cycle: prove, on GitHub, that the chain is alive.

Five days of dead autopilot (2026-08-15..19) were discovered by their
silence: every failure before the cycle's first publish was written to a
local file on the office machine, and the remote view simply stopped
changing - indistinguishable from "machine off", "network down", or
"nothing to report". Four wrong diagnoses were made from that silence.

This step runs FIRST and produces a one-screen liveness record that the
existing per-step publish pushes within seconds of the cycle starting.
From the outside the rule becomes simple: a heartbeat older than ~2 hours
means the chain is down, full stop - no inference from absence required.

Always exits 0. A heartbeat that could fail would poison the very signal
it exists to provide.
"""

from __future__ import annotations

import datetime
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        import config
        version = getattr(config, "WORKBENCH_VERSION", "unknown")
    except Exception:
        version = "unknown"

    now = datetime.datetime.now().astimezone()
    print("ALIVE")
    print(f"cycle started : {now.isoformat(timespec='seconds')}")
    print(f"version       : {version}")
    print(f"host          : {platform.node()}")
    print()
    print("If the timestamp above is more than ~2 hours old, the autopilot")
    print("chain is DOWN - run fix-autopilot-task.bat on the office machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
