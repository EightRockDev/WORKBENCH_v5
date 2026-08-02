"""Flip-day provenance colors (the last non-gate item on the P0-3 list).

The de-identification sweep renamed `src_aln` -> `src_8r` in COLORS — into a
dict that already HAD a teal `src_8r` for the self-sourced backbone. Python
keeps the last duplicate silently, so the reference-survey grey vanished and
every "property record" badge rendered in backbone teal: the one distinction
the badge exists to draw, erased by a key collision no linter flagged.

These tests pin the repaired taxonomy and — more importantly — make any
future duplicate literal key in the config palettes a test failure instead of
a silent overwrite.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import config


def test_reference_and_backbone_provenance_are_distinct_colors():
    """The badge exists to tell these two apart."""
    assert "src_ref" in config.COLORS
    assert "src_8r" in config.COLORS
    assert config.COLORS["src_ref"] != config.COLORS["src_8r"]


def test_no_dict_literal_in_config_has_a_duplicate_key():
    """The root cause, pinned: a duplicate key in a literal is legal Python
    and silently drops the first value. Catch it at the AST, where the
    duplicate is still visible."""
    src = pathlib.Path(config.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant)]
            dups = {k for k in keys if keys.count(k) > 1}
            assert not dups, (
                f"duplicate key(s) {dups} in a config.py dict literal - "
                f"Python keeps only the last, the rest vanish silently")


def test_the_db_badge_color_follows_the_read_seam(monkeypatch):
    """Pre-flip the record row is the reference survey (grey); post-flip it
    is the 8R backbone (teal). One config flip must move the badge with it,
    with no restart and no UI edit."""
    monkeypatch.setattr(config, "SPINE_READ_SOURCE", "legacy")
    assert config.spine_provenance_color() == config.COLORS["src_ref"]
    monkeypatch.setattr(config, "SPINE_READ_SOURCE", "8r")
    assert config.spine_provenance_color() == config.COLORS["src_8r"]


def test_the_ui_reads_the_seam_not_a_hardcoded_palette_key():
    """The badge call sites must go through spine_provenance_color(), or the
    next palette edit reintroduces the bug behind the tests' back."""
    from ui import inventory, property_detail

    for mod in (inventory, property_detail):
        src = inspect.getsource(mod)
        assert "spine_provenance_color()" in src, (
            f"{mod.__name__} no longer resolves the record-provenance color "
            f"through the SPINE_READ_SOURCE seam")
        assert 'c["src_8r"]' not in src and "c.get(\"src_8r\"" not in src, (
            f"{mod.__name__} hardcodes src_8r for a record badge - grey/teal "
            f"must come from the read seam")


def test_the_crossref_index_limit_clears_the_backbone():
    """list_properties(limit=N) feeds the inventory match counters. The
    backbone behind the same seam is ~19,000 rows; a limit sized to the
    ~2,500-row legacy table silently drops half of them post-flip, and every
    dropped row counts as "unmatched"."""
    from ui import inventory

    src = inspect.getsource(inventory._build_prop_address_index)
    assert "limit=50_000" in src, (
        "the address-index query limit must clear the 8R backbone (~19K "
        "rows), not just the legacy table (~2.5K)")
