"""Due Diligence tab — checklist + risk dashboard + per-item detail.

Three sections render in order:
  1. Header card — completion %, overall risk score + level + recommendation,
     dealbreaker count, IC-readiness badge.
  2. Risk dashboard — 9 category cards (score + level + open-item count).
  3. Master checklist — collapsible per-category, with per-item expander
     for edit (status, owner, due, notes, risk score, dealbreaker flag,
     artifact list).

Deferred to a later session (depends on cloud foundation):
  - AI artifact extraction (needs Anthropic call wired through storage layer)
  - @Peter mention → Microsoft Graph email (needs Graph auth landed)
  - Bidirectional verdict tightening (DD finding → verdict.py constraint update)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

import config
from core import due_diligence as dd
from data.property_io import PropertyFolder
from ui.components import section_card


# ---------------------------------------------------------------------------
# Public entry point — called from app.py
# ---------------------------------------------------------------------------

def render_due_diligence(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    if folder is None or folder.path is None:
        st.info(
            "This property doesn't have an on-disk folder yet. Add one via the "
            "sidebar before starting DD."
        )
        return

    fp = folder.path
    state = dd.load_state(fp)

    # Sync deal_id with current property name (for new properties)
    if not state.deal_id or state.deal_id == fp.name:
        state.deal_id = prop.get("name") or fp.name

    # Recompute aggregates on every render — cheap, keeps UI honest
    state = dd.recompute_aggregates(state)

    _render_header(state, prop, fp)
    _render_dashboard(state)
    _render_checklist(state, fp)


# ---------------------------------------------------------------------------
# Header — KPI tiles + recommendation + IC readiness
# ---------------------------------------------------------------------------

def _render_header(state: dd.DDState, prop: dict[str, Any], folder_path: Path) -> None:
    c = config.COLORS

    total = len(state.items)
    done = sum(1 for i in state.items if i.status in ("complete", "n-a"))
    pct = done / total if total else 0.0
    readiness = dd.ic_readiness(state)

    score_str = (
        f"{state.overall_risk_score:.0f}" if state.overall_risk_score is not None else "—"
    )
    level = state.overall_risk_level
    level_color = {
        "LOW": c["gn"], "MEDIUM": c["yw"], "HIGH": c["yw"],
        "CRITICAL": c["rd"], "UNSCORED": c["tx3"],
    }.get(level, c["tx3"])
    rec_color = {
        "PROCEED": c["gn"], "PROCEED_WITH_MITIGATIONS": c["gn"],
        "PROCEED_WITH_CAUTION": c["yw"], "FURTHER_DILIGENCE": c["bl"],
        "REJECT": c["rd"],
    }.get(state.recommendation or "", c["tx3"])
    rec_label = (state.recommendation or "—").replace("_", " ")

    with section_card(
        f"Due Diligence — {state.deal_id}",
        icon="📋",
        accent="ac",
        subtitle=(
            f"Strategy: {state.investment_strategy} · "
            f"Last updated {state.last_updated[:16] if state.last_updated else '—'}"
        ),
        help_anchor="dd-bidirectional",
        help_summary=(
            "49-item DD checklist with 9-category risk scoring ships today. "
            "Bidirectional verdict tightening (findings ripple to other "
            "verdicts, both directions) is the next build. Click for the "
            "full Help section."
        ),
    ):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(_tile(
                "Completion", f"{pct:.0%}", f"{done} of {total} items",
                color=c["ac3"] if pct < 0.80 else c["gn"],
                c=c,
            ), unsafe_allow_html=True)
        with col2:
            st.markdown(_tile(
                "Overall Risk", score_str, level,
                color=level_color, c=c,
            ), unsafe_allow_html=True)
        with col3:
            st.markdown(_tile(
                "Dealbreakers",
                f"{len(state.dealbreakers)}H / {len(state.soft_dealbreakers)}S",
                "Hard / Soft",
                color=c["rd"] if state.dealbreakers else c["tx3"],
                c=c,
            ), unsafe_allow_html=True)
        with col4:
            ready_str = "READY" if readiness.is_ready else "NOT READY"
            ready_color = c["gn"] if readiness.is_ready else c["rd"]
            st.markdown(_tile(
                "IC-Ready", ready_str, "(gates Exec Summary GO)",
                color=ready_color, c=c,
            ), unsafe_allow_html=True)

        # Recommendation row
        st.markdown(
            f'<div style="margin-top:14px;padding:10px 14px;'
            f'background:{c["bg3"]};border-left:3px solid {rec_color};'
            f'border-radius:4px">'
            f'<span style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'letter-spacing:0.6px;font-weight:600">Recommendation</span><br>'
            f'<span style="font-size:18px;color:{rec_color};font-weight:700;'
            f'letter-spacing:0.3px">{rec_label}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if not readiness.is_ready and readiness.blocking_reasons:
            st.warning(
                "**Blocking IC sign-off:**\n"
                + "\n".join(f"- {r}" for r in readiness.blocking_reasons)
            )


def _tile(label: str, value: str, sub: str, color: str, c: dict) -> str:
    return (
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {color};border-radius:6px;padding:10px 14px">'
        f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
        f'letter-spacing:0.6px;font-weight:600">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color};'
        f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:3px">'
        f'{value}</div>'
        f'<div style="color:{c["tx3"]};font-size:11px;margin-top:2px">{sub}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Risk dashboard — 9 category cards
# ---------------------------------------------------------------------------

def _render_dashboard(state: dd.DDState) -> None:
    c = config.COLORS

    with section_card("Risk Categories", icon="🎯"):
        # 3 columns × 3 rows = 9 categories
        rows = [
            ("ownershipTitle", "legalLitigation", "environmental"),
            ("zoningRegulatory", "financial", "market"),
            ("tenantConcentration", "physicalCondition", "regulatoryMultifamily"),
        ]
        for row in rows:
            cols = st.columns(3)
            for i, cat in enumerate(row):
                with cols[i]:
                    stats = dd.category_stats(state, cat)
                    score = stats["score"]
                    level = stats["level"]
                    color = {
                        "LOW": c["gn"], "MEDIUM": c["yw"],
                        "HIGH": c["yw"], "CRITICAL": c["rd"],
                        "UNSCORED": c["tx3"],
                    }.get(level, c["tx3"])
                    icon = dd.CATEGORY_ICONS[cat]
                    label = dd.CATEGORY_LABELS[cat]
                    score_str = f"{score:.0f}" if score is not None else "—"
                    by_status = stats["by_status"]
                    open_count = stats["total"] - by_status.get("complete", 0) - by_status.get("n-a", 0)
                    st.markdown(
                        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
                        f'border-top:3px solid {color};border-radius:6px;padding:10px 12px">'
                        f'<div style="font-size:12px;color:{c["tx3"]};font-weight:600">'
                        f'{icon} {label}</div>'
                        f'<div style="font-size:24px;font-weight:700;color:{color};'
                        f'line-height:1.1;margin:4px 0">{score_str}</div>'
                        f'<div style="font-size:11px;color:{c["tx2"]}">'
                        f'{level} · {open_count}/{stats["total"]} open'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )


# ---------------------------------------------------------------------------
# Master checklist — collapsible per-category, per-item expander
# ---------------------------------------------------------------------------

def _render_checklist(state: dd.DDState, folder: Path) -> None:
    c = config.COLORS

    with section_card("Master Checklist", icon="✅"):
        # Strategy + global controls at top
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            new_strategy = st.selectbox(
                "Investment strategy (affects risk weighting)",
                options=["core", "core-plus", "value-add", "opportunistic", "distressed"],
                index=["core", "core-plus", "value-add", "opportunistic", "distressed"]
                      .index(state.investment_strategy),
                key=f"dd_strategy_{state.deal_id}",
            )
            if new_strategy != state.investment_strategy:
                state.investment_strategy = new_strategy  # type: ignore[assignment]
                dd.save_state(folder, dd.recompute_aggregates(state))
                st.rerun()
        with col2:
            if st.button("+ Add custom DD item", key=f"dd_add_item_{state.deal_id}"):
                st.session_state[f"dd_show_add_{state.deal_id}"] = True
        with col3:
            if st.button("↻ Reseed defaults", key=f"dd_reseed_{state.deal_id}",
                         help="Append any missing default-checklist items without overwriting your existing notes."):
                _reseed_defaults(state)
                dd.save_state(folder, state)
                st.rerun()

        if st.session_state.get(f"dd_show_add_{state.deal_id}"):
            _render_add_item_form(state, folder)

        # Items by category
        for cat in dd.CATEGORIES:
            cat_items = [i for i in state.items if i.category == cat]
            if not cat_items:
                continue
            cat_label = dd.CATEGORY_LABELS[cat]
            icon = dd.CATEGORY_ICONS[cat]
            done = sum(1 for i in cat_items if i.status in ("complete", "n-a"))
            with st.expander(
                f"{icon}  {cat_label}  ·  {done}/{len(cat_items)}",
                expanded=False,
            ):
                for item in cat_items:
                    _render_item_row(state, item, folder)


def _render_item_row(state: dd.DDState, item: dd.DDItem, folder: Path) -> None:
    c = config.COLORS

    status_colors = {
        "pending":     c["tx3"],
        "in-progress": c["bl"],
        "complete":    c["gn"],
        "n-a":         c["tx3"],
        "blocked":     c["rd"],
    }
    status_color = status_colors.get(item.status, c["tx3"])
    risk_color = {
        "LOW": c["gn"], "MEDIUM": c["yw"], "HIGH": c["yw"],
        "CRITICAL": c["rd"], "UNSCORED": c["tx3"],
    }.get(dd.risk_level_for_score(item.risk_score), c["tx3"])
    risk_str = (
        f"risk {int(item.risk_score)}" if item.risk_score is not None else "unscored"
    )
    db_chip = ""
    if item.is_dealbreaker_hit:
        db_label = "HARD" if item.dealbreaker_type == "hard" else "SOFT"
        db_color = c["rd"] if item.dealbreaker_type == "hard" else c["yw"]
        db_chip = (
            f'<span style="background:{db_color};color:#fff;font-size:9px;'
            f'font-weight:700;padding:2px 6px;border-radius:8px;margin-left:6px">'
            f'{db_label} DB</span>'
        )

    # Single-line summary
    summary_html = (
        f'<div style="display:flex;align-items:center;gap:10px">'
        f'<span style="background:{status_color};color:#fff;font-size:10px;'
        f'font-weight:700;padding:3px 8px;border-radius:8px;'
        f'text-transform:uppercase;letter-spacing:0.4px;min-width:88px;'
        f'text-align:center">{item.status}</span>'
        f'<span style="flex:1;color:{c["tx"]};font-weight:600;font-size:13px">'
        f'{item.title}</span>{db_chip}'
        f'<span style="color:{c["tx3"]};font-size:11px">{item.owner}</span>'
        f'<span style="color:{risk_color};font-size:11px;font-weight:600">{risk_str}</span>'
        f'</div>'
    )
    st.markdown(summary_html, unsafe_allow_html=True)

    # Edit pane (compact, always visible per-item but inside the category expander)
    with st.expander("edit", expanded=False):
        _render_item_edit(state, item, folder)
    st.markdown(
        f'<hr style="margin:6px 0;border:none;border-top:1px solid {c["bdr"]}">',
        unsafe_allow_html=True,
    )


def _render_item_edit(state: dd.DDState, item: dd.DDItem, folder: Path) -> None:
    """The form for editing a single DD item."""
    k = f"{state.deal_id}_{item.id}"

    col1, col2, col3 = st.columns(3)
    with col1:
        new_status = st.selectbox(
            "Status", options=["pending", "in-progress", "complete", "n-a", "blocked"],
            index=["pending", "in-progress", "complete", "n-a", "blocked"].index(item.status),
            key=f"st_{k}",
        )
    with col2:
        new_owner = st.text_input(
            "Owner", value=item.owner, key=f"ow_{k}",
            help="brian | peter | vendor:title-co | vendor:environmental | etc.",
        )
    with col3:
        try:
            current_due = dt.date.fromisoformat(item.due_date) if item.due_date else dt.date.today()
        except ValueError:
            current_due = dt.date.today()
        new_due = st.date_input("Due", value=current_due, key=f"du_{k}")

    new_notes = st.text_area(
        "Notes", value=item.notes, key=f"nt_{k}", height=80,
        placeholder="Findings, vendor name, follow-ups, etc.",
    )

    # Risk scoring
    col4, col5, col6 = st.columns([1, 1, 2])
    with col4:
        score_value = float(item.risk_score) if item.risk_score is not None else 0.0
        new_score = st.slider(
            "Risk score (0=low, 100=critical)",
            min_value=0.0, max_value=100.0, value=score_value, step=5.0,
            key=f"rs_{k}",
        )
        score_for_save: float | None = (
            None if (item.risk_score is None and new_score == 0.0) else new_score
        )
        # If the user moves the slider off 0 explicitly, persist that value
        if new_score != score_value:
            score_for_save = new_score
    with col5:
        new_db = st.checkbox("Dealbreaker flag", value=item.is_dealbreaker_hit, key=f"db_{k}")
    with col6:
        new_db_type = "none"
        if new_db:
            new_db_type = st.radio(
                "Type", options=["hard", "soft"],
                index=["hard", "soft"].index(item.dealbreaker_type or "soft"),
                horizontal=True, key=f"dbt_{k}",
            )

    new_mitigation = ""
    if new_db and new_db_type == "soft":
        new_mitigation = st.text_area(
            "Mitigation plan (required for IC sign-off — min 40 chars)",
            value=item.soft_mitigation,
            key=f"mit_{k}", height=60,
            placeholder="e.g. 'Full plumbing repipe budgeted at $108K (24 units × $4.5K) included in renovation scope.'",
        )

    # Artifacts (manual upload; AI extraction deferred to cloud build)
    st.caption("📎 Artifacts (attach PDFs / reports; AI extraction lands with the cloud build)")
    uploaded = st.file_uploader(
        "Drop a PDF / DOCX / XLSX for this item",
        type=["pdf", "docx", "xlsx", "xls", "csv", "jpg", "png"],
        accept_multiple_files=False,
        key=f"up_{k}",
    )
    if uploaded is not None:
        artifact = _save_artifact(folder, item, uploaded)
        item.artifacts.append(artifact)
        st.success(f"Saved {artifact['filename']}")

    if item.artifacts:
        for a in item.artifacts:
            st.markdown(
                f"- `{a['filename']}` · uploaded {a.get('uploaded_at', '')[:16]}"
                + (f"  \n  > {a['ai_summary']}" if a.get("ai_summary") else "")
            )

    # Save button — applies all the form changes
    col_save, col_delete = st.columns([3, 1])
    with col_save:
        if st.button("Save changes", key=f"sv_{k}", type="primary"):
            item.status = new_status  # type: ignore[assignment]
            item.owner = new_owner
            item.due_date = new_due.isoformat()
            item.notes = new_notes
            item.risk_score = score_for_save
            item.is_dealbreaker_hit = new_db
            item.dealbreaker_type = (None if not new_db else new_db_type)
            item.soft_mitigation = new_mitigation if new_db and new_db_type == "soft" else ""
            dd.save_state(folder, dd.recompute_aggregates(state))
            st.success("Saved.")
            st.rerun()
    with col_delete:
        if st.button("🗑 Delete", key=f"dl_{k}"):
            state.items = [x for x in state.items if x.id != item.id]
            dd.save_state(folder, dd.recompute_aggregates(state))
            st.rerun()


def _render_add_item_form(state: dd.DDState, folder: Path) -> None:
    """Free-form add of a custom DD item not in the seeded checklist."""
    with section_card("Add custom DD item", icon="➕"):
        with st.form(f"dd_add_form_{state.deal_id}"):
            col1, col2 = st.columns(2)
            with col1:
                title = st.text_input("Title", placeholder="e.g. 'Confirm pet policy with PM'")
                owner = st.text_input("Owner", value="brian")
            with col2:
                cat = st.selectbox(
                    "Category",
                    options=list(dd.CATEGORIES),
                    format_func=lambda c: dd.CATEGORY_LABELS[c],
                )
                due = st.date_input("Due", value=dt.date.today() + dt.timedelta(days=14))
            notes = st.text_area("Notes (optional)", height=60)
            submitted = st.form_submit_button("Add item", type="primary")
            if submitted and title.strip():
                import uuid as _uuid
                new_item = dd.DDItem(
                    id=f"custom-{_uuid.uuid4().hex[:6]}",
                    category=cat,
                    title=title.strip(),
                    owner=owner.strip() or "brian",
                    due_date=due.isoformat(),
                    status="pending",
                    notes=notes,
                )
                state.items.append(new_item)
                dd.save_state(folder, dd.recompute_aggregates(state))
                st.session_state[f"dd_show_add_{state.deal_id}"] = False
                st.success(f"Added: {title}")
                st.rerun()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_artifact(folder: Path, item: dd.DDItem, uploaded_file) -> dict:
    """Persist the uploaded file under <folder>/dd-artifacts/<item-id>/<filename>."""
    art_dir = folder / "dd-artifacts" / item.id
    art_dir.mkdir(parents=True, exist_ok=True)
    target = art_dir / uploaded_file.name
    with open(target, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return {
        "filename": uploaded_file.name,
        "stored_path": str(target.relative_to(folder)),
        "uploaded_at": dt.datetime.now().isoformat(timespec="seconds"),
        "size_bytes": target.stat().st_size,
        "ai_summary": None,   # populated by the cloud-side AI extractor later
    }


def _reseed_defaults(state: dd.DDState) -> None:
    """Append any default-checklist items not already in state.items.
    Preserves all existing items + their state. Used after we add new
    default items in code and want to bring existing deals up to date."""
    have_ids = {i.id for i in state.items}
    today = dt.date.today()
    for d in dd.DEFAULT_CHECKLIST:
        if d.id in have_ids:
            continue
        state.items.append(dd.DDItem(
            id=d.id, category=d.category, title=d.title,
            owner=d.default_owner,
            due_date=(today + dt.timedelta(days=d.default_due_offset_days)).isoformat(),
            status="pending",
            risk_factor=d.risk_factor,
        ))
