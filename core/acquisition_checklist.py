"""Per-property acquisition checklist — state I/O + progress aggregates.

Catalog (8 phases, 23 categories, 157 items) lives in
[[acquisition_checklist_default]] and is auto-generated from
`knowledgebase/acquisition-checklist-04282026.html`. This module wraps it
with property-scoped state: which items the user has checked off, when,
and aggregate progress per phase / per critical-item track / overall.

State is persisted as `acquisition-checklist.json` in the property folder
alongside `dd.json`, `deal.json`, `sources.json`. Same pattern as the DD tab.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.acquisition_checklist_default import (
    ACQUISITION_CHECKLIST,
    Item,
    Phase,
    all_items,
    find_item,
)

# Re-export the catalog so consumers don't need two imports
__all__ = [
    "ACQUISITION_CHECKLIST",
    "Item",
    "Phase",
    "AcqChecklistState",
    "PhaseProgress",
    "OverallProgress",
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
]

_STATE_FILENAME = "acquisition-checklist.json"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class AcqChecklistState:
    deal_id: str
    checked_item_ids: set[str] = field(default_factory=set)
    notes: dict[str, str] = field(default_factory=dict)  # item_id -> free-text note
    updated_at: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "deal_id": self.deal_id,
            "checked_item_ids": sorted(self.checked_item_ids),
            "notes": {k: v for k, v in self.notes.items() if v},
            "updated_at": self.updated_at,
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str, deal_id_fallback: str) -> AcqChecklistState:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return cls(deal_id=deal_id_fallback)
        raw_notes = data.get("notes") or {}
        notes: dict[str, str] = {}
        if isinstance(raw_notes, dict):
            for k, v in raw_notes.items():
                if isinstance(v, str) and v.strip():
                    notes[str(k)] = v
        return cls(
            deal_id=str(data.get("deal_id") or deal_id_fallback),
            checked_item_ids=set(data.get("checked_item_ids") or []),
            notes=notes,
            updated_at=str(data.get("updated_at") or ""),
        )


def bootstrap_state(deal_id: str) -> AcqChecklistState:
    return AcqChecklistState(deal_id=deal_id, checked_item_ids=set())


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _state_path(folder: Path) -> Path:
    return folder / _STATE_FILENAME


def load_state(folder: Path) -> AcqChecklistState:
    """Read the acquisition-checklist.json for this property. If missing or
    corrupt, returns a fresh bootstrapped state keyed off the folder name."""
    p = _state_path(folder)
    deal_id_fallback = folder.name
    if not p.is_file():
        return bootstrap_state(deal_id_fallback)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return bootstrap_state(deal_id_fallback)
    state = AcqChecklistState.from_json(raw, deal_id_fallback)
    # Prune any IDs that no longer exist in the catalog (HTML edits may
    # remove items; we should not preserve dangling references).
    valid_ids = {i.id for i in all_items()}
    state.checked_item_ids = {x for x in state.checked_item_ids if x in valid_ids}
    state.notes = {k: v for k, v in state.notes.items() if k in valid_ids}
    return state


def save_state(folder: Path, state: AcqChecklistState) -> None:
    state.updated_at = dt.datetime.now().isoformat(timespec="seconds")
    folder.mkdir(parents=True, exist_ok=True)
    _state_path(folder).write_text(state.to_json(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def toggle_item(state: AcqChecklistState, item_id: str) -> AcqChecklistState:
    if find_item(item_id) is None:
        return state
    if item_id in state.checked_item_ids:
        state.checked_item_ids.discard(item_id)
    else:
        state.checked_item_ids.add(item_id)
    return state


def set_item(state: AcqChecklistState, item_id: str, checked: bool) -> AcqChecklistState:
    if find_item(item_id) is None:
        return state
    if checked:
        state.checked_item_ids.add(item_id)
    else:
        state.checked_item_ids.discard(item_id)
    return state


def set_note(state: AcqChecklistState, item_id: str, text: str) -> AcqChecklistState:
    """Set the note for an item. Empty/whitespace removes the entry."""
    if find_item(item_id) is None:
        return state
    if text and text.strip():
        state.notes[item_id] = text
    else:
        state.notes.pop(item_id, None)
    return state


def get_note(state: AcqChecklistState, item_id: str) -> str:
    return state.notes.get(item_id, "")


def check_all(state: AcqChecklistState, phase_id: str | None = None) -> AcqChecklistState:
    """Check every item, or every item in one phase if phase_id is given."""
    target = _items_in_phase(phase_id) if phase_id else all_items()
    state.checked_item_ids.update(i.id for i in target)
    return state


def clear_all(state: AcqChecklistState, phase_id: str | None = None) -> AcqChecklistState:
    target_ids = {i.id for i in (_items_in_phase(phase_id) if phase_id else all_items())}
    state.checked_item_ids -= target_ids
    return state


def _items_in_phase(phase_id: str | None) -> tuple[Item, ...]:
    if phase_id is None:
        return all_items()
    for p in ACQUISITION_CHECKLIST:
        if p.id == phase_id:
            return tuple(i for cat in p.categories for i in cat.items)
    return ()


# ---------------------------------------------------------------------------
# Progress aggregates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhaseProgress:
    phase_id: str
    total: int
    done: int
    critical_total: int
    critical_done: int

    @property
    def pct(self) -> float:
        return (self.done / self.total) if self.total else 0.0

    @property
    def critical_pct(self) -> float:
        return (self.critical_done / self.critical_total) if self.critical_total else 0.0


@dataclass(frozen=True)
class OverallProgress:
    total: int
    done: int
    critical_total: int
    critical_done: int
    phases: tuple[PhaseProgress, ...]

    @property
    def pct(self) -> float:
        return (self.done / self.total) if self.total else 0.0

    @property
    def critical_pct(self) -> float:
        return (self.critical_done / self.critical_total) if self.critical_total else 0.0


def phase_progress(state: AcqChecklistState, phase_id: str) -> PhaseProgress:
    items = _items_in_phase(phase_id)
    total = len(items)
    done = sum(1 for i in items if i.id in state.checked_item_ids)
    crit = [i for i in items if i.critical]
    return PhaseProgress(
        phase_id=phase_id,
        total=total,
        done=done,
        critical_total=len(crit),
        critical_done=sum(1 for i in crit if i.id in state.checked_item_ids),
    )


def overall_progress(state: AcqChecklistState) -> OverallProgress:
    per_phase = tuple(phase_progress(state, p.id) for p in ACQUISITION_CHECKLIST)
    return OverallProgress(
        total=sum(p.total for p in per_phase),
        done=sum(p.done for p in per_phase),
        critical_total=sum(p.critical_total for p in per_phase),
        critical_done=sum(p.critical_done for p in per_phase),
        phases=per_phase,
    )
