"""Shared rent-roll renderer used by both Property Detail and Underwriting tabs.

Reads `rentRoll` block from the property's `sources.json`, renders:

  - 4 summary tiles (Units / Occupied / Vacant / Notice) — counts only,
    no clamp-shrink so they're always readable
  - 4 dollar / sqft tiles (Occupancy% / Total market rent / Total actual
    rent / Avg sqft)
  - Vacant-and-Notice highlight section (always expanded — these are the
    actionable units)
  - Full unit-by-unit table inside an expander, with Notice rows tinted
    yellow and Vacant rows tinted red so the eye finds them fast

The Underwriting tab uses this to give analysts a single-pane view of the
deal-level dials AND the unit-level reality on the same screen.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

import config
from data.property_io import PropertyFolder, load_sources


def _money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _tile(label: str, value: str, accent: str | None = None) -> str:
    """Custom HTML tile with explicit big-font value (no metric clamp)."""
    c = config.COLORS
    border_left = f"border-left:3px solid {accent};" if accent else ""
    return (
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'{border_left}border-radius:6px;padding:10px 14px;min-height:78px;'
        f'display:flex;flex-direction:column;justify-content:center">'
        f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600">{label}</div>'
        f'<div style="font-size:24px;font-weight:700;color:{c["tx"]};'
        f'line-height:1.1;margin-top:4px;font-variant-numeric:tabular-nums">'
        f'{value}</div></div>'
    )


def _row_status_color(status: str) -> str | None:
    """Return a row background tint based on the unit's lease status, or
    None for default (Current).

    Tints are tuned for the light theme (white card bg) — opacity 0.30 so
    they read clearly. Solid colors would be too loud.
    """
    if not status:
        return None
    s = status.lower()
    if "vacant" in s:
        return "rgba(220,38,38,0.20)"   # red tint — empty unit
    if "notice" in s:
        return "rgba(217,119,6,0.20)"   # amber tint — leaving soon
    if "future" in s:
        return "rgba(37,99,235,0.15)"   # blue tint — pre-leased
    return None


def _style_units_df(df: pd.DataFrame) -> Any:
    """Apply row tinting based on the lease-status column. Returns a Styler
    so `st.dataframe(styled)` shows the tinted rows.

    Bug fix 2026-05-08: `_prepare_units_df` renames the source `status`
    column to `Status` (capital S) for display. The styler now looks for
    BOTH names so the tinting renders regardless of which version it's
    given. Also bumped tint opacity from 0.18 → 0.30 so the colors are
    visible against the white card bg in the new light theme.
    """
    def _style_row(row):
        # Try both casings — display df uses "Status", raw rentRoll uses "status"
        status_value = row.get("Status") if "Status" in row.index else row.get("status", "")
        color = _row_status_color(str(status_value or ""))
        if color:
            return [f"background-color: {color}"] * len(row)
        return [""] * len(row)
    return df.style.apply(_style_row, axis=1)


# Columns we want in the unit-detail table, in order, with display names.
_UNIT_DISPLAY_COLS: list[tuple[str, str]] = [
    ("unit", "Unit"),
    ("unitType", "Type"),
    ("status", "Status"),
    ("tenant", "Tenant"),
    ("sqft", "SqFt"),
    ("marketRent", "Market $"),
    ("actualRent", "Actual $"),
    ("totalCharges", "Total $"),
    ("isMTM", "MTM"),
    ("moveIn", "Move-in"),
    ("leaseExp", "Lease exp"),
    ("moveOut", "Move-out"),
]


def _prepare_units_df(units: list[dict[str, Any]]) -> pd.DataFrame:
    """Take the raw rentRoll.units list, return a display-ready DataFrame
    with proper column order, dollar formatting, and date display."""
    df = pd.DataFrame(units)
    if df.empty:
        return df

    # Build a fresh dataframe with the columns we want, in our order
    out = pd.DataFrame()
    for src, dst in _UNIT_DISPLAY_COLS:
        if src in df.columns:
            out[dst] = df[src]
    # Format dollar columns
    for col in ("Market $", "Actual $", "Total $"):
        if col in out.columns:
            out[col] = out[col].apply(
                lambda v: f"${float(v):,.0f}" if pd.notna(v) and v != 0 else "—"
            )
    if "SqFt" in out.columns:
        out["SqFt"] = out["SqFt"].apply(
            lambda v: f"{int(v):,}" if pd.notna(v) and v != 0 else "—"
        )
    if "MTM" in out.columns:
        out["MTM"] = out["MTM"].apply(lambda v: "✓" if v else "")
    return out


def render_rent_roll(
    folder: PropertyFolder | None,
    *,
    section_title: str | None = "Rent Roll",
    show_summary: bool = True,
    expand_units: bool = False,
) -> None:
    """Render the rent roll block. Returns silently if no rent roll on disk.

    Args:
      folder: the property's on-disk folder. None → silent no-op.
      section_title: heading text. Property Detail uses "Rent Roll Summary";
        Underwriting uses "Rent Roll" so both tabs read consistently.
      show_summary: render the 8 summary tiles (Units/Occupied/Vacant/etc.).
      expand_units: render the full unit-detail table expanded by default.
        Property Detail keeps it collapsed (subordinate to property info);
        Underwriting expands it (you need it visible while you tune dials).
    """
    if folder is None:
        return
    sources = load_sources(folder.path)
    if not sources or "rentRoll" not in sources:
        return

    rr = sources["rentRoll"]
    summary = rr.get("summary", {}) or {}
    units = rr.get("units", []) or []

    c = config.COLORS

    # Section header + provenance. Bigger, bolder source line with Excel
    # icon (per Brian 2026-05-08): the rent roll's source file is the
    # single most important "where did this data come from" cue on the
    # whole tab — make it readable without scrolling.
    rr_date = rr.get("date") or "—"
    rr_file = rr.get("file") or ""
    # `section_title=None` (or "") → caller has already provided a heading
    # via `section_card(...)`, so we skip the inner h5 to avoid duplicate
    # titling (e.g. comps.py wraps this call in a card titled "Rent Roll").
    if section_title:
        st.markdown(f"##### {section_title}")
    if rr_file:
        source_html = (
            f'<div style="font-size:15px;font-weight:600;color:{c["tx"]};'
            f'margin-bottom:10px;line-height:1.5">'
            f'{config.EXCEL_ICON_HTML}'
            f'<span>from <code style="background:{c["bg3"]};padding:2px 6px;'
            f'border-radius:3px;font-size:13px;color:{c["tx"]}">{rr_file}</code> '
            f'<span style="color:{c["tx2"]};font-weight:500">· effective '
            f'{rr_date}</span></span></div>'
        )
    else:
        source_html = (
            f'<div style="font-size:15px;font-weight:600;color:{c["tx"]};'
            f'margin-bottom:10px;line-height:1.5">'
            f'{config.EXCEL_ICON_HTML}'
            f'<span>effective {rr_date}</span></div>'
        )
    st.markdown(source_html, unsafe_allow_html=True)

    if show_summary:
        # Row 1 — counts.
        # Occupancy convention (industry standard for multifamily):
        #   - "Occupied" = currently paying tenants, INCLUDING those on notice
        #     (notice tenants haven't moved out yet and are still paying rent).
        #   - "Vacant" = the only thing that drives the occupancy % calc.
        #   - "On Notice" is rendered separately as an at-risk indicator
        #     (subset of Occupied, NOT additive). The source's `summary.occupied`
        #     field counts only "Current" status — so we add `notice` back in
        #     for the display number.
        raw_occupied = summary.get("occupied") or 0
        notice = summary.get("notice") or 0
        vacant = summary.get("vacant") or 0
        total = summary.get("totalUnits") or 0
        occupied_display = raw_occupied + notice  # incl. notice tenants

        col1, col2, col3, col4 = st.columns(4)
        col1.markdown(_tile("Units", _int(total)), unsafe_allow_html=True)
        col2.markdown(
            _tile("Occupied", _int(occupied_display), accent=c["gn"]),
            unsafe_allow_html=True,
        )
        col3.markdown(_tile("Vacant", _int(vacant), accent=c["rd"]), unsafe_allow_html=True)
        col4.markdown(
            _tile(
                "On Notice",
                _int(notice),
                accent=c["yw"],
            ),
            unsafe_allow_html=True,
        )
        if notice:
            st.caption(
                f"📎 **Occupied** counts all rent-paying units ({raw_occupied} current "
                f"+ {notice} on-notice). On-notice tenants are a subset of Occupied — "
                f"they're still paying rent until they vacate."
            )

        # Row 2 — financials
        col5, col6, col7, col8 = st.columns(4)
        col5.markdown(_tile("Occupancy", _pct(summary.get("occupancyPct"))), unsafe_allow_html=True)
        col6.markdown(_tile("Total market rent", _money(summary.get("totalMarketRent"))), unsafe_allow_html=True)
        col7.markdown(_tile("Total actual rent", _money(summary.get("totalActualRent"))), unsafe_allow_html=True)
        col8.markdown(_tile("Avg sqft", _int(summary.get("avgSqft"))), unsafe_allow_html=True)

    if not units:
        return

    # ---- Action panel: vacant + notice units (always shown if any exist) ----
    actionable = [
        u for u in units
        if str(u.get("status", "")).lower() in ("vacant", "notice")
    ]
    if actionable:
        n_vacant = sum(1 for u in actionable if str(u.get("status", "")).lower() == "vacant")
        n_notice = sum(1 for u in actionable if str(u.get("status", "")).lower() == "notice")
        bits = []
        if n_vacant:
            bits.append(f"<span style='color:{c['rd']};font-weight:700'>{n_vacant} vacant</span>")
        if n_notice:
            bits.append(f"<span style='color:{c['yw']};font-weight:700'>{n_notice} on notice</span>")
        st.markdown(
            f'<div style="margin-top:14px;color:{c["tx"]};font-size:14px;font-weight:600">'
            f'⚠️ Action items — {" · ".join(bits)}</div>',
            unsafe_allow_html=True,
        )
        action_df = _prepare_units_df(actionable)
        if not action_df.empty:
            st.dataframe(
                _style_units_df(action_df),
                use_container_width=True,
                hide_index=True,
            )

    # ---- Full unit-by-unit table ----
    full_df = _prepare_units_df(units)
    if not full_df.empty:
        with st.expander(
            f"Full unit-by-unit detail ({len(units)} units · 🟥 vacant · 🟧 notice)",
            expanded=expand_units,
        ):
            st.dataframe(
                _style_units_df(full_df),
                use_container_width=True,
                hide_index=True,
            )
