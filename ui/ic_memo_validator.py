"""IC Memo Validator tab — pre-IC audit on generated artifacts.

UI flow:
  1. User picks an existing generated artifact from the property folder
     (executive summary, investor memos, value-add strategy, or LOI).
  2. Optionally toggles AI voice check against Brian-approved Templates samples.
  3. Click "Run validation" → severity-banded findings (Critical / Warning / Info)
     surface on the page, with a downloadable JSON report.

Wires `core.ic_memo_validator` against the same briefing produced by
`core.artifact_engine._build_briefing()` plus the property's DD state.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

import streamlit as st

import config
from core import due_diligence as dd
from core.artifact_engine import (
    ARTIFACT_CATALOG,
    _build_briefing,
    get_artifact_spec,
    list_generated_artifacts,
)
from core.ic_memo_validator import (
    ValidationReport,
    extract_docx_text,
    validate,
)
from data.property_io import PropertyFolder, load_deal
from ui.components import section_card, subsection_card

# Workbench root (Templates/ lives there). Three parents up from this file:
# python_workbench/ui/ic_memo_validator.py → workbench root
_WORKBENCH_ROOT = Path(__file__).resolve().parents[2]


def _severity_style(severity: str) -> tuple[str, str, str]:
    """Return (bg, border, fg) hex colors for a finding pill."""
    c = config.COLORS
    if severity == "critical":
        return c["rdbg"], c["rdbrd"], c["rd"]
    if severity == "warning":
        return "#fef3c7", "#fcd34d", c["yw"]   # amber band (no existing token)
    return c["blbg"], "#93c5fd", c["bl"]


def _severity_icon(severity: str) -> str:
    return {"critical": "🛑", "warning": "⚠️", "info": "ℹ️"}.get(severity, "•")


def _render_finding(idx: int, finding: Any) -> None:
    bg, border, fg = _severity_style(finding.severity)
    icon = _severity_icon(finding.severity)
    c = config.COLORS
    expected_html = ""
    actual_html = ""
    if finding.expected is not None or finding.actual is not None:
        rows = []
        if finding.expected is not None:
            rows.append(f"<b>Expected:</b> {finding.expected}")
        if finding.actual is not None:
            rows.append(f"<b>Actual:</b> {finding.actual}")
        expected_html = (
            f'<div style="font-size:12px;color:{c["tx2"]};margin-top:6px">'
            + " · ".join(rows)
            + "</div>"
        )
    section_html = (
        f'<span style="font-size:11px;color:{c["tx3"]};margin-left:6px">'
        f'[{finding.section}]</span>'
        if finding.section else ""
    )
    st.markdown(
        f'<div style="background:{bg};border:1px solid {border};border-radius:6px;'
        f'padding:10px 12px;margin-bottom:8px">'
        f'<div style="font-size:13px;font-weight:600;color:{fg}">'
        f'{icon} {finding.title}'
        f'<span style="font-size:11px;color:{c["tx3"]};margin-left:8px;font-weight:400">'
        f'[{finding.category}]</span>{section_html}'
        f'</div>'
        f'<div style="font-size:13px;color:{c["tx"]};margin-top:4px;line-height:1.5">'
        f'{finding.message}</div>'
        f'{expected_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_report(report: ValidationReport) -> None:
    c = config.COLORS
    if report.overall_ready:
        bar_bg, bar_fg = c["gnbg"], c["gn"]
    else:
        bar_bg, bar_fg = c["rdbg"], c["rd"]
    st.markdown(
        f'<div style="background:{bar_bg};color:{bar_fg};border:1px solid {bar_fg};'
        f'padding:10px 14px;border-radius:6px;margin-bottom:14px;font-size:14px;font-weight:600">'
        f'{report.summary}</div>',
        unsafe_allow_html=True,
    )

    sections = (
        ("Critical", report.critical),
        ("Warnings", report.warnings),
        ("Info", report.info),
    )
    any_finding = False
    for label, findings in sections:
        if not findings:
            continue
        any_finding = True
        with subsection_card(f"{label} ({len(findings)})"):
            for i, f in enumerate(findings):
                _render_finding(i, f)
    if not any_finding:
        st.markdown(
            f'<div style="color:{c["tx2"]};font-size:13px;padding:8px">'
            f'No findings — memo passed every check.</div>',
            unsafe_allow_html=True,
        )

    payload = json.dumps(report.to_dict(), indent=2, default=str)
    today = dt.datetime.now().strftime("%m%d%Y")
    st.download_button(
        "⬇️ Download JSON report",
        data=payload,
        file_name=f"ic-memo-validation-{report.artifact_path.stem}-{today}.json",
        mime="application/json",
        use_container_width=True,
    )


def render_ic_memo_validator(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    if folder is None:
        st.info(
            "No property folder yet. Open the Underwriting tab and adjust sliders "
            "to create a deal first, then generate an artifact on the Exec Summary tab."
        )
        return

    # Find all generated artifacts in the folder, grouped by type.
    generated = list_generated_artifacts(folder)
    flat: list[tuple[str, Path]] = []
    for type_id, paths in generated.items():
        for p in paths:
            flat.append((type_id, p))
    # Sort newest first across all types
    flat.sort(key=lambda x: x[1].stat().st_mtime if x[1].is_file() else 0, reverse=True)

    with section_card(
        "IC Memo Validator",
        icon="🛡️",
        accent="ac",
        subtitle=(
            "Audits a generated artifact against calibrated thresholds, the verdict engine, "
            "the DD-readiness gate, and (optionally) Brian-approved voice samples. "
            "Surfaces critical issues that should block IC submission."
        ),
    ):
        if not flat:
            st.info(
                "No generated artifacts in this property folder yet. Use the **Exec Summary** "
                "tab to generate an Executive Summary or Investor Memo first, then return here "
                "to validate it before IC."
            )
            return

        col_pick, col_voice = st.columns([3, 1])
        with col_pick:
            options = [
                f"{get_artifact_spec(t).icon if get_artifact_spec(t) else '📄'} "
                f"{get_artifact_spec(t).label if get_artifact_spec(t) else t}  ·  {p.name}"
                for (t, p) in flat
            ]
            idx = st.selectbox(
                "Artifact to validate",
                options=range(len(flat)),
                format_func=lambda i: options[i],
                key="ic_memo_validator_pick",
            )
            artifact_type, artifact_path = flat[idx]
        with col_voice:
            run_voice = st.checkbox(
                "AI voice check",
                value=False,
                help=(
                    "Run an additional LLM audit comparing the memo's tone/structure "
                    "to Brian-approved samples in Templates/. Costs API tokens."
                ),
                key="ic_memo_validator_voice",
            )

        spec = get_artifact_spec(artifact_type)
        if spec:
            st.markdown(
                f'<div style="font-size:12px;color:{config.COLORS["tx2"]};'
                f'margin:-6px 0 8px 0">'
                f'{spec.description}</div>',
                unsafe_allow_html=True,
            )

        run = st.button(
            "🛡️ Run validation",
            type="primary",
            use_container_width=True,
            key="ic_memo_validator_run",
        )

        if run:
            deal = load_deal(folder.path)
            if deal is None:
                st.error(
                    "No saved underwriting for this property yet. Open the "
                    "Underwriting tab and adjust the sliders first — the "
                    "validator needs the dialed numbers."
                )
                return

            try:
                briefing = _build_briefing(prop, deal, folder)
            except Exception as e:  # noqa: BLE001 — surface as UI error
                st.error(
                    f"Could not build briefing: {type(e).__name__}: {e}. "
                    "The validator needs a successful briefing build to cross-check numbers."
                )
                return

            # Load DD state if dd.json exists
            try:
                dd_state = dd.load_state(folder.path)
            except Exception:
                dd_state = None

            with st.spinner("Auditing memo…" + (" (AI voice check enabled)" if run_voice else "")):
                report = validate(
                    artifact_path=artifact_path,
                    artifact_type=artifact_type,
                    briefing=briefing,
                    dd_state=dd_state,
                    workbench_root=_WORKBENCH_ROOT,
                    run_ai_voice_check=run_voice,
                )

            _render_report(report)


__all__ = ["render_ic_memo_validator"]
