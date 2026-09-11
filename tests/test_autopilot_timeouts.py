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
