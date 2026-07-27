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

        doc_type = None if dt_override == "auto" else dt_override

        if uploaded is not None:
            payload = bytes(uploaded.getbuffer())
            if not payload:
                # The browser sent a 0-byte stub (cloud-only OneDrive file or
                # a drag straight out of an email preview). No website can
                # read those - but THIS app runs on the same machine as the
                # file, so the from-disk picker below reads it directly.
                st.error(
                    f"**The browser sent {uploaded.name} as 0 bytes** (this "
                    "happens with OneDrive cloud-only files and email "
                    "drag-outs - the content never reaches any website). "
                    "**Use \"Pull from this computer\" below instead** - it "
                    "reads the file straight from disk and doesn't care "
                    "where it's stored.")
            else:
                target_dir = fp / "ingest-uploads"
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / uploaded.name
                target.write_bytes(payload)
                if st.button(
                    f"🤖 Extract from {uploaded.name}",
                    key=f"docing_run_{fp.name}",
                    type="primary",
                ):
                    _run_extraction(target, doc_type, overwrite, fp, c)

        _render_disk_picker(fp, c, doc_type, overwrite)

        # ---- Show ingestion log ----
        _render_ingestion_log(fp, c)


_DOC_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv", ".pdf")


def _run_extraction(target: Path, doc_type, overwrite: bool, fp: Path, c: dict) -> None:
    """Ingest one on-disk file and render the outcome. Shared by the
    browser-upload path and the from-disk picker."""
    with st.spinner(f"Extracting fields from {target.name}..."):
        result = di.ingest_document(target, document_type=doc_type)

    if result.error and result.error.startswith("NEEDS_API_KEY"):
        _render_needs_api_key(result.error.split(": ", 1)[-1], fp)
    elif result.error and result.error.startswith("EMPTY_FILE"):
        st.error("Nothing to extract - " + result.error.split(": ", 1)[-1])
    elif result.error:
        st.error(f"Extraction failed: {result.error}")
    elif not result.is_success:
        st.warning("No fields extracted.")
    else:
        n = di.commit_to_sources_json(fp, result, overwrite=overwrite)
        st.success(
            f"Extracted {n} field(s) from {result.source_doc} "
            f"(type: {result.document_type}). "
            + ("Saved into the workbench." if n else
               "No new fields written (check 'Overwrite' to replace existing values).")
        )
        if result.extraction_notes:
            prefix = ("Parser" if "no AI used" in result.extraction_notes
                      else "AI notes")
            st.caption(f"{prefix}: {result.extraction_notes}")
        _render_qa_report(fp, c)
        _render_extracted(result.extracted, c)


def _candidate_files(root: Path, limit: int = 30) -> list[Path]:
    """Documents under `root` (one level of subfolders), newest first."""
    hits: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.is_file() and entry.suffix.lower() in _DOC_SUFFIXES:
                hits.append(entry)
            elif entry.is_dir() and not entry.name.startswith("."):
                for sub in entry.iterdir():
                    if sub.is_file() and sub.suffix.lower() in _DOC_SUFFIXES:
                        hits.append(sub)
        except OSError:
            continue
    hits.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return hits[:limit]


def _default_scan_roots(fp: Path) -> list[Path]:
    """Places documents usually land on the host, in scan order."""
    roots = [fp]                                   # the property folder itself
    home = Path.home()
    for name in ("Downloads", "Desktop", "Documents"):
        d = home / name
        if d.is_dir():
            roots.append(d)
    return roots


def _render_disk_picker(fp: Path, c: dict, doc_type, overwrite: bool) -> None:
    """Read a document straight from this computer's disk - no browser upload.

    Exists because the browser cannot read cloud-only OneDrive placeholders
    or files dragged from an email preview (they arrive as 0 bytes). The app
    runs on the same machine as the files, so a direct disk read sidesteps
    the browser entirely - and opening a OneDrive placeholder from Python
    makes Windows download the real content automatically.
    """
    with st.expander("📂 Pull from this computer instead (no upload needed)",
                     expanded=False):
        st.caption(
            "Reads the file straight from disk - works no matter where it's "
            "stored (OneDrive included). Showing the newest documents from "
            "the property folder, Downloads, Desktop and Documents; or paste "
            "any full path.")
        typed = st.text_input(
            "File or folder path (optional)",
            key=f"docing_disk_path_{fp.name}",
            placeholder=r"C:\Users\you\Downloads\Crossroads T12.xlsx  (or a folder)",
        )

        typed_path = Path(typed.strip().strip('"')) if typed.strip() else None
        if typed_path is not None and typed_path.is_file():
            candidates = [typed_path]
        elif typed_path is not None and typed_path.is_dir():
            candidates = _candidate_files(typed_path)
            if not candidates:
                st.warning(f"No documents (.xlsx/.xls/.csv/.pdf) found in {typed_path}")
        elif typed_path is not None:
            st.warning(f"Path not found: {typed_path}")
            candidates = []
        else:
            candidates = []
            seen: set = set()
            for root in _default_scan_roots(fp):
                for f in _candidate_files(root, limit=10):
                    r = str(f.resolve())
                    if r not in seen:
                        seen.add(r)
                        candidates.append(f)
            candidates = candidates[:30]

        if not candidates:
            return

        def _label(f: Path) -> str:
            try:
                kb = f.stat().st_size / 1024
                stamp = dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                kb, stamp = 0, "?"
            return f"{f.name}  ({kb:,.0f} KB, {stamp})"

        chosen = st.selectbox(
            "Pick the document", candidates, format_func=_label,
            key=f"docing_disk_pick_{fp.name}")
        if st.button("🤖 Extract from this file", type="primary",
                     key=f"docing_disk_run_{fp.name}"):
            source = Path(chosen)
            try:
                payload = source.read_bytes()
            except OSError as exc:
                st.error(f"Could not read {source.name}: {exc}")
                return
            if not payload:
                st.error(
                    f"{source.name} really is 0 bytes on disk. If it lives in "
                    "OneDrive, right-click it in File Explorer and choose "
                    "'Always keep on this device', wait for the green check, "
                    "then try again.")
                return
            # Copy into the property folder so the doc travels with the deal.
            target_dir = fp / "ingest-uploads"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            if source.resolve() != target.resolve():
                target.write_bytes(payload)
            _run_extraction(target, doc_type, overwrite, fp, c)


def _render_needs_api_key(reason: str, fp: Path) -> None:
    """Actionable no-key panel: explain what still works without a key, and
    let the operator paste one right here (saved to .env, never committed)."""
    st.warning(
        "\U0001f512 This document needs AI extraction \u2014 " + reason + ". "
        "Excel/CSV rent rolls and T-12s parse automatically without a key; "
        "PDFs and unusual layouts use Claude and need one.")
    with st.form(f"docing_key_{fp.name}", clear_on_submit=False):
        st.markdown(
            "Get a key at [console.anthropic.com](https://console.anthropic.com/settings/keys), "
            "paste it below, and re-run the extraction. It is stored in the "
            "server's local `.env` (gitignored) \u2014 one time, all features.")
        entered = st.text_input("Anthropic API key", type="password",
                                key=f"docing_key_input_{fp.name}",
                                placeholder="sk-ant-...")
        if st.form_submit_button("Save key", type="primary"):
            cleaned = (entered or "").strip()
            if not cleaned.startswith("sk-") or len(cleaned) < 20:
                st.error("That doesn't look like an Anthropic key "
                         "(they start with sk-). Nothing saved.")
            else:
                import os
                from ui.exec_summary import _save_api_key_to_env
                env_path = Path(__file__).resolve().parent.parent / ".env"
                _save_api_key_to_env(env_path, cleaned)
                os.environ["ANTHROPIC_API_KEY"] = cleaned
                st.success("Key saved. Click the Extract button again.")


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
