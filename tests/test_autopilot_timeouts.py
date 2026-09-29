"""Discovery steps get a short leash - a wedged external portal ate the
03:00 and 04:00 cycles twice (2026-09-04, 2026-09-11) because two
back-to-back 1-hour timeouts overlap the next scheduled cycle."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "autopilot_run", ROOT / "scripts" / "autopilot_run.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["autopilot_run"] = m
    spec.loader.exec_module(m)
    return m


def test_discovery_steps_have_a_short_timeout():
    m = _mod()
    for step in ("discover", "discover_national", "salesdiscovery"):
        assert m.STEP_TIMEOUTS[step] <= 900, step
    # and the data-path steps keep the full hour
    assert "pull" not in m.STEP_TIMEOUTS
    assert "arcgissales" not in m.STEP_TIMEOUTS


def test_every_capped_step_actually_exists():
    m = _mod()
    names = {name for name, _a, _o in m.STEPS}
    assert set(m.STEP_TIMEOUTS) <= names


def _stage1():
    spec = importlib.util.spec_from_file_location(
        "autopilot_stage1", ROOT / "scripts" / "autopilot.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["autopilot_stage1"] = m
    spec.loader.exec_module(m)
    return m


def _hang(*a, **k):
    import subprocess
    assert k.get("timeout"), "git must never run without a timeout"
    raise subprocess.TimeoutExpired(a[0], k["timeout"])


def test_stage2_git_times_out_instead_of_wedging(monkeypatch):
    """2026-09-28: the chain went silent after the 17:26 publish. A git
    push over a stalled connection blocks forever with no timeout, and
    Task Scheduler stands down every later cycle while it 'runs'."""
    m = _mod()
    monkeypatch.setattr(m.subprocess, "run", _hang)
    r = m.git("push", "origin", "main")
    assert r.returncode == 124
    assert "timed out" in r.stderr


def test_stage1_git_times_out_instead_of_wedging(monkeypatch):
    """Stage 1 runs BEFORE the auto-update, so a hang there can never be
    fixed by pushing code - it must fail visibly on its own."""
    m = _stage1()
    monkeypatch.setattr(m.subprocess, "run", _hang)
    r = m.git("fetch", "origin")
    assert r.returncode == 124
    assert "timed out" in r.stderr


def test_stage1_main_reports_a_hung_fetch_and_exits_nonzero(monkeypatch,
                                                            capsys):
    m = _stage1()
    monkeypatch.setattr(m.subprocess, "run", _hang)
    assert m.main() == 1
    assert "fetch failed" in capsys.readouterr().out
