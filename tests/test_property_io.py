"""Tests for data.property_io — property folder discovery + deal state I/O.

Strategy:
  - Pydantic model tests pin the legacy `s-*` alias mapping and the migration
    shim for stale `s-amf` values.
  - tmp_path-based fixtures cover load/save round-tripping and missing-file
    behavior without touching the real Properties/ folder.
  - One smoke test against the real Properties/ folder confirms we can read
    Brian's actual data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data.property_io import (
    PROPERTIES_ROOT,
    DealState,
    PropertyFolder,
    discover_property_folders,
    load_custom_props,
    load_deal,
    load_favorites,
    load_notes,
    load_sales,
    load_sources,
    save_deal,
    save_notes,
)


# ---------------------------------------------------------------------------
# DealState — load via legacy s-* aliases
# ---------------------------------------------------------------------------


def _legacy_deal_dict() -> dict:
    """Mirrors the shape of an actual Dove Landing deal.json on disk."""
    return {
        "s-pp": 46_300_000,
        "s-noi": 3_252_500,
        "s-dp": 30,
        "s-ir": 6.5,
        "s-vac": 7,
        "s-rg": 3,
        "s-eg": 3,
        "s-xc": 7.5,
        "s-hp": 10,
        "s-am": 25,
        "s-io": 0,
        "s-amf": 4,
    }


def test_load_legacy_aliases_correctly():
    deal = DealState.model_validate(_legacy_deal_dict())
    assert deal.pp == 46_300_000
    assert deal.noi == 3_252_500
    assert deal.dp == 30
    assert deal.ir == 6.5
    assert deal.amf == 4
    assert deal.io == 0


def test_legacy_file_supplies_defaults_for_new_fields():
    """Legacy files don't have `raise_amount` or `vacancy_source` — defaults apply."""
    deal = DealState.model_validate(_legacy_deal_dict())
    assert deal.raise_amount is None
    assert deal.vacancy_source == "record"


def test_loads_with_field_names_too():
    """populate_by_name allows loading with non-aliased names."""
    raw = {
        "pp": 1_000_000, "noi": 70_000, "dp": 30, "ir": 6.5, "vac": 7,
        "rg": 3, "eg": 3, "xc": 7.5, "hp": 5,
    }
    deal = DealState.model_validate(raw)
    assert deal.pp == 1_000_000


# ---------------------------------------------------------------------------
# Percentage → fraction helpers
# ---------------------------------------------------------------------------


def test_fraction_helpers_convert_percent():
    deal = DealState.model_validate(_legacy_deal_dict())
    assert deal.down_payment_frac == pytest.approx(0.30)
    assert deal.interest_rate == pytest.approx(0.065)
    assert deal.vacancy_frac == pytest.approx(0.07)
    assert deal.rent_growth == pytest.approx(0.03)
    assert deal.expense_growth == pytest.approx(0.03)
    assert deal.exit_cap == pytest.approx(0.075)
    assert deal.am_fee_pct == pytest.approx(0.04)


def test_loan_amount_computed_from_pp_minus_dp():
    deal = DealState.model_validate(_legacy_deal_dict())
    # $46.3M × (1 - 0.30) = $32,410,000
    assert deal.loan_amount == pytest.approx(32_410_000)


def test_equity_raise_defaults_to_down_payment_dollars():
    """When raise_amount is None, equity_raise = pp × dp/100."""
    deal = DealState.model_validate(_legacy_deal_dict())
    # $46.3M × 30% = $13,890,000
    assert deal.equity_raise == pytest.approx(13_890_000)


def test_equity_raise_uses_explicit_raise_amount_when_set():
    """When raise_amount is set, equity_raise returns it (not pp × dp/100)."""
    raw = _legacy_deal_dict() | {"raise_amount": 15_000_000}
    deal = DealState.model_validate(raw)
    assert deal.equity_raise == 15_000_000


def test_equity_raise_ignores_zero_or_negative_raise_amount():
    """Sentinel values fall back to the down-payment-dollars default."""
    raw = _legacy_deal_dict() | {"raise_amount": 0}
    deal = DealState.model_validate(raw)
    assert deal.equity_raise == pytest.approx(13_890_000)


# ---------------------------------------------------------------------------
# Migration shim for stale s-amf
# ---------------------------------------------------------------------------


def test_amf_migration_resets_stale_dollar_value_to_4_percent():
    """Crossroads-29 has `s-amf: 15000` (legacy $0-50k slider) → reset to 4%."""
    raw = _legacy_deal_dict() | {"s-amf": 15000}
    deal = DealState.model_validate(raw)
    assert deal.amf == 4.0


def test_amf_migration_preserves_valid_percent_values():
    """Values 0-5 are valid new-format percents and should pass through."""
    for v in (0, 2.5, 4, 5):
        raw = _legacy_deal_dict() | {"s-amf": v}
        deal = DealState.model_validate(raw)
        assert deal.amf == v


# ---------------------------------------------------------------------------
# Validation bounds
# ---------------------------------------------------------------------------


def test_dp_above_50_rejected():
    raw = _legacy_deal_dict() | {"s-dp": 60}
    with pytest.raises(ValidationError):
        DealState.model_validate(raw)


def test_ir_above_12_rejected():
    raw = _legacy_deal_dict() | {"s-ir": 15}
    with pytest.raises(ValidationError):
        DealState.model_validate(raw)


def test_io_above_10_rejected():
    raw = _legacy_deal_dict() | {"s-io": 15}
    with pytest.raises(ValidationError):
        DealState.model_validate(raw)


# ---------------------------------------------------------------------------
# Folder discovery
# ---------------------------------------------------------------------------


def _make_folder(parent: Path, name: str, files: dict[str, str]) -> Path:
    """Build a property folder with the given files."""
    f = parent / name
    f.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (f / fname).write_text(content, encoding="utf-8")
    return f


def test_discover_walks_and_flags_files(tmp_path: Path):
    _make_folder(tmp_path, "Test-Property-100-Norfolk", {
        "deal.json": json.dumps(_legacy_deal_dict()),
        "sources.json": "{}",
        "notes.txt": "hello",
    })
    _make_folder(tmp_path, "Empty-Property-50-Hampton", {})  # no files

    folders = discover_property_folders(tmp_path)
    assert len(folders) == 2
    by_name = {f.folder_name: f for f in folders}
    full = by_name["Test-Property-100-Norfolk"]
    assert full.has_deal is True
    assert full.has_sources is True
    assert full.has_notes is True
    assert full.has_sales is False
    empty = by_name["Empty-Property-50-Hampton"]
    assert empty.has_deal is False
    assert empty.has_sources is False
    assert empty.has_notes is False


def test_discover_skips_files_at_root(tmp_path: Path):
    """Root-level files (`_custom_props.json`, etc.) are not folders."""
    _make_folder(tmp_path, "Real-Property-100-Norfolk", {})
    (tmp_path / "_custom_props.json").write_text("[]")
    (tmp_path / "_favorites.json").write_text("[]")
    folders = discover_property_folders(tmp_path)
    assert len(folders) == 1
    assert folders[0].folder_name == "Real-Property-100-Norfolk"


def test_discover_skips_hidden_dirs(tmp_path: Path):
    _make_folder(tmp_path, ".hidden", {})
    _make_folder(tmp_path, "visible-100-Norfolk", {})
    folders = discover_property_folders(tmp_path)
    assert [f.folder_name for f in folders] == ["visible-100-Norfolk"]


def test_discover_returns_empty_when_root_missing(tmp_path: Path):
    nonexistent = tmp_path / "does-not-exist"
    assert discover_property_folders(nonexistent) == []


# ---------------------------------------------------------------------------
# load_deal / save_deal round-trip
# ---------------------------------------------------------------------------


def test_load_deal_returns_none_when_missing(tmp_path: Path):
    folder = tmp_path / "Empty"
    folder.mkdir()
    assert load_deal(folder) is None


def test_load_deal_parses_legacy_file(tmp_path: Path):
    folder = _make_folder(tmp_path, "P", {
        "deal.json": json.dumps(_legacy_deal_dict()),
    })
    deal = load_deal(folder)
    assert deal is not None
    assert deal.pp == 46_300_000
    assert deal.amf == 4


def test_save_deal_writes_with_legacy_aliases(tmp_path: Path):
    folder = tmp_path / "P"
    folder.mkdir()
    deal = DealState.model_validate(_legacy_deal_dict())
    save_deal(folder, deal)
    raw = json.loads((folder / "deal.json").read_text(encoding="utf-8"))
    # Aliased keys present
    assert "s-pp" in raw
    assert "s-noi" in raw
    # New fields also written
    assert "raise_amount" in raw
    assert "vacancy_source" in raw
    # Field-name keys NOT present (alias-only output)
    assert "pp" not in raw
    assert "noi" not in raw


def test_save_deal_canonical_order(tmp_path: Path):
    """Legacy slider keys come first (in canonical order), new fields after."""
    folder = tmp_path / "P"
    folder.mkdir()
    deal = DealState.model_validate(_legacy_deal_dict())
    save_deal(folder, deal)
    raw_text = (folder / "deal.json").read_text(encoding="utf-8")
    # s-pp should appear before s-noi, which should appear before raise_amount
    assert raw_text.find("s-pp") < raw_text.find("s-noi")
    assert raw_text.find("s-noi") < raw_text.find("raise_amount")


def test_save_then_load_round_trips(tmp_path: Path):
    folder = tmp_path / "P"
    folder.mkdir()
    original = DealState.model_validate(
        _legacy_deal_dict() | {"raise_amount": 15_000_000, "vacancy_source": "user"}
    )
    save_deal(folder, original)
    reloaded = load_deal(folder)
    assert reloaded is not None
    assert reloaded.pp == original.pp
    assert reloaded.raise_amount == 15_000_000
    assert reloaded.vacancy_source == "user"


def test_save_deal_atomic_no_partial_writes(tmp_path: Path):
    """A successful save shouldn't leave behind .tmp files."""
    folder = tmp_path / "P"
    folder.mkdir()
    deal = DealState.model_validate(_legacy_deal_dict())
    save_deal(folder, deal)
    leftover = list(folder.glob("*.tmp"))
    assert leftover == []


# ---------------------------------------------------------------------------
# Other loaders (sources, sales, notes, custom props, favorites)
# ---------------------------------------------------------------------------


def test_load_sources_returns_dict(tmp_path: Path):
    folder = _make_folder(tmp_path, "P", {
        "sources.json": json.dumps({"un": {"value": 100, "source": "record"}}),
    })
    src = load_sources(folder)
    assert src is not None
    assert src["un"]["value"] == 100


def test_load_sources_returns_none_when_missing(tmp_path: Path):
    folder = tmp_path / "P"
    folder.mkdir()
    assert load_sources(folder) is None


def test_load_sales_handles_list_shape(tmp_path: Path):
    """Legacy: sales.json is a flat list of records."""
    sales = [{"date": "2024-01-15", "price": 36_000_000, "grantor": "X", "grantee": "Y", "notes": ""}]
    folder = _make_folder(tmp_path, "P", {"sales.json": json.dumps(sales)})
    assert load_sales(folder) == sales


def test_load_sales_handles_dict_shape(tmp_path: Path):
    """Newer auto-pulled shape: dict with metadata + a `last_3_apartment_sales` list."""
    sales = {"property": "Foo", "last_3_apartment_sales": [{"sale_date": "2024-01-15"}]}
    folder = _make_folder(tmp_path, "P", {"sales.json": json.dumps(sales)})
    loaded = load_sales(folder)
    assert isinstance(loaded, dict)
    assert loaded["property"] == "Foo"


def test_load_notes_returns_empty_string_when_missing(tmp_path: Path):
    folder = tmp_path / "P"
    folder.mkdir()
    assert load_notes(folder) == ""


def test_load_notes_returns_file_content(tmp_path: Path):
    folder = _make_folder(tmp_path, "P", {"notes.txt": "Brian's notes"})
    assert load_notes(folder) == "Brian's notes"


def test_save_notes_round_trip(tmp_path: Path):
    folder = tmp_path / "P"
    folder.mkdir()
    save_notes(folder, "first draft\nsecond line\n")
    assert load_notes(folder) == "first draft\nsecond line\n"


def test_load_custom_props_handles_missing_file(tmp_path: Path):
    assert load_custom_props(tmp_path) == []


def test_load_favorites_handles_missing_file(tmp_path: Path):
    """`load_favorites` returns an empty set (not a list) when the file is missing."""
    assert load_favorites(tmp_path) == set()


def test_load_favorites_normalizes_legacy_int_ids(tmp_path: Path):
    """Legacy HTML workbench saved ints; we now return Set[str]."""
    (tmp_path / "_favorites.json").write_text(json.dumps([133760, 134263, "uuid-abc"]))
    favs = load_favorites(tmp_path)
    assert favs == {"133760", "134263", "uuid-abc"}


def test_toggle_favorite_round_trip(tmp_path: Path):
    """Toggling on/off changes state and persists to disk."""
    from data.property_io import is_favorite, toggle_favorite

    prop = {"property_id": "custom-abc", "name": "Test", "legacy_id": None}

    # Initially not favorited
    assert is_favorite(prop, properties_root=tmp_path) is False

    # Toggle on
    state = toggle_favorite(prop, properties_root=tmp_path)
    assert state is True
    assert is_favorite(prop, properties_root=tmp_path) is True
    saved = load_favorites(tmp_path)
    assert "custom-abc" in saved

    # Toggle off
    state = toggle_favorite(prop, properties_root=tmp_path)
    assert state is False
    assert is_favorite(prop, properties_root=tmp_path) is False


def test_toggle_favorite_clears_legacy_id_match(tmp_path: Path):
    """If a property is favorited under its legacy legacy_id, toggle off clears that too."""
    from data.property_io import is_favorite, toggle_favorite

    # Pre-seed with the legacy legacy_id format
    (tmp_path / "_favorites.json").write_text(json.dumps([134263]))

    prop = {"property_id": "uuid-modern", "legacy_id": "134263", "name": "Foo"}
    # is_favorite matches via legacy_id
    assert is_favorite(prop, properties_root=tmp_path) is True

    # Toggle off should remove the legacy legacy_id entry
    toggle_favorite(prop, properties_root=tmp_path)
    saved = load_favorites(tmp_path)
    assert "134263" not in saved
    assert "uuid-modern" not in saved


def test_favorite_matches_id_from_older_synthesized_prefix(tmp_path: Path):
    """A favorite saved under an older synthesized-id prefix still resolves.

    Export rows with no provider `API Id` get a synthesized `property_id` of
    `<slug>-<numeric id>`. The slug changed in the Phase-0 de-identification,
    so `_favorites.json` can still hold entries written under the old one —
    those must keep matching, and toggling off must clear them.
    """
    from data.property_io import is_favorite, toggle_favorite

    (tmp_path / "_favorites.json").write_text(json.dumps(["aln-134263"]))
    prop = {"property_id": "legacy-134263", "legacy_id": "134263", "name": "Foo"}

    assert is_favorite(prop, properties_root=tmp_path) is True

    toggle_favorite(prop, properties_root=tmp_path)
    assert load_favorites(tmp_path) == set()


def test_favorite_does_not_collapse_distinct_native_ids(tmp_path: Path):
    """Normalization must not merge distinct 8R / UUID property_ids."""
    from data.property_io import is_favorite

    (tmp_path / "_favorites.json").write_text(json.dumps(["8R-51710-aaaaaaaaaaaa"]))
    prop = {"property_id": "8R-51710-bbbbbbbbbbbb", "name": "Bar"}
    assert is_favorite(prop, properties_root=tmp_path) is False


def test_load_custom_props_converts_legacy_arrays_to_dicts(tmp_path: Path):
    """Legacy list-of-arrays format auto-converts to list-of-dicts on load.

    Position 0 = legacy id (-1 = custom sentinel, cleared to None on load).
    Position 1 = name, 2 = address, 6 = units, 8 = occupancy %, 12 = class.
    """
    data = [[-1, "Custom Prop", "Address", "Norfolk", "23502", "Norfolk Co",
             100, 1985, 93, 850, 1500, 1.76, "C"]]
    (tmp_path / "_custom_props.json").write_text(json.dumps(data))
    result = load_custom_props(tmp_path)
    assert len(result) == 1
    assert result[0]["name"] == "Custom Prop"
    assert result[0]["units"] == 100
    assert result[0]["asset_class"] == "C"
    assert result[0]["legacy_id"] is None  # -1 sentinel cleared
    assert result[0]["occupancy_pct"] == pytest.approx(0.93)  # 93 → 0.93
    assert result[0]["property_id"].startswith("custom-")


def test_load_custom_props_handles_new_dict_format(tmp_path: Path):
    """New format: list of property dicts, no conversion needed."""
    data = [
        {
            "property_id": "custom-abc-123",
            "name": "New Format Prop",
            "city": "Hampton",
            "units": 50,
            "asset_class": "B",
        }
    ]
    (tmp_path / "_custom_props.json").write_text(json.dumps(data))
    result = load_custom_props(tmp_path)
    assert len(result) == 1
    assert result[0]["property_id"] == "custom-abc-123"
    assert result[0]["name"] == "New Format Prop"


def test_add_custom_property_appends_to_file(tmp_path: Path):
    from data.property_io import add_custom_property

    prop = {
        "name": "Foo Apartments",
        "address": "1 Main St",
        "city": "Norfolk",
        "units": 75,
        "asset_class": "C",
        "latitude": 36.85,
        "longitude": -76.29,
    }
    pid = add_custom_property(prop, properties_root=tmp_path)
    assert pid.startswith("custom-")
    # File now exists with the new entry
    cp_path = tmp_path / "_custom_props.json"
    assert cp_path.is_file()
    saved = load_custom_props(tmp_path)
    assert len(saved) == 1
    assert saved[0]["name"] == "Foo Apartments"
    assert saved[0]["property_id"] == pid


def test_add_custom_property_appends_to_existing(tmp_path: Path):
    """Adding a second custom property doesn't clobber the first."""
    from data.property_io import add_custom_property

    pid1 = add_custom_property({"name": "First", "units": 10}, properties_root=tmp_path)
    pid2 = add_custom_property({"name": "Second", "units": 20}, properties_root=tmp_path)
    saved = load_custom_props(tmp_path)
    assert len(saved) == 2
    pids = {p["property_id"] for p in saved}
    assert pids == {pid1, pid2}


# ---------------------------------------------------------------------------
# Smoke test against the real Properties/ folder
# (skips gracefully if the folder isn't there — useful for CI)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not PROPERTIES_ROOT.is_dir()
    or not any(PROPERTIES_ROOT.glob("*Dove-Landing*")),
    reason="full Properties/ library not present in this checkout "
           "(a partial folder with a stray property or two doesn't count)",
)
def test_smoke_real_properties_folder():
    """Confirm we can walk Brian's actual Properties/ + load Dove Landing's deal."""
    folders = discover_property_folders()
    assert len(folders) >= 20  # Brian had 21+ folders as of 2026-05-06

    # Find Dove Landing — should have all four files per the most-recent UW
    dove = next(
        (f for f in folders if "Dove-Landing" in f.folder_name),
        None,
    )
    assert dove is not None, "Dove Landing folder missing"
    assert dove.has_deal is True
    assert dove.has_sources is True
    assert dove.has_sales is True

    deal = load_deal(dove.path)
    assert deal is not None
    # Sanity check Dove Landing's saved dial: $46.3M / $3.25M NOI per memory
    assert 46_000_000 < deal.pp < 47_000_000
    assert deal.dp == 30
    assert deal.am == 25  # locked


@pytest.mark.skipif(
    not PROPERTIES_ROOT.is_dir(),
    reason="real Properties/ folder not present in this checkout",
)
def test_smoke_crossroads_29_amf_migration():
    """Crossroads-Townhomes-29 has `s-amf: 15000` legacy stale value.
    Loader should auto-migrate to the 4% default."""
    cross = PROPERTIES_ROOT / "Crossroads-Townhomes-29-Norfolk"
    if not cross.is_dir():
        pytest.skip("Crossroads-29 folder not present")
    deal = load_deal(cross)
    if deal is None:
        pytest.skip("no deal.json in Crossroads-29")
    # Confirm migration: stale $15k value → 4%
    assert deal.amf == 4.0
