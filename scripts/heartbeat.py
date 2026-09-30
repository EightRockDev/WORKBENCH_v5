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
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Why did the LAST cycle stop? 2026-09-28 and 09-29 the chain went silent
# mid-cycle twice, and from the outside a shutdown, a sleep, a sign-out
# and a hung step all look identical. The first heartbeat after a restart
# answers it from the machine's own records: uptime (a reboot resets it)
# plus the System log's power/session events for the last 3 days.
_POWER_EVENTS = {
    ("User32", "1074"): "shutdown/restart requested",
    ("EventLog", "6006"): "clean shutdown",
    ("EventLog", "6008"): "UNEXPECTED shutdown (crash or power loss)",
    ("Microsoft-Windows-Kernel-Power", "41"):
        "rebooted without a clean shutdown",
    ("Microsoft-Windows-Kernel-Power", "42"): "went to sleep",
    ("Microsoft-Windows-Power-Troubleshooter", "1"): "woke from sleep",
    ("Microsoft-Windows-Winlogon", "7001"): "user signed in",
    ("Microsoft-Windows-Winlogon", "7002"): "user signed out",
}
_QUERY = ("*[System[(EventID=1074 or EventID=6006 or EventID=6008 or "
          "EventID=41 or EventID=42 or EventID=1 or EventID=7001 or "
          "EventID=7002) and TimeCreated[timediff(@SystemTime) <= "
          "259200000]]]")


def uptime_text() -> str:
    if os.name != "nt":
        return "n/a (not Windows)"
    try:
        import ctypes
        ms = ctypes.windll.kernel32.GetTickCount64()
        ms = int(ms)
    except Exception as exc:
        return f"unknown ({type(exc).__name__})"
    boot = datetime.datetime.now() - datetime.timedelta(milliseconds=ms)
    hours = ms / 3_600_000
    return f"{hours:.1f} h (booted {boot.isoformat(timespec='minutes')})"


def parse_events(text: str) -> list[str]:
    """wevtutil /f:text blocks -> one plain line per power/session event
    we recognise, newest first as wevtutil returns them."""
    out: list[str] = []
    for block in text.split("Event["):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, val = line.strip().partition(":")
            if sep and key in ("Date", "Source", "Event ID") \
                    and key not in fields:
                fields[key] = val.strip()
        what = _POWER_EVENTS.get(
            (fields.get("Source", ""), fields.get("Event ID", "")))
        if what:
            out.append(f"  {fields.get('Date', '?')[:19]}  {what}")
    return out


def power_events() -> list[str]:
    if os.name != "nt":
        return ["  n/a (not Windows)"]
    try:
        proc = subprocess.run(
            ["wevtutil", "qe", "System", f"/q:{_QUERY}", "/c:30",
             "/rd:true", "/f:text"],
            capture_output=True, timeout=60)
    except Exception as exc:
        return [f"  could not read the event log ({type(exc).__name__})"]
    text = proc.stdout.decode("utf-8", errors="replace")
    lines = parse_events(text)
    return lines[:15] or ["  none recorded in the last 3 days"]


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
    try:
        print(f"uptime        : {uptime_text()}")
        print("power/session events, last 3 days (newest first):")
        for line in power_events():
            print(line)
    except Exception as exc:
        print(f"power history : unavailable ({type(exc).__name__})")
    print()
    print("If the timestamp above is more than ~2 hours old, the autopilot")
    print("chain is DOWN - run fix-autopilot-task.bat on the office machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
