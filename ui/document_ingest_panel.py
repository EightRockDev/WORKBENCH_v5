"""UI: drop a T-12 / Rent Roll / OM into the Subject tab.

Triggers AI extraction; writes to sources.json; surfaces what was written
with provenance per field so Brian can audit + override.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

import config
from core import document_ingest as di
from ui.components import section_card


def render_document_ingest_panel(prop: dict[str, Any], folder) -> None:
    if folder is None or getattr(folder, "path", None) is None:
        return
    fp = folder.path
    c = config.COLORS

    with section_card(
        "Document Auto-Ingestion",
        icon="🤖",
        accent="ac",
        subtitle=(
            "Drop T-12 / Rent Roll / OM (PDF, XLSX, CSV). AI extracts the "
            "key fields into the workbench with per-field provenance — "
            "90 min → 5 min."
        ),
    ):
        uploaded = st.file_uploader(
            "Drop a document (T-12, rent roll, or OM)",
            type=["pdf", "xlsx", "xls", "csv"],
            accept_multiple_files=False,
            key=f"docing_upload_{fp.name}",
        )

        # Document-type override
        col1, col2 = st.columns([1, 2])
        with col1:
            dt_override = st.selectbox(
                "Type (auto-detect default)",
                options=["auto", "t12", "rent_roll", "om"],
                index=0,
                key=f"docing_type_{fp.name}",
            )
        with col2:
            overwrite = st.checkbox(
                "Overwrite existing fields",
                value=False,
                help="Default: keeps any values that are already filled in. Check this to replace them with what the AI extracts.",
                key=f"docing_overwrite_{fp.name}",
            )

        if uploaded is not None:
            target_dir = fp / "ingest-uploads"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / uploaded.name
            target.write_bytes(uploaded.getbuffer())

            if st.button(
                f"🤖 Extract from {uploaded.name}",
                key=f"docing_run_{fp.name}",
                type="primary",
            ):
                with st.spinner(f"Extracting fields from {uploaded.name}..."):
                    doc_type = None if dt_override == "auto" else dt_override
                    result = di.ingest_document(target, document_type=doc_type)

                if result.error:
                    st.error(f"Extraction failed: {result.error}")
                elif not result.is_success:
                    st.warning("No fields extracted.")
                else:
                    n = di.commit_to_sources_json(fp, result, overwrite=overwrite)
                    st.success(
                        f"Extracted {n} field(s) from {result.source_doc} "
                        f"(type: {result.document_type}). "
                        + ("Saved into the workbench." if n else "No new fields written (check 'Overwrite' to replace existing values).")
                    )
                    if result.extraction_notes:
                        st.caption(f"AI notes: {result.extraction_notes}")
                    _render_qa_report(fp, c)
                    _render_extracted(result.extracted, c)

        # ---- Show ingestion log ----
        _render_ingestion_log(fp, c)


def _render_qa_report(folder: Path, c: dict) -> None:
    """Module E (\u00a76.3): deterministic QA over everything now on file.

    Runs AFTER commit so cross-document ties see the newly-written fields
    alongside earlier uploads. No model calls - pure arithmetic.
    """
    from core.extraction_qa import run_qa
    from data.property_io import load_sources

    sources = load_sources(folder)
    if not sources:
        return
    report = run_qa(sources)
    if not report.checks and not report.low_confidence:
        return

    if report.blocking:
        st.error(
            "\u26d4 Extraction QA: " + report.summary()
            + " \u2014 review before trusting the numbers. A blocking QA "
            "failure holds a GO verdict at WATCH.")
    elif report.failures:
        st.warning("\u26a0 Extraction QA: " + report.summary())
    else:
        st.success("\u2705 Extraction QA: " + report.summary())

    problems = report.failures or report.low_confidence
    if problems:
        with st.expander("QA details", expanded=report.blocking):
            for chk in report.failures:
                st.markdown(f"**{chk.severity.upper()}** \u2014 {chk.title}")
                st.caption(chk.detail)
            for flag in report.low_confidence:
                st.markdown(f"**LOW CONFIDENCE** \u2014 `{flag.key}`")
                st.caption(flag.reason)


def _render_extracted(data: dict, c: dict) -> None:
    """Show the extracted data tree."""
    import pandas as pd
    rows = []
    _flatten_extracted("", data, rows)
    if rows:
        st.markdown(
            f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600;margin-top:12px;margin-bottom:6px">'
            f'Extracted fields</div>',
            unsafe_allow_html=True,
        )
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, use_container_width=True)


def _flatten_extracted(prefix: str, d: Any, rows: list) -> None:
    """Recursively flatten nested extraction dict into table rows."""
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                _flatten_extracted(key, v, rows)
            else:
                rows.append({"Field": key, "Value": _format_value(v)})
    elif isinstance(d, list):
        for i, item in enumerate(d):
            _flatten_extracted(f"{prefix}[{i}]", item, rows)


def _format_value(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if 0 < v < 1:
            return f"{v:.1%}"
        return f"${v:,.0f}"
    if isinstance(v, int) and abs(v) > 100:
        return f"${v:,}"
    return str(v)


def _render_ingestion_log(folder: Path, c: dict) -> None:
    """Show the log of past ingestions for this property."""
    sources_path = folder / "sources.json"
    if not sources_path.is_file():
        return
    try:
        data = json.loads(sources_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    log = (data or {}).get("_ingestion_log") or []
    if not log:
        return

    st.markdown(
        f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600;margin-top:18px;margin-bottom:6px">'
        f'Ingestion history</div>',
        unsafe_allow_html=True,
    )
    for entry in reversed(log[-10:]):
        st.markdown(
            f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
            f'border-radius:4px;padding:6px 10px;margin-bottom:4px;font-size:12px">'
            f'<b>{entry.get("source_doc", "?")}</b> '
            f'<span style="color:{c["tx3"]}">({entry.get("document_type", "?")})</span> '
            f'· {entry.get("extracted_at", "?")[:16]}'
            f'<br><span style="color:{c["tx2"]};font-size:11px">'
            f'{entry.get("fields_written", 0)} fields written'
            f'{" · " + entry.get("extraction_notes", "")[:200] if entry.get("extraction_notes") else ""}'
            f'</span></div>',
            unsafe_allow_html=True,
        )
