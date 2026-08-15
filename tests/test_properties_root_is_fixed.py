"""The deal folder location is a constant, not a search.

Owner directive, 2026-08-15: "Don't choose anything - pick a folder - in the
directory I told you - and write to it. Every Time. Don't guess."

Every clever version of this failed differently. A path computed relative to
the app broke when the app moved into WORKBENCH_V5, silently severing the
curated sale history and leaving the app to infer sales from county records.
A first-match search then picked a stray Properties folder holding one junk
directory over the OneDrive root holding every real deal. A constant cannot
do either.
"""

from __future__ import annotations

from pathlib import Path

import data.property_io as pio


def test_properties_root_is_inside_the_app_folder():
    app_root = Path(pio.__file__).resolve().parent.parent
    assert pio.PROPERTIES_ROOT == app_root / "Properties"


def test_the_folder_exists_so_writes_never_fail_for_a_missing_dir():
    assert pio.PROPERTIES_ROOT.is_dir()


def test_no_discovery_function_remains():
    """If someone reintroduces a search, this is the tripwire."""
    assert not hasattr(pio, "_discover_properties_root"), (
        "deal-folder location must stay a constant - see the owner directive "
        "in property_io")


def test_env_override_is_still_honoured(monkeypatch, tmp_path):
    """Explicit configuration is not guessing, so the override stays."""
    import importlib
    monkeypatch.setenv("ER_PROPERTIES_ROOT", str(tmp_path / "Deals"))
    reloaded = importlib.reload(pio)
    try:
        assert reloaded.PROPERTIES_ROOT == tmp_path / "Deals"
    finally:
        monkeypatch.delenv("ER_PROPERTIES_ROOT", raising=False)
        importlib.reload(pio)
