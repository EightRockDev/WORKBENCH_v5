"""Acquisition Checklist tab — per-property LOI → Day-90 transaction tracker.

Streamlit-native rebuild of `knowledgebase/acquisition-checklist-04282026.html`.
Persists per-property to `<property folder>/acquisition-checklist.json` so each
deal has independent progress tracking (the source HTML used localStorage, which
is browser-scoped not deal-scoped).

Layout:
  * Header strip — overall progress (X / Y items, critical-track X / Y)
  * Phase navigator — 8 phase pills with per-phase progress badges
  * One expander per phase (collapsed by default except the user's selected phase)
  * Inside each phase: phase summary box + categories with item checkboxes
  * Each item row: checkbox + text + italic note + deadline pill + critical badge
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import streamlit as st

import config
from core import acquisition_checklist as ac
from core.acquisition_checklist_export import render_pdf
from data.property_io import PropertyFolder
from ui.components import section_card


def _pdf_filename(prop: dict[str, Any]) -> str:
    """Build a kebab-case filename per Brian's file-naming convention."""
    name = (prop.get("name") or "property").lower()
    slug = "".join(c if c.isalnum() else "-" for c in name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    today = dt.datetime.now().strftime("%m%d%Y")
    return f"{slug}-acquisition-checklist-{today}.pdf"


def _cached_pdf_bytes(folder: Path, prop: dict[str, Any], state: ac.AcqChecklistState) -> bytes | None:
    """Generate PDF bytes once per (state, prop) signature; cache the result in
    session_state so the ~1 second PDF render only fires on real changes, not
    on every checkbox click."""
    cache_key = f"acq_pdf_cache::{folder}"
    sig_key = f"acq_pdf_sig::{folder}"
    sig = (state.to_json(), prop.get("name"), prop.get("address"), prop.get("units"))
    if st.session_state.get(sig_key) == sig:
        cached = st.session_state.get(cache_key)
        if isinstance(cached, bytes):
            return cached
    try:
        pdf_bytes = render_pdf(prop, state)
    except Exception as e:  # noqa: BLE001 — surface in UI
        st.session_state[cache_key] = e
        st.session_state[sig_key] = sig
        return None
    st.session_state[cache_key] = pdf_bytes
    st.session_state[sig_key] = sig
    return pdf_bytes

# Color tokens for deadline pills — mirror the source HTML palette where it
# diverges from the workbench's standard tokens.
_DEADLINE_COLORS = {
    "hard": ("#fde2dc", "#e8a092", "#b13b25"),  # rust
    "soft": ("#fbeed1", "#e2c982", "#8a6a10"),  # gold
    "open": ("#e4eaf0", "#a9b6c4", "#3a4a5a"),  # slate
}

_CRITICAL_BADGE = "#c04a2a"


def _state_key(folder: Path) -> str:
    return f"acq_checklist::{folder}"


def _get_state(folder: Path) -> ac.AcqChecklistState:
    """Cache the loaded state in session_state so we don't re-read JSON every
    rerun. Streamlit fires a rerun on every checkbox toggle — without this,
    we'd thrash the disk 157 times for a Check-All operation."""
    key = _state_key(folder)
    if key not in st.session_state:
        st.session_state[key] = ac.load_state(folder)
    return st.session_state[key]


def _commit_state(folder: Path) -> None:
    state = st.session_state.get(_state_key(folder))
    if state is not None:
        ac.save_state(folder, state)


def _render_progress_header(state: ac.AcqChecklistState) -> None:
    c = config.COLORS
    o = ac.overall_progress(state)
    pct = o.pct * 100
    crit_pct = o.critical_pct * 100

    pct_color = c["gn"] if pct >= 90 else c["ac"] if pct >= 50 else c["tx2"]
    crit_color = c["gn"] if o.critical_done == o.critical_total else c["rd"] if o.critical_done < o.critical_total * 0.5 else c["ac"]

    st.markdown(
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};border-radius:6px;'
        f'padding:14px 18px;margin-bottom:14px">'
        f'<div style="display:flex;gap:32px;flex-wrap:wrap;align-items:baseline">'
        f'<div>'
        f'<div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{c["tx3"]}">Overall progress</div>'
        f'<div style="font-size:22px;font-weight:700;color:{pct_color}">{o.done} / {o.total}'
        f'<span style="font-size:14px;color:{c["tx2"]};margin-left:8px">({pct:.0f}%)</span></div>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{c["tx3"]}">Critical track</div>'
        f'<div style="font-size:22px;font-weight:700;color:{crit_color}">{o.critical_done} / {o.critical_total}'
        f'<span style="font-size:14px;color:{c["tx2"]};margin-left:8px">({crit_pct:.0f}%)</span></div>'
        f'</div>'
        f'</div>'
        f'<div style="background:{c["bdr"]};height:6px;border-radius:3px;margin-top:12px;overflow:hidden">'
        f'<div style="background:{pct_color};height:100%;width:{pct:.1f}%"></div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_phase_navigator(state: ac.AcqChecklistState) -> None:
    c = config.COLORS
    o = ac.overall_progress(state)
    chips = []
    for i, phase in enumerate(ac.ACQUISITION_CHECKLIST):
        pp = o.phases[i]
        pct = pp.pct * 100
        if pp.done == pp.total and pp.total > 0:
            color = c["gn"]
        elif pp.done > 0:
            color = c["ac"]
        else:
            color = c["tx3"]
        chips.append(
            f'<div style="display:inline-block;border:1px solid {color};color:{color};'
            f'border-radius:14px;padding:4px 12px;margin:0 6px 6px 0;font-size:11px;line-height:1.4">'
            f'<b>{phase.number}.</b> {phase.title.split(":")[0][:32]} '
            f'<span style="opacity:0.7;margin-left:4px">{pp.done}/{pp.total}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="margin-bottom:10px">{"".join(chips)}</div>',
        unsafe_allow_html=True,
    )


def _render_phase_summary(phase) -> None:
    c = config.COLORS
    st.markdown(
        f'<div style="background:#0f0f0f;color:#f5f2ec;padding:12px 16px;'
        f'border-left:4px solid {c["ac"]};font-size:12.5px;line-height:1.6;'
        f'margin-bottom:14px">{phase.summary}</div>',
        unsafe_allow_html=True,
    )


def _deadline_pill_html(deadline_type: str, deadline_text: str) -> str:
    bg, border, fg = _DEADLINE_COLORS.get(deadline_type, _DEADLINE_COLORS["open"])
    return (
        f'<span style="background:{bg};color:{fg};border:1px solid {border};'
        f'border-radius:2px;padding:1px 6px;font-size:10px;letter-spacing:0.4px;'
        f'white-space:nowrap;font-family:system-ui,sans-serif">'
        f'{deadline_text or deadline_type.upper()}</span>'
    )


def _render_item(folder: Path, item, state: ac.AcqChecklistState) -> None:
    c = config.COLORS
    key = f"acq_item::{folder}::{item.id}"
    current = item.id in state.checked_item_ids
    user_note = state.notes.get(item.id, "")

    # Streamlit's checkbox callback fires after the value flips. We persist on
    # change rather than re-reading from the widget each frame.
    def _on_change():
        new_val = st.session_state.get(key, False)
        ac.set_item(state, item.id, new_val)
        _commit_state(folder)

    col_cb, col_body, col_note = st.columns([0.04, 0.86, 0.10], gap="small")
    with col_cb:
        st.checkbox(
            label=item.id,
            value=current,
            key=key,
            on_change=_on_change,
            label_visibility="collapsed",
        )
    with col_body:
        text_color = c["tx3"] if current else c["tx"]
        decoration = "text-decoration:line-through;" if current else ""
        critical_badge = (
            f'<span style="display:inline-block;background:rgba(192,74,42,0.08);'
            f'color:{_CRITICAL_BADGE};border:1px solid rgba(192,74,42,0.3);'
            f'border-radius:50%;width:18px;height:18px;text-align:center;'
            f'font-size:10px;font-weight:600;line-height:16px;margin-right:6px">!</span>'
            if item.critical else ""
        )
        catalog_note_html = (
            f'<div style="font-size:11px;color:{c["tx3"]};font-style:italic;margin-top:2px">{item.note}</div>'
            if item.note else ""
        )
        # User note shown inline (collapsible visual: small left-border, gold accent)
        user_note_html = ""
        if user_note:
            import html as _html
            safe = _html.escape(user_note).replace("\n", "<br/>")
            user_note_html = (
                f'<div style="font-size:11.5px;color:{c["tx2"]};margin-top:4px;'
                f'padding:4px 8px;border-left:2px solid {c["ac"]};'
                f'background:rgba(200,144,10,0.06);border-radius:2px;line-height:1.45">'
                f'📝 {safe}</div>'
            )
        st.markdown(
            f'<div style="padding-top:2px;line-height:1.5">'
            f'<div style="display:flex;align-items:flex-start;gap:10px">'
            f'<div style="flex:1;font-size:13.5px;color:{text_color};{decoration}">{critical_badge}{item.text}</div>'
            f'<div style="flex-shrink:0;margin-top:1px">'
            f'{_deadline_pill_html(item.deadline_type, item.deadline_text)}'
            f'</div>'
            f'</div>'
            f'{catalog_note_html}'
            f'{user_note_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col_note:
        # Streamlit's popover keeps the note edit UI out-of-flow until clicked.
        # Label changes based on whether the item has a note so the user can scan
        # for items with notes at a glance.
        popover_label = "📝" if user_note else "＋"
        with st.popover(popover_label, help=("View / edit note" if user_note else "Add note"), use_container_width=True):
            note_key = f"acq_note_input::{folder}::{item.id}"
            new_note = st.text_area(
                "Note",
                value=user_note,
                key=note_key,
                height=120,
                placeholder="e.g. Bob Smith @ Kaufman & Canoles engaged 2026-05-27, awaiting PSA draft.",
                label_visibility="collapsed",
            )
            save_col, clear_col = st.columns(2)
            with save_col:
                if st.button("Save", key=f"acq_note_save::{folder}::{item.id}", use_container_width=True, type="primary"):
                    ac.set_note(state, item.id, new_note)
                    _commit_state(folder)
                    st.rerun()
            with clear_col:
                if st.button("Clear", key=f"acq_note_clear::{folder}::{item.id}", use_container_width=True):
                    ac.set_note(state, item.id, "")
                    _commit_state(folder)
                    st.rerun()


def _render_category(folder: Path, category, state: ac.AcqChecklistState) -> None:
    c = config.COLORS
    st.markdown(
        f'<div style="background:rgba(58,74,90,0.06);border-left:3px solid {c["tx2"]};'
        f'padding:4px 12px;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;'
        f'color:{c["tx2"]};font-weight:600;margin:14px 0 6px 0">'
        f'{category.label}</div>',
        unsafe_allow_html=True,
    )
    for item in category.items:
        _render_item(folder, item, state)


def _render_phase(folder: Path, phase, progress: ac.PhaseProgress, state: ac.AcqChecklistState) -> None:
    c = config.COLORS
    pct = progress.pct * 100
    pct_color = c["gn"] if pct >= 90 else c["ac"] if pct >= 50 else c["tx3"]

    # Inline header above the phase body — mirrors the HTML phase-header look.
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:18px;'
        f'border-bottom:2px solid {c["tx"]};padding-bottom:10px;margin-bottom:14px">'
        f'<div style="font-size:42px;font-weight:900;line-height:1;color:{c["bdr"]};'
        f'width:54px;text-align:right">{phase.number}</div>'
        f'<div style="flex:1">'
        f'<div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:{c["tx3"]};margin-bottom:2px">{phase.tag}</div>'
        f'<div style="font-size:18px;font-weight:700;color:{c["tx"]}">{phase.title}</div>'
        f'<div style="display:inline-block;background:rgba(200,144,10,0.08);border-left:3px solid {c["ac"]};'
        f'color:{c["ac2"]};font-size:11px;padding:2px 10px;margin-top:6px">{phase.timeline}</div>'
        f'</div>'
        f'<div style="flex-shrink:0;text-align:right">'
        f'<div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{c["tx3"]}">Progress</div>'
        f'<div style="font-size:18px;font-weight:700;color:{pct_color}">{progress.done}/{progress.total}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    _render_phase_summary(phase)

    # Per-phase Check-All / Clear-All controls
    col_a, col_b, _ = st.columns([1, 1, 6], gap="small")
    with col_a:
        if st.button("Check phase", key=f"acq_check_phase::{folder}::{phase.id}", use_container_width=True):
            ac.check_all(state, phase.id)
            _commit_state(folder)
            st.rerun()
    with col_b:
        if st.button("Clear phase", key=f"acq_clear_phase::{folder}::{phase.id}", use_container_width=True):
            ac.clear_all(state, phase.id)
            _commit_state(folder)
            st.rerun()

    for category in phase.categories:
        _render_category(folder, category, state)


def render_acquisition_checklist(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    if folder is None:
        st.info(
            "No property folder yet. Save deal state on the Underwriting tab first — "
            "the checklist persists to `acquisition-checklist.json` inside the property folder."
        )
        return

    c = config.COLORS
    state = _get_state(folder.path)
    overall = ac.overall_progress(state)

    with section_card(
        "Acquisition Checklist",
        icon="📅",
        accent="ac",
        subtitle=(
            "LOI Acceptance → Close → 90 Days Post-Closing. 8 phases, "
            f"{overall.total} items. Lindahl + Eight Rock methodology. "
            "Saved per property to acquisition-checklist.json."
        ),
        help_anchor="acquisition-checklist",
        help_summary=(
            "157-item, 8-phase checklist ships today. AI co-pilot that "
            "reads your uploaded docs and pre-fills ~70 items with source "
            "citations is the next build. Click for the full Help section."
        ),
    ):
        _render_progress_header(state)
        _render_phase_navigator(state)

        # Global controls
        col_check, col_clear, col_pdf, _spacer = st.columns([1, 1, 1.4, 4.6], gap="small")
        with col_check:
            if st.button("Check all", key=f"acq_check_all::{folder.path}", use_container_width=True):
                ac.check_all(state)
                _commit_state(folder.path)
                st.rerun()
        with col_clear:
            if st.button("Clear all", key=f"acq_clear_all::{folder.path}", use_container_width=True):
                ac.clear_all(state)
                _commit_state(folder.path)
                st.rerun()
        with col_pdf:
            pdf_bytes = _cached_pdf_bytes(folder.path, prop, state)
            if pdf_bytes is not None:
                st.download_button(
                    "📥 Download PDF",
                    data=pdf_bytes,
                    file_name=_pdf_filename(prop),
                    mime="application/pdf",
                    key=f"acq_pdf::{folder.path}",
                    use_container_width=True,
                    type="primary",
                )
            else:
                err = st.session_state.get(f"acq_pdf_cache::{folder.path}")
                st.button(
                    "📥 PDF unavailable",
                    key=f"acq_pdf_err::{folder.path}",
                    disabled=True,
                    use_container_width=True,
                    help=f"PDF generation error: {type(err).__name__}: {err}" if err else None,
                )

        # Default-open the first incomplete phase (the one the user is actively working).
        first_incomplete_idx = next(
            (i for i, pp in enumerate(overall.phases) if pp.done < pp.total),
            0,
        )

        for i, phase in enumerate(ac.ACQUISITION_CHECKLIST):
            pp = overall.phases[i]
            header_pct = f"{pp.done}/{pp.total}"
            done_marker = "✅" if pp.done == pp.total and pp.total > 0 else ""
            expander_label = f"**Phase {phase.number}** · {phase.title}  ·  {header_pct} {done_marker}"
            with st.expander(expander_label, expanded=(i == first_incomplete_idx)):
                _render_phase(folder.path, phase, pp, state)


__all__ = ["render_acquisition_checklist"]
