"""Print-ready HTML + PDF export for the Acquisition Checklist.

Renders the catalog from `acquisition_checklist_default` plus the user's
per-property state (checked items + notes) into a styled HTML document
that mirrors the source `knowledgebase/acquisition-checklist-04282026.html`
design, then converts to PDF via `xhtml2pdf` (pure Python — no GTK/cairo
system deps, works on Windows out of the box).

xhtml2pdf has CSS limitations vs. modern browsers (no flex, no var(), limited
gradient support), so the HTML here is structured around tables and inline
styles. Output is print-oriented (Letter size, with page breaks between phases).
"""

from __future__ import annotations

import datetime as dt
import html
import io
from typing import Any

from core.acquisition_checklist import (
    ACQUISITION_CHECKLIST,
    AcqChecklistState,
    overall_progress,
    phase_progress,
)

# Inline color tokens (no CSS vars — xhtml2pdf chokes on them)
_INK = "#0f0f0f"
_PAPER = "#f5f2ec"
_GOLD = "#b8922a"
_GOLD_LIGHT = "#e8c96a"
_GOLD_DARK = "#8a6a10"
_RUST = "#c04a2a"
_SLATE = "#3a4a5a"
_MUTED = "#8a8278"
_LINE = "#d8d2c8"
_CHECK_BG = "#e8f4e8"
_CHECK_BORDER = "#4a9a4a"


def _deadline_pill(deadline_type: str, deadline_text: str) -> str:
    if deadline_type == "hard":
        bg, fg, border = "#fde2dc", _RUST, "#e8a092"
    elif deadline_type == "soft":
        bg, fg, border = "#fbeed1", _GOLD_DARK, "#e2c982"
    else:
        bg, fg, border = "#e4eaf0", _SLATE, "#a9b6c4"
    return (
        f'<span style="background:{bg};color:{fg};border:1px solid {border};'
        f'padding:1px 5px;font-size:7.5pt;letter-spacing:0.4px;'
        f'white-space:nowrap">'
        f'{html.escape(deadline_text or deadline_type.upper())}</span>'
    )


def _critical_badge() -> str:
    return (
        f'<span style="background:#f4d4cc;color:{_RUST};'
        f'border:1px solid #e8a092;padding:0 5px;font-size:7.5pt;'
        f'font-weight:700">!</span>'
    )


def _checkbox(checked: bool) -> str:
    if checked:
        return (
            f'<span style="display:inline-block;width:11px;height:11px;'
            f'background:{_CHECK_BORDER};color:#fff;text-align:center;'
            f'font-size:9pt;line-height:11px;font-weight:700">'
            f'&#10003;</span>'
        )
    return (
        f'<span style="display:inline-block;width:11px;height:11px;'
        f'border:1px solid {_LINE};background:#fff"></span>'
    )


def _render_item_row(item: Any, checked: bool, note: str) -> str:
    text_style = (
        f"color:{_MUTED};text-decoration:line-through;"
        if checked else f"color:{_INK};"
    )
    critical = _critical_badge() + "&nbsp;" if item.critical else ""
    note_html = ""
    if note:
        safe_note = html.escape(note).replace("\n", "<br/>")
        note_html = (
            f'<tr><td></td>'
            f'<td colspan="2" style="padding:2px 0 6px 0;font-size:8.5pt;'
            f'color:{_SLATE};font-style:italic;border-left:2px solid {_GOLD};'
            f'padding-left:8px">📝 {safe_note}</td></tr>'
        )
    catalog_note_html = ""
    if item.note:
        safe_cat = html.escape(item.note)
        catalog_note_html = (
            f'<div style="font-size:8pt;color:{_MUTED};font-style:italic;'
            f'margin-top:2px">{safe_cat}</div>'
        )
    return (
        f'<tr style="border-bottom:1px solid {_LINE}">'
        f'<td style="width:18px;padding:5px 8px 5px 0;vertical-align:top">{_checkbox(checked)}</td>'
        f'<td style="padding:5px 0;font-size:9.5pt;line-height:1.4;{text_style}">'
        f'{critical}{html.escape(item.text)}{catalog_note_html}'
        f'</td>'
        f'<td style="padding:5px 0 5px 8px;text-align:right;vertical-align:top">'
        f'{_deadline_pill(item.deadline_type, item.deadline_text)}'
        f'</td>'
        f'</tr>'
        f'{note_html}'
    )


def _render_category(category: Any, state: AcqChecklistState) -> str:
    rows = "".join(
        _render_item_row(it, it.id in state.checked_item_ids, state.notes.get(it.id, ""))
        for it in category.items
    )
    return (
        f'<div style="margin-top:14px">'
        f'<div style="background:#eceae5;border-left:3px solid {_SLATE};'
        f'padding:3px 10px;font-size:8pt;letter-spacing:1.8px;'
        f'text-transform:uppercase;color:{_SLATE};font-weight:700">'
        f'{html.escape(category.label)}</div>'
        f'<table style="width:100%;border-collapse:collapse;margin-top:4px">'
        f'{rows}'
        f'</table>'
        f'</div>'
    )


def _render_phase(phase: Any, state: AcqChecklistState, is_first: bool) -> str:
    pp = phase_progress(state, phase.id)
    pct = pp.pct * 100
    page_break = "" if is_first else 'page-break-before:always;'
    categories_html = "".join(_render_category(c, state) for c in phase.categories)
    return (
        f'<div style="{page_break}margin-top:20px">'
        # Header row
        f'<table style="width:100%;border-collapse:collapse;'
        f'border-bottom:2px solid {_INK};padding-bottom:6px">'
        f'<tr>'
        f'<td style="width:50px;font-size:24pt;font-weight:900;color:{_LINE};'
        f'vertical-align:top">{html.escape(phase.number)}</td>'
        f'<td style="vertical-align:top;padding-left:8px">'
        f'<div style="font-size:8pt;letter-spacing:2px;text-transform:uppercase;'
        f'color:{_MUTED}">{html.escape(phase.tag)}</div>'
        f'<div style="font-size:14pt;font-weight:700;color:{_INK}">{html.escape(phase.title)}</div>'
        f'<div style="display:inline-block;background:#f3ecd6;'
        f'border-left:3px solid {_GOLD};color:{_GOLD_DARK};'
        f'font-size:8.5pt;padding:1px 8px;margin-top:4px">'
        f'{html.escape(phase.timeline)}</div>'
        f'</td>'
        f'<td style="text-align:right;vertical-align:top;width:90px">'
        f'<div style="font-size:8pt;letter-spacing:1.5px;text-transform:uppercase;color:{_MUTED}">Progress</div>'
        f'<div style="font-size:14pt;font-weight:700;color:{_INK}">{pp.done}/{pp.total}</div>'
        f'<div style="font-size:8pt;color:{_MUTED}">{pct:.0f}%</div>'
        f'</td>'
        f'</tr>'
        f'</table>'
        # Summary
        f'<div style="background:{_INK};color:{_PAPER};padding:10px 14px;'
        f'border-left:4px solid {_GOLD};font-size:9pt;line-height:1.5;'
        f'margin-top:8px">{html.escape(phase.summary)}</div>'
        f'{categories_html}'
        f'</div>'
    )


def render_html(
    prop: dict[str, Any],
    state: AcqChecklistState,
) -> str:
    """Render the checklist + state as a print-ready HTML document.

    Args:
        prop: Property dict (name/address/city/state/units/year_built)
        state: AcqChecklistState (checked items + notes)
    """
    o = overall_progress(state)
    today = dt.datetime.now().strftime("%B %d, %Y")
    name = html.escape(prop.get("name") or "Property")
    address_parts = [
        prop.get("address"), prop.get("city"),
        prop.get("state") or "VA", prop.get("zip"),
    ]
    address = html.escape(", ".join(p for p in address_parts if p))
    units = prop.get("units")
    year_built = prop.get("year_built")
    meta_bits = []
    if units:
        meta_bits.append(f"{int(units)} Units")
    if year_built:
        meta_bits.append(f"Built {int(year_built)}")
    if prop.get("asset_class"):
        meta_bits.append(f"Class {prop['asset_class']}")
    meta_html = html.escape(" · ".join(meta_bits))

    phases_html = "".join(
        _render_phase(p, state, is_first=(i == 0))
        for i, p in enumerate(ACQUISITION_CHECKLIST)
    )

    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<style>
@page {{
  size: Letter;
  margin: 0.6in 0.5in 0.5in 0.5in;
}}
body {{
  font-family: Helvetica, Arial, sans-serif;
  font-size: 9.5pt;
  color: {_INK};
  background: {_PAPER};
}}
</style>
</head>
<body>

<!-- Header banner -->
<table style="width:100%;border-collapse:collapse;background:{_INK};color:{_PAPER};margin-bottom:14px">
<tr>
<td style="padding:18px 22px">
<div style="font-size:8pt;letter-spacing:2.5px;text-transform:uppercase;color:{_GOLD}">Eight Rock Capital Partners</div>
<div style="font-size:20pt;font-weight:900;line-height:1.1;margin-top:4px">Acquisition Master Checklist</div>
<div style="font-size:11pt;color:#bbb;margin-top:2px">LOI Acceptance → Close → 90 Days Post-Closing</div>
</td>
<td style="text-align:right;padding:18px 22px;vertical-align:top;width:240px">
<div style="font-size:9pt;color:{_GOLD_LIGHT};font-weight:700">{name}</div>
<div style="font-size:8pt;color:#aaa;margin-top:2px">{address}</div>
<div style="font-size:8pt;color:#aaa;margin-top:2px">{meta_html}</div>
<div style="font-size:8pt;color:#aaa;margin-top:8px">As of: {today}</div>
</td>
</tr>
</table>

<!-- Overall progress strip -->
<table style="width:100%;border-collapse:collapse;background:#eceae5;border:1px solid {_LINE};margin-bottom:14px">
<tr>
<td style="padding:10px 14px;vertical-align:middle">
<table style="width:100%;border-collapse:collapse">
<tr>
<td>
<div style="font-size:8pt;letter-spacing:1.5px;text-transform:uppercase;color:{_MUTED}">Overall</div>
<div style="font-size:14pt;font-weight:700;color:{_INK}">{o.done} / {o.total} <span style="font-size:9pt;color:{_MUTED}">({o.pct*100:.0f}%)</span></div>
</td>
<td style="padding-left:30px">
<div style="font-size:8pt;letter-spacing:1.5px;text-transform:uppercase;color:{_MUTED}">Critical Track</div>
<div style="font-size:14pt;font-weight:700;color:{_RUST if o.critical_done < o.critical_total else _CHECK_BORDER}">{o.critical_done} / {o.critical_total} <span style="font-size:9pt;color:{_MUTED}">({o.critical_pct*100:.0f}%)</span></div>
</td>
</tr>
</table>
</td>
</tr>
</table>

{phases_html}

<div style="margin-top:24px;text-align:center;font-size:7.5pt;color:{_MUTED}">
<strong style="color:{_GOLD_DARK}">Eight Rock Capital Partners</strong> · Acquisition Master Checklist · Based on Lindahl Multi-Family Millions + Eight Rock Underwriting Standards · Norfolk, Virginia
</div>

</body>
</html>'''


def render_pdf(
    prop: dict[str, Any],
    state: AcqChecklistState,
) -> bytes:
    """Render the checklist + state directly to PDF bytes.

    Uses xhtml2pdf (pure Python, no system deps). Raises if the conversion
    produces non-trivial errors; warnings are accepted.
    """
    from xhtml2pdf import pisa

    html_doc = render_html(prop, state)
    buf = io.BytesIO()
    result = pisa.CreatePDF(
        src=io.BytesIO(html_doc.encode("utf-8")),
        dest=buf,
        encoding="utf-8",
    )
    if result.err:
        raise RuntimeError(f"PDF generation failed with {result.err} errors")
    pdf_bytes = buf.getvalue()
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise RuntimeError("PDF generation produced empty or invalid output")
    return pdf_bytes
