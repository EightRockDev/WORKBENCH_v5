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
    assert lines == [
        "  2026-09-29T13:14:02  went to sleep",
        "  2026-09-28T17:31:40  UNEXPECTED shutdown (crash or power loss)",
    ]


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
