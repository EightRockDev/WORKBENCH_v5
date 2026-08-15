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


def test_finds_the_onedrive_sharepoint_sync_root(tmp_path, monkeypatch):
    """The real location (owner, 2026-08-15):
        C:\\Users\\<user>\\<Org>\\<Org> - Documents\\Properties
    A OneDrive/SharePoint sync root. No rule based on the app's install
    location can reach it, which is why every candidate came up empty and
    the app fell back to inferring sale history from county records."""
    home = tmp_path / "Users" / "brian2"
    home.mkdir(parents=True)
    app = tmp_path / "WORKBENCH_V5"
    app.mkdir()
    (tmp_path / "Properties").mkdir()          # the empty sibling v5 used
    real = (home / "Eight Rock Capital Partners"
            / "Eight Rock Capital Partners - Documents" / "Properties")
    (real / "Grand-Hampton-at-Langley-136-Hampton").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: home))
    assert _discover_properties_root(app) == real


def test_org_rename_does_not_break_it(tmp_path, monkeypatch):
    """Globbed, not hardcoded - renaming the org folder must not undo this."""
    home = tmp_path / "Users" / "b"
    home.mkdir(parents=True)
    app = tmp_path / "WORKBENCH_V5"
    app.mkdir()
    real = home / "Some Other Org" / "Some Other Org - Documents" / "Properties"
    (real / "A-Deal-10-Norfolk").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: home))
    assert _discover_properties_root(app) == real


def test_a_decoy_folder_does_not_beat_the_real_one(tmp_path, monkeypatch):
    """Order-based selection let a stray Properties folder containing one
    junk directory win over the OneDrive root holding every real deal - so
    the app read no curated sale history and fell back to county records
    (owner, 2026-08-15: still empty after the move). Score by how many
    sales.json files a candidate actually holds."""
    home = tmp_path / "Users" / "brian2"
    home.mkdir(parents=True)
    app = tmp_path / "WORKBENCH_V5"
    app.mkdir()
    decoy = tmp_path / "Properties"
    (decoy / "Archive").mkdir(parents=True)          # 1 folder, 0 sales.json
    real = (home / "Eight Rock Capital Partners"
            / "Eight Rock Capital Partners - Documents" / "Properties")
    for name in ("Grand-Hampton-at-Langley-136-Hampton",
                 "Crossroads-Townhomes-26-Norfolk"):
        (real / name).mkdir(parents=True)
        (real / name / "sales.json").write_text("[]")
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: home))
    assert _discover_properties_root(app) == real
