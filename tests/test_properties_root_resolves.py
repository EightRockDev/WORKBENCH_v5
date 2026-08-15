"""The app must read deal folders from the directory it writes them to.

This is the bug that ate 2026-08-15, and every test in the suite walked
straight past it, because each half was individually correct:

  * `data.property_io` composed storage keys relative to `_WB_ROOT`
    (the parent of `PROPERTIES_ROOT`), and
  * `core.storage.LocalDiskStorage` resolved those keys against a root of
    `Path(__file__).parent.parent.parent`.

Under the v1 layout — `<root>/python_workbench/core/storage.py` — those two
named the same directory. Under v5 — `<app>/core/storage.py` — the second is
one level ABOVE the app. Every `Properties/...` key resolved to
`C:\\Properties` instead of `C:\\WORKBENCH_V5\\Properties`.

Nothing raised. `discover_property_folders()` returned `[]` from a directory
that did not exist, no property ever matched a folder, no curated
`sales.json` was ever opened, and Sale History quietly fell back to county
records and blamed a nightly data feed. Four releases were spent fixing
things that were not broken.

The tests below check the round trip — write through the app's own API, read
it back through the app's own API — because that is the only assertion that
could not have passed while the app was broken.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import data.property_io as pio
from core.storage import LocalDiskStorage


def test_storage_root_matches_property_io():
    """One module composes the keys the other resolves. If these two ever
    disagree again, every deal-folder read silently reads nowhere."""
    assert LocalDiskStorage().root == pio._WB_ROOT.resolve(), (
        "the local storage root and property_io's workbench root name "
        "different directories — deal folders will read from a path that "
        "does not exist"
    )


def test_the_properties_key_resolves_to_the_properties_folder():
    key = pio._rel(pio.PROPERTIES_ROOT)
    resolved = LocalDiskStorage()._resolve(key)
    assert Path(resolved) == pio.PROPERTIES_ROOT.resolve(), (
        f"key {key!r} resolves to {resolved}, not {pio.PROPERTIES_ROOT}"
    )


@pytest.fixture()
def app_like(tmp_path, monkeypatch):
    """An app root laid out like the real one, wired end to end."""
    app = tmp_path / "WORKBENCH_V5"
    props = app / "Properties"
    props.mkdir(parents=True)

    monkeypatch.setattr(pio, "PROPERTIES_ROOT", props)
    monkeypatch.setattr(pio, "_WB_ROOT", app)
    monkeypatch.setenv("ER_LOCAL_ROOT", str(app))

    import core.storage as storage_mod
    monkeypatch.setattr(storage_mod, "_storage", None, raising=False)
    monkeypatch.setattr(storage_mod, "get_storage",
                        lambda: LocalDiskStorage(app))
    return props


def test_a_deal_folder_on_disk_is_discovered(app_like):
    """The end-to-end assertion. A folder placed in the Properties
    directory must come back from discover_property_folders()."""
    deal = app_like / "Grand-Hampton-at-Langley-136-Hampton"
    deal.mkdir()
    (deal / "sales.json").write_text("[]", encoding="utf-8")

    found = pio.discover_property_folders(app_like)
    assert [f.folder_name for f in found] == [
        "Grand-Hampton-at-Langley-136-Hampton"]
    assert found[0].has_sales is True


def test_the_curated_sale_history_is_actually_read(app_like):
    """What the owner was looking at. The sale card reads sales.json through
    load_sales(); if the path plumbing is wrong this returns None and the
    card falls through to county records."""
    deal = app_like / "Grand-Hampton-at-Langley-136-Hampton"
    deal.mkdir()
    rows = [{"date": "2019-06-04", "price": 21500000,
             "grantor": "Seller LLC", "grantee": "Buyer LLC"}]
    (deal / "sales.json").write_text(json.dumps(rows), encoding="utf-8")

    loaded = pio.load_sales(deal)
    assert loaded == rows, "the curated sale history was not read back"


def test_the_property_on_the_screenshot_matches_its_folder(app_like):
    """Grand Hampton at Langley: the record says 192 units, the folder says
    136. The unit count must not be required for the match, or a folder
    named before a re-count is unreachable forever."""
    deal = app_like / "Grand-Hampton-at-Langley-136-Hampton"
    deal.mkdir()
    (deal / "sales.json").write_text("[]", encoding="utf-8")

    prop = {"name": "Grand Hampton at Langley", "units": 192,
            "city": "Hampton", "address": "611 Michigan Dr"}
    match = pio.find_folder_for_property(
        prop, pio.discover_property_folders(app_like))
    assert match is not None, "the deal folder did not match its property"
    assert match.folder_name == "Grand-Hampton-at-Langley-136-Hampton"


def test_a_deal_folder_outside_the_app_still_reads(tmp_path, monkeypatch):
    """ER_PROPERTIES_ROOT can point at another drive. The key for a path
    outside the storage root must go through as absolute, not be rewritten
    into a same-named folder under the app."""
    app = tmp_path / "WORKBENCH_V5"
    (app / "Properties").mkdir(parents=True)
    elsewhere = tmp_path / "D_drive" / "Deals"
    deal = elsewhere / "Grand-Hampton-at-Langley-136-Hampton"
    deal.mkdir(parents=True)
    (deal / "sales.json").write_text('[{"price": 1}]', encoding="utf-8")

    import core.storage as storage_mod
    monkeypatch.setattr(storage_mod, "get_storage",
                        lambda: LocalDiskStorage(app))
    monkeypatch.setattr(pio, "_WB_ROOT", app)

    assert pio.load_sales(deal) == [{"price": 1}]
    assert pio.discover_property_folders(elsewhere)[0].has_sales is True
