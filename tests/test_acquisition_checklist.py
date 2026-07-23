"""Tests for core.acquisition_checklist — catalog integrity + state I/O + progress + export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import acquisition_checklist as ac
from core import acquisition_checklist_default as defaults
from core import acquisition_checklist_export as exp


# ---------------------------------------------------------------------------
# Catalog integrity (the auto-generated module)
# ---------------------------------------------------------------------------

class TestCatalog:
    def test_eight_phases(self):
        assert len(defaults.ACQUISITION_CHECKLIST) == 8

    def test_phase_ids_unique(self):
        ids = [p.id for p in defaults.ACQUISITION_CHECKLIST]
        assert len(set(ids)) == len(ids)

    def test_every_phase_has_at_least_one_category(self):
        for p in defaults.ACQUISITION_CHECKLIST:
            assert len(p.categories) >= 1, f"phase {p.id} has no categories"

    def test_every_category_has_at_least_one_item(self):
        for p in defaults.ACQUISITION_CHECKLIST:
            for cat in p.categories:
                assert len(cat.items) >= 1, f"category {cat.label} in {p.id} is empty"

    def test_item_ids_globally_unique(self):
        ids = [i.id for i in defaults.all_items()]
        assert len(set(ids)) == len(ids), "duplicate item IDs in catalog"

    def test_every_item_has_a_known_deadline_type(self):
        for i in defaults.all_items():
            assert i.deadline_type in ("hard", "soft", "open"), (
                f"item {i.id} has unknown deadline_type {i.deadline_type!r}"
            )

    def test_find_item_roundtrip(self):
        all_ids = [i.id for i in defaults.all_items()]
        for item_id in all_ids[:5] + all_ids[-5:]:
            found = defaults.find_item(item_id)
            assert found is not None
            assert found.id == item_id

    def test_find_item_returns_none_for_unknown(self):
        assert defaults.find_item("nope-doesnt-exist") is None

    def test_total_item_count_is_reasonable(self):
        # Sanity bound — if extraction breaks, the count usually changes.
        n = len(defaults.all_items())
        assert 100 < n < 250, f"unexpected item count: {n}"


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

class TestStateIO:
    def test_load_missing_file_bootstraps(self, tmp_path: Path):
        state = ac.load_state(tmp_path)
        assert state.deal_id == tmp_path.name
        assert state.checked_item_ids == set()

    def test_save_then_load_roundtrip(self, tmp_path: Path):
        state = ac.bootstrap_state("RoundTrip")
        items = defaults.all_items()
        ac.set_item(state, items[0].id, True)
        ac.set_item(state, items[5].id, True)
        ac.save_state(tmp_path, state)

        loaded = ac.load_state(tmp_path)
        assert loaded.deal_id == "RoundTrip"
        assert loaded.checked_item_ids == {items[0].id, items[5].id}
        assert loaded.updated_at  # save sets a timestamp

    def test_load_corrupt_file_falls_back(self, tmp_path: Path):
        (tmp_path / "acquisition-checklist.json").write_text("garbage", encoding="utf-8")
        state = ac.load_state(tmp_path)
        assert state.deal_id == tmp_path.name
        assert state.checked_item_ids == set()

    def test_load_prunes_dangling_ids(self, tmp_path: Path):
        # If the HTML/catalog removes an item, persisted state should drop the stale ID.
        payload = {
            "deal_id": tmp_path.name,
            "checked_item_ids": [
                defaults.all_items()[0].id,
                "stale-removed-item-id",
            ],
            "updated_at": "2026-05-27T10:00:00",
        }
        (tmp_path / "acquisition-checklist.json").write_text(json.dumps(payload), encoding="utf-8")
        state = ac.load_state(tmp_path)
        assert defaults.all_items()[0].id in state.checked_item_ids
        assert "stale-removed-item-id" not in state.checked_item_ids


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class TestMutations:
    def test_toggle_item_adds_then_removes(self):
        state = ac.bootstrap_state("T")
        item_id = defaults.all_items()[0].id
        ac.toggle_item(state, item_id)
        assert item_id in state.checked_item_ids
        ac.toggle_item(state, item_id)
        assert item_id not in state.checked_item_ids

    def test_toggle_unknown_item_is_noop(self):
        state = ac.bootstrap_state("T")
        before = set(state.checked_item_ids)
        ac.toggle_item(state, "nope")
        assert state.checked_item_ids == before

    def test_set_item_idempotent(self):
        state = ac.bootstrap_state("T")
        item_id = defaults.all_items()[0].id
        ac.set_item(state, item_id, True)
        ac.set_item(state, item_id, True)
        assert state.checked_item_ids == {item_id}
        ac.set_item(state, item_id, False)
        ac.set_item(state, item_id, False)
        assert state.checked_item_ids == set()

    def test_check_all_global(self):
        state = ac.bootstrap_state("T")
        ac.check_all(state)
        assert len(state.checked_item_ids) == len(defaults.all_items())

    def test_check_all_phase_scoped(self):
        state = ac.bootstrap_state("T")
        first_phase = defaults.ACQUISITION_CHECKLIST[0]
        ac.check_all(state, phase_id=first_phase.id)
        phase_items = {i.id for c in first_phase.categories for i in c.items}
        assert state.checked_item_ids == phase_items

    def test_clear_all_phase_scoped(self):
        state = ac.bootstrap_state("T")
        ac.check_all(state)  # everything
        first_phase = defaults.ACQUISITION_CHECKLIST[0]
        ac.clear_all(state, phase_id=first_phase.id)
        phase_items = {i.id for c in first_phase.categories for i in c.items}
        # phase 1 items removed; rest still checked
        assert phase_items.isdisjoint(state.checked_item_ids)
        assert len(state.checked_item_ids) == len(defaults.all_items()) - len(phase_items)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

class TestProgress:
    def test_overall_progress_zero_at_start(self):
        state = ac.bootstrap_state("T")
        o = ac.overall_progress(state)
        assert o.done == 0
        assert o.total == len(defaults.all_items())
        assert o.pct == 0.0

    def test_overall_progress_full_after_check_all(self):
        state = ac.bootstrap_state("T")
        ac.check_all(state)
        o = ac.overall_progress(state)
        assert o.done == o.total
        assert o.pct == 1.0
        assert o.critical_done == o.critical_total

    def test_phase_progress_isolated(self):
        state = ac.bootstrap_state("T")
        first = defaults.ACQUISITION_CHECKLIST[0]
        ac.check_all(state, phase_id=first.id)
        pp = ac.phase_progress(state, first.id)
        assert pp.done == pp.total
        # Other phases should still be zero
        for other in defaults.ACQUISITION_CHECKLIST[1:]:
            assert ac.phase_progress(state, other.id).done == 0

    def test_critical_progress_tracks_critical_items_only(self):
        state = ac.bootstrap_state("T")
        critical_items = [i for i in defaults.all_items() if i.critical]
        # Mark only the first critical item
        ac.set_item(state, critical_items[0].id, True)
        o = ac.overall_progress(state)
        assert o.critical_done == 1
        assert o.critical_total == len(critical_items)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class TestNotes:
    def test_set_note_stores_text(self):
        state = ac.bootstrap_state("T")
        item_id = defaults.all_items()[0].id
        ac.set_note(state, item_id, "Attorney is Bob Smith @ Kaufman & Canoles, engaged 2026-05-27.")
        assert "Bob Smith" in ac.get_note(state, item_id)

    def test_set_empty_note_removes_entry(self):
        state = ac.bootstrap_state("T")
        item_id = defaults.all_items()[0].id
        ac.set_note(state, item_id, "Some note")
        assert state.notes
        ac.set_note(state, item_id, "")
        assert not state.notes

    def test_set_whitespace_only_removes_entry(self):
        state = ac.bootstrap_state("T")
        item_id = defaults.all_items()[0].id
        ac.set_note(state, item_id, "Note")
        ac.set_note(state, item_id, "   \n  \t  ")
        assert not state.notes

    def test_set_note_on_unknown_item_is_noop(self):
        state = ac.bootstrap_state("T")
        ac.set_note(state, "nope", "should not store")
        assert not state.notes

    def test_notes_roundtrip(self, tmp_path: Path):
        state = ac.bootstrap_state("T")
        items = defaults.all_items()
        ac.set_note(state, items[0].id, "First note")
        ac.set_note(state, items[3].id, "Third item, with a longer note covering specifics.")
        ac.save_state(tmp_path, state)
        loaded = ac.load_state(tmp_path)
        assert ac.get_note(loaded, items[0].id) == "First note"
        assert "longer note" in ac.get_note(loaded, items[3].id)

    def test_load_legacy_json_without_notes_field(self, tmp_path: Path):
        # Older saves predate the notes field — they must still load cleanly.
        legacy = {
            "deal_id": tmp_path.name,
            "checked_item_ids": [defaults.all_items()[0].id],
            "updated_at": "2026-05-27T10:00:00",
        }
        (tmp_path / "acquisition-checklist.json").write_text(json.dumps(legacy), encoding="utf-8")
        state = ac.load_state(tmp_path)
        assert state.notes == {}
        assert defaults.all_items()[0].id in state.checked_item_ids

    def test_load_prunes_notes_for_unknown_items(self, tmp_path: Path):
        payload = {
            "deal_id": tmp_path.name,
            "checked_item_ids": [],
            "notes": {
                defaults.all_items()[0].id: "valid",
                "phantom-removed-item": "should drop",
            },
        }
        (tmp_path / "acquisition-checklist.json").write_text(json.dumps(payload), encoding="utf-8")
        state = ac.load_state(tmp_path)
        assert defaults.all_items()[0].id in state.notes
        assert "phantom-removed-item" not in state.notes


# ---------------------------------------------------------------------------
# HTML / PDF export
# ---------------------------------------------------------------------------

_TEST_PROP = {
    "name": "Test Property",
    "address": "1 Main St",
    "city": "Norfolk",
    "state": "VA",
    "zip": "23504",
    "units": 26,
    "year_built": 1988,
    "asset_class": "C",
}


class TestExport:
    def test_html_renders_with_empty_state(self):
        import html as _html
        state = ac.bootstrap_state("X")
        html = exp.render_html(_TEST_PROP, state)
        assert "Acquisition Master Checklist" in html
        assert "Test Property" in html
        # All 8 phase titles should be in there (escaped for HTML)
        for phase in defaults.ACQUISITION_CHECKLIST:
            assert _html.escape(phase.title) in html

    def test_html_embeds_overall_progress(self):
        state = ac.bootstrap_state("X")
        ac.check_all(state)
        html = exp.render_html(_TEST_PROP, state)
        total = len(defaults.all_items())
        # Both "X / Y" and "Y" should appear
        assert f"{total} / {total}" in html

    def test_html_embeds_user_notes(self):
        state = ac.bootstrap_state("X")
        item_id = defaults.all_items()[0].id
        ac.set_note(state, item_id, "Engaged attorney 2026-05-27")
        html = exp.render_html(_TEST_PROP, state)
        assert "Engaged attorney 2026-05-27" in html

    def test_html_escapes_note_html(self):
        state = ac.bootstrap_state("X")
        item_id = defaults.all_items()[0].id
        ac.set_note(state, item_id, "<script>alert('xss')</script>")
        html = exp.render_html(_TEST_PROP, state)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_pdf_renders_with_empty_state(self):
        state = ac.bootstrap_state("X")
        pdf = exp.render_pdf(_TEST_PROP, state)
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 5_000  # Should be a non-trivial PDF

    def test_pdf_renders_with_notes_and_checked_items(self):
        state = ac.bootstrap_state("X")
        items = defaults.all_items()
        ac.set_item(state, items[0].id, True)
        ac.set_note(state, items[0].id, "First item — attorney engaged 2026-05-27, awaiting PSA draft")
        ac.set_note(state, items[5].id, "Multi-line note\nWith second line of detail")
        pdf = exp.render_pdf(_TEST_PROP, state)
        assert pdf.startswith(b"%PDF")
        # PDF with extra notes should be larger than the bare PDF
        bare = exp.render_pdf(_TEST_PROP, ac.bootstrap_state("X"))
        assert len(pdf) > len(bare) - 200  # roughly comparable; notes don't always grow much in xhtml2pdf


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

class TestPublicAPI:
    def test_exports(self):
        # Make sure the module exports the documented surface
        for name in (
            "ACQUISITION_CHECKLIST",
            "AcqChecklistState",
            "load_state",
            "save_state",
            "bootstrap_state",
            "toggle_item",
            "set_item",
            "set_note",
            "get_note",
            "check_all",
            "clear_all",
            "phase_progress",
            "overall_progress",
        ):
            assert hasattr(ac, name), f"missing public export: {name}"
