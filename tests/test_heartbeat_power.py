"""The heartbeat's power history: the first cycle after an outage must
say whether the office machine rebooted, slept, crashed or was signed
out - the 2026-09-28/29 silences could not be told apart remotely."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """Event[0]:
  Log Name: System
  Source: Microsoft-Windows-Kernel-Power
  Date: 2026-09-29T13:14:02.1230000Z
  Event ID: 42
  Task: N/A
  Description:
The system is entering sleep.

Event[1]:
  Log Name: System
  Source: EventLog
  Date: 2026-09-28T17:31:40.0000000Z
  Event ID: 6008
  Description:
The previous system shutdown was unexpected.

Event[2]:
  Log Name: System
  Source: User32
  Date: 2026-09-29T13:34:26.0000000Z
  Event ID: 1074
  Description:
The process C:\\Windows\\Explorer.EXE (DESKTOP-RINL8AD) has initiated the restart of computer DESKTOP-RINL8AD on behalf of user DESKTOP\\brian for the following reason: Other (Unplanned)

Event[3]:
  Log Name: System
  Source: Service Control Manager
  Date: 2026-09-28T17:30:00.0000000Z
  Event ID: 1
  Description:
Not a power event - different provider, same id.
"""


def _mod():
    spec = importlib.util.spec_from_file_location(
        "heartbeat", ROOT / "scripts" / "heartbeat.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["heartbeat"] = m
    spec.loader.exec_module(m)
    return m


def test_parses_the_power_events_and_ignores_lookalikes():
    m = _mod()
    lines = m.parse_events(SAMPLE)
    assert lines[:3] == [
        "  2026-09-29T13:14:02  went to sleep",
        "  2026-09-28T17:31:40  UNEXPECTED shutdown (crash or power loss)",
        "  2026-09-29T13:34:26  shutdown/restart requested",
    ]
    assert "Explorer.EXE" in lines[3] and "on behalf of user" in lines[3]
    assert len(lines) == 4


def test_event_log_read_is_time_limited_and_never_raises(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m.os, "name", "nt")
    seen = {}

    def boom(*a, **k):
        seen["timeout"] = k.get("timeout")
        raise OSError("wevtutil missing")
    monkeypatch.setattr(m.subprocess, "run", boom)
    out = m.power_events()
    assert seen["timeout"], "the event-log read must have a timeout"
    assert "could not read the event log" in out[0]


def test_heartbeat_always_exits_zero_even_if_history_fails(monkeypatch,
                                                            capsys):
    m = _mod()

    def broken():
        raise RuntimeError("nope")
    monkeypatch.setattr(m, "power_events", broken)
    assert m.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("ALIVE")
    assert "power history : unavailable" in out


def test_task_status_is_time_limited_and_never_raises(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m.os, "name", "nt")
    seen = {}

    def boom(*a, **k):
        seen["timeout"] = k.get("timeout")
        raise OSError("schtasks missing")
    monkeypatch.setattr(m.subprocess, "run", boom)
    out = m.task_status()
    assert seen["timeout"]
    assert "could not query the task" in out[0]


def test_task_status_keeps_the_lines_that_matter(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m.os, "name", "nt")
    listing = (b"HostName:      DESKTOP-RINL8AD\r\n"
               b"TaskName:      \\EightRockWorkbenchAutopilot\r\n"
               b"Next Run Time: 10/1/2026 4:00:00 AM\r\n"
               b"Status:        Running\r\n"
               b"Logon Mode:    Interactive only\r\n"
               b"Last Run Time: 10/1/2026 3:00:02 AM\r\n"
               b"Last Result:   267009\r\n"
               b"Author:        DESKTOP\\brian\r\n")

    class P:
        stdout = listing
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: P())
    out = "\n".join(m.task_status())
    assert "Next Run Time: 10/1/2026 4:00:00 AM" in out
    assert "Last Result: 267009" in out
    assert "Interactive only" in out
    assert "Author" not in out and "HostName" not in out
