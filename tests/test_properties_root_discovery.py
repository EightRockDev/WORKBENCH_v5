"""v5 looked for the deal folders in a different place than v2 did.

Owner, 2026-08-15: "this worked fine in V2. Why doesn't it work now?"

Both versions compute the deal-folder location as "one directory above the
app". That rule is RELATIVE, so moving the app moves the answer - v2 in one
folder and v5 in WORKBENCH_V5 resolve to two different Properties
directories. The curated `sales.json` files - the only hand-verified sale
history there is - stayed where v2 left them, so v5 saw nothing and fell back
to inferring sales from county records.
"""

from __future__ import annotations

from data.property_io import _discover_properties_root


def _mk(root, *parts):
    p = root.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_prefers_the_classic_sibling_when_it_has_deals(tmp_path):
    app = _mk(tmp_path, "WORKBENCH_V5")
    classic = _mk(tmp_path, "Properties")
    _mk(classic, "Crossroads-Townhomes-26-Norfolk")
    assert _discover_properties_root(app) == classic


def test_falls_through_an_empty_sibling_to_the_previous_install(tmp_path):
    """The exact production shape: v5's sibling exists but is empty, while
    v2's still holds every deal folder."""
    app = _mk(tmp_path, "WORKBENCH_V5")
    _mk(tmp_path, "Properties")                    # exists, EMPTY
    legacy = _mk(tmp_path, "WORKBENCH", "Properties")
    _mk(legacy, "Crossroads-Townhomes-26-Norfolk")
    assert _discover_properties_root(app) == legacy


def test_ignores_config_only_folders(tmp_path):
    """A Properties dir holding only _favorites.json is not the real one."""
    app = _mk(tmp_path, "WORKBENCH_V5")
    classic = _mk(tmp_path, "Properties")
    (classic / "_favorites.json").write_text("{}")
    legacy = _mk(tmp_path, "python_workbench", "Properties")
    _mk(legacy, "Oasis-216-Virginia-Beach")
    assert _discover_properties_root(app) == legacy


def test_falls_back_to_classic_when_nothing_exists(tmp_path):
    """A fresh machine must behave exactly as before - no surprises."""
    app = _mk(tmp_path, "WORKBENCH_V5")
    assert _discover_properties_root(app) == tmp_path / "Properties"
