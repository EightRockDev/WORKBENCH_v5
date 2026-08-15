"""The fixed deal-folder location has to actually contain the deal folders.

V5.57 pinned `PROPERTIES_ROOT` to one directory inside the app folder. That
was right, and it was the owner's call — but the folders themselves were
still in the OneDrive sync root, so the app read an empty directory, found
no folder for Grand Hampton at Langley, and told the owner the nightly data
pull had not landed Hampton's transfer records. His curated `sales.json` was
sitting on disk the whole time, unread.

These pin the seeding behaviour that closes that gap, and in particular the
limits on it: it fills an EMPTY destination, it never overwrites, and it
never touches the source.
"""

from __future__ import annotations

import json
from pathlib import Path

from data.property_seed import _MARKER, candidate_sources, seed_if_empty


def _make_deal(root: Path, name: str, *, sales: bool = True) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "deal.json").write_text("{}", encoding="utf-8")
    if sales:
        (folder / "sales.json").write_text(
            json.dumps([{"date": "2019-06-04", "price": 21500000,
                         "grantor": "Seller LLC", "grantee": "Buyer LLC"}]),
            encoding="utf-8")
    return folder


def test_seeds_an_empty_destination_from_the_folder_that_has_the_data(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    source = home / "Eight Rock" / "Eight Rock - Documents" / "Properties"
    source.mkdir(parents=True)
    _make_deal(source, "Grand-Hampton-at-Langley-136-Hampton")
    _make_deal(source, "Driftwood-140-Virginia-Beach", sales=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    target = tmp_path / "WORKBENCH_V5" / "Properties"
    copied, used = seed_if_empty(target)

    assert copied == 2
    assert used is not None and used.resolve() == source.resolve()
    landed = target / "Grand-Hampton-at-Langley-136-Hampton" / "sales.json"
    assert landed.is_file(), "the curated sale history did not arrive"
    assert json.loads(landed.read_text())[0]["price"] == 21500000


def test_the_source_is_only_read_never_moved(tmp_path, monkeypatch):
    """A move that half-fails takes the only copy of hand-verified sale
    history with it. Copy, always."""
    home = tmp_path / "home"
    source = home / "Org" / "Org - Documents" / "Properties"
    source.mkdir(parents=True)
    _make_deal(source, "Grand-Hampton-at-Langley-136-Hampton")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    seed_if_empty(tmp_path / "app" / "Properties")

    still_there = source / "Grand-Hampton-at-Langley-136-Hampton" / "sales.json"
    assert still_there.is_file(), "seeding moved or deleted the owner's data"


def test_a_populated_destination_is_never_touched(tmp_path, monkeypatch):
    """Once deal folders live at the destination, it is the truth. Copying
    a stale outside version over a curated one would be data loss."""
    home = tmp_path / "home"
    source = home / "Org" / "Org - Documents" / "Properties"
    source.mkdir(parents=True)
    _make_deal(source, "Grand-Hampton-at-Langley-136-Hampton")
    (source / "Grand-Hampton-at-Langley-136-Hampton" / "sales.json").write_text(
        json.dumps([{"date": "1999-01-01", "price": 1}]), encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    target = tmp_path / "app" / "Properties"
    kept = _make_deal(target, "Grand-Hampton-at-Langley-136-Hampton")
    (kept / "sales.json").write_text(
        json.dumps([{"date": "2019-06-04", "price": 21500000}]),
        encoding="utf-8")

    copied, used = seed_if_empty(target)

    assert (copied, used) == (0, None)
    kept_rows = json.loads((kept / "sales.json").read_text())
    assert kept_rows[0]["price"] == 21500000, "destination data was clobbered"


def test_seeding_happens_once(tmp_path, monkeypatch):
    """The marker means a normal startup does no filesystem walking, and a
    folder the owner later deletes on purpose stays deleted."""
    home = tmp_path / "home"
    source = home / "Org" / "Org - Documents" / "Properties"
    source.mkdir(parents=True)
    _make_deal(source, "Grand-Hampton-at-Langley-136-Hampton")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    target = tmp_path / "app" / "Properties"
    assert seed_if_empty(target)[0] == 1
    assert (target / _MARKER).is_file()

    # The owner deletes it deliberately; seeding must not resurrect it.
    import shutil
    shutil.rmtree(target / "Grand-Hampton-at-Langley-136-Hampton")
    assert seed_if_empty(target) == (0, None)
    assert not (target / "Grand-Hampton-at-Langley-136-Hampton").exists()


def test_the_folder_holding_real_sale_history_wins_over_a_decoy(
        tmp_path, monkeypatch):
    """A second sync copy, or an empty scaffold, must not outrank the folder
    that actually carries sales.json."""
    home = tmp_path / "home"
    decoy = home / "AAA" / "Properties"          # sorts first, holds nothing
    decoy.mkdir(parents=True)
    _make_deal(decoy, "Empty-Scaffold-1-Nowhere", sales=False)
    real = home / "Org" / "Org - Documents" / "Properties"
    real.mkdir(parents=True)
    _make_deal(real, "Grand-Hampton-at-Langley-136-Hampton")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    target = tmp_path / "app" / "Properties"
    ranked = candidate_sources(target)
    assert ranked and ranked[0].resolve() == real.resolve()


def test_no_source_anywhere_is_not_an_error(tmp_path, monkeypatch):
    """A machine with no deal folders yet must still start the app."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    assert seed_if_empty(tmp_path / "app" / "Properties") == (0, None)


def test_root_level_config_files_come_across(tmp_path, monkeypatch):
    """_favorites.json and friends sit at the root of Properties/, not in a
    deal folder — leaving them behind silently drops every starred deal."""
    home = tmp_path / "home"
    source = home / "Org" / "Org - Documents" / "Properties"
    source.mkdir(parents=True)
    _make_deal(source, "Grand-Hampton-at-Langley-136-Hampton")
    (source / "_favorites.json").write_text('["134263"]', encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    target = tmp_path / "app" / "Properties"
    seed_if_empty(target)
    assert (target / "_favorites.json").read_text() == '["134263"]'
