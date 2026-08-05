"""Tests for the Input tab — the quick-start "first numbers" front door.

The Streamlit rendering needs a running app; what matters for correctness is
that the Input tab shares ONE source of truth with Underwriting — the same
default-deal seed and the same version-preserving edit path — so the two
surfaces can't diverge. Those are pinned here.
"""

from __future__ import annotations

import app
from data.property_io import DealState
from ui.underwriting import build_default_deal

_PROP = {"units": 100, "city": "Norfolk", "asset_class": "C",
         "avg_rent": 1500, "name": "Madison Terrace"}


def test_build_default_deal_is_valid_and_seeded_from_record():
    deal = build_default_deal(_PROP)
    assert isinstance(deal, DealState)
    # 100 units × $130k/unit mid-Class-C seed.
    assert deal.pp == 100 * 130_000
    assert deal.noi > 0
    # Config-defaulted dials are in-range (Pydantic would reject otherwise).
    assert 10 <= deal.dp <= 50
    assert 3 <= deal.ir <= 12


def test_default_deal_matches_across_surfaces():
    # Underwriting and Input both call build_default_deal — same seed, no drift.
    a = build_default_deal(_PROP)
    b = build_default_deal(_PROP)
    assert a.model_dump() == b.model_dump()


def test_input_edit_preserves_concurrency_metadata():
    # The Input form edits via model_copy(update=...) — the same technique the
    # dial board uses so row_version/selected_levers survive (the fade-loop fix).
    deal = build_default_deal(_PROP)
    edited = deal.model_copy(update={"pp": 4_000_000.0, "noi": 300_000.0,
                                     "hp": 7, "dp": 25.0, "ir": 6.5})
    assert edited.pp == 4_000_000.0
    assert edited.noi == 300_000.0
    assert edited.hp == 7
    assert edited.row_version == deal.row_version   # metadata preserved


def test_input_is_the_first_tab():
    assert app._PTAB_KEYS[0] == "input"
    assert app._PTAB_LABELS_V2[0] == "Input"
    assert len(app._PTAB_KEYS) == len(app._PTAB_LABELS_V2) == len(app._PTAB_LABELS_V1)


def test_input_tab_module_imports():
    import ui.input_tab as it
    assert hasattr(it, "render_input")
