"""Market Calibration panel — surfaces every dynamic threshold with its
current value, source, as-of date, floor, and 30/90/365-day movement.

Renders inside the Underwriting tab above the deal dials. Lets Brian see at
a glance whether the bars he's about to underwrite against are at the locked
floor or have been market-widened, and which way they're trending.

Override controls:
    Each row has a "Override" expander where Brian can pin a manual value
    below the floor (the only way to compress a threshold). Writes to
    `calibration_current.override_value` via `core.calibration.set_override`.
    Overrides survive subsequent Monday cron re-applies.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

import streamlit as st

import config
from core import calibration
from ui.components import section_card


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_market_calibration_panel(subject_city: str | None = None) -> None:
    """Top-level renderer. Call from the Underwriting tab.

    Args:
        subject_city: If provided, PPU ceiling rows are scoped to this city
            only (e.g. "Norfolk"). Other categories always show in full.
            Default None shows every city's PPU rows (legacy behavior).
    """
    c = config.COLORS

    thresholds = calibration.get_all_thresholds()
    if not thresholds:
        return

    # Scope PPU rows to the subject city only — showing all 7 Hampton Roads
    # cities is noise when underwriting a specific deal. Match by display
    # label prefix (e.g. "Norfolk GO PPU Ceiling" starts with "Norfolk ").
    if subject_city:
        prefix = f"{subject_city} "
        thresholds = [
            t for t in thresholds
            if t.category != "ppu" or t.display_label.startswith(prefix)
        ]

    # Aggregate stats for the section subtitle
    n_total = len(thresholds)
    n_market = sum(1 for t in thresholds if t.effective_source == "market")
    n_override = sum(1 for t in thresholds if t.effective_source == "override")
    n_floor = n_total - n_market - n_override

    # Find the most-recent apply timestamp across all thresholds
    last_applied = max(
        (t.last_apply_at for t in thresholds if t.last_apply_at is not None),
        default=None,
    )
    sub_parts = []
    if last_applied is not None:
        age = (dt.datetime.now() - last_applied).days
        if age == 0:
            sub_parts.append("Recalibrated today")
        elif age == 1:
            sub_parts.append("Recalibrated yesterday")
        else:
            sub_parts.append(f"Recalibrated {age} days ago")
    sub_parts.append(
        f"{n_market} market-widened · {n_override} overridden · {n_floor} at floor"
    )
    subtitle = " · ".join(sub_parts)

    with section_card(
        "Market Calibration",
        icon="📐",
        accent="ac",
        subtitle=subtitle,
        help_anchor="calibration",
        help_summary=(
            "Your underwriting bars (GO/WATCH/NO-GO cap rates, debt yield, "
            "vacancy, per-unit ceilings) refresh every Monday from FRED + "
            "property records + assessor data. Floor-locked. Click for the full Help "
            "section."
        ),
    ):
        # Group rows by category for readability
        by_cat: dict[str, list[calibration.Threshold]] = {}
        for t in thresholds:
            by_cat.setdefault(t.category, []).append(t)

        order = ("returns", "debt", "operating", "ppu")
        label_for = {
            "returns": "Verdict bars",
            "debt": "Debt + refi",
            "operating": "Operating",
            "ppu": "Per-unit price ceilings",
        }
        for cat in order:
            rows = by_cat.get(cat, [])
            if not rows:
                continue
            st.markdown(
                f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
                f'letter-spacing:0.8px;font-weight:700;margin-top:6px;'
                f'margin-bottom:6px">{label_for[cat]}</div>',
                unsafe_allow_html=True,
            )
            for t in rows:
                _render_row(t)

        # Footer with last-pull provenance + manual recalibrate button
        st.markdown(
            f'<div style="margin-top:14px;padding-top:10px;'
            f'border-top:1px solid {c["bdr"]};font-size:11px;color:{c["tx3"]}">'
            f'Calibration is recomputed Monday 6:53 AM via the '
            f'<code>etl-weekly-monday</code> cron. '
            f'Floor values are locked in <code>config.py</code> per Brian\'s '
            f'ratified 2026-05-06 conventions. Market data may only widen '
            f'thresholds in the conservative direction; compression below '
            f'floor requires an explicit override.'
            f'</div>',
            unsafe_allow_html=True,
        )
        col_a, col_b, _ = st.columns([1, 2, 4])
        with col_a:
            if st.button("↻ Recalibrate now", key="recalibrate_now_btn"):
                with st.spinner("Recomputing thresholds from latest ETL data…"):
                    calibration.apply_calibration()
                st.success("Recalibrated. Refreshing…")
                st.rerun()
        with col_b:
            st.caption(
                "Forces an immediate re-pull. Otherwise waits for Monday cron."
            )


# ---------------------------------------------------------------------------
# Row renderer — one card per threshold
# ---------------------------------------------------------------------------

def _render_row(t: calibration.Threshold) -> None:
    c = config.COLORS

    # Resolve badge color + label for effective_source
    src_badge_color, src_badge_label = _badge_for_source(t)

    # Movement vs 30 / 90 / 365 days
    d30 = calibration.delta_bps_over(t.name, 30)
    d90 = calibration.delta_bps_over(t.name, 90)
    d365 = calibration.delta_bps_over(t.name, 365)

    # Current value (large, tabular-num)
    val_str = t.format_value()
    floor_str = calibration._format_for_units(t.floor_value, t.units)

    # Subtle direction-of-conservatism arrow next to the floor reference
    if t.direction == "conservative_up":
        if t.effective_value > t.floor_value + 1e-9:
            arrow_text = f"↑ from floor {floor_str}"
            arrow_color = c["gn"]
        elif t.effective_value < t.floor_value - 1e-9:
            arrow_text = f"↓ from floor {floor_str} (override)"
            arrow_color = c["rd"]
        else:
            arrow_text = f"at floor {floor_str}"
            arrow_color = c["tx3"]
    else:  # conservative_down (PPU)
        if t.effective_value < t.floor_value - 1e-9:
            arrow_text = f"↓ from floor {floor_str}"
            arrow_color = c["gn"]
        elif t.effective_value > t.floor_value + 1e-9:
            arrow_text = f"↑ from floor {floor_str} (override)"
            arrow_color = c["rd"]
        else:
            arrow_text = f"at floor {floor_str}"
            arrow_color = c["tx3"]

    # As-of date for market_value
    as_of_str = (
        t.market_as_of.isoformat() if t.market_as_of else "—"
    )

    # Row card -----------------------------------------------------------------
    with st.container(border=True):
        col_label, col_value, col_movement, col_action = st.columns([3, 2, 3, 1])

        # ---- left: label + source description
        with col_label:
            st.markdown(
                f'<div style="font-weight:700;font-size:14px;color:{c["tx"]}">'
                f'{t.display_label}'
                f'<span style="background:{src_badge_color};color:#fff;'
                f'font-size:10px;font-weight:600;padding:2px 7px;border-radius:8px;'
                f'margin-left:8px;vertical-align:middle">{src_badge_label}</span>'
                f'</div>'
                f'<div style="font-size:11px;color:{c["tx3"]};margin-top:3px;'
                f'line-height:1.45">'
                f'{(t.market_source or t.notes or "—")[:140]}'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ---- center: current effective value + floor reference
        with col_value:
            st.markdown(
                f'<div style="font-size:22px;font-weight:700;color:{c["tx"]};'
                f'font-variant-numeric:tabular-nums;line-height:1.1">'
                f'{val_str}</div>'
                f'<div style="font-size:11px;color:{arrow_color};'
                f'margin-top:2px;font-weight:600">{arrow_text}</div>',
                unsafe_allow_html=True,
            )

        # ---- right: 30/90/365-day movement + as-of
        with col_movement:
            st.markdown(
                _delta_chip_row(t, d30, d90, d365)
                + f'<div style="font-size:10px;color:{c["tx3"]};margin-top:4px">'
                f'market as-of {as_of_str}</div>',
                unsafe_allow_html=True,
            )

        with col_action:
            # Per-threshold override controls
            with st.popover("⋯", help="Set override / clear override"):
                _render_override_controls(t)


def _delta_chip_row(
    t: calibration.Threshold,
    d30: float | None,
    d90: float | None,
    d365: float | None,
) -> str:
    c = config.COLORS
    chips: list[str] = []
    for label, d in (("30d", d30), ("90d", d90), ("365d", d365)):
        chips.append(_one_chip(t, label, d, c))
    return (
        '<div style="display:flex;gap:6px;font-variant-numeric:tabular-nums">'
        + "".join(chips)
        + "</div>"
    )


def _one_chip(
    t: calibration.Threshold,
    label: str,
    delta: float | None,
    c: dict,
) -> str:
    if delta is None:
        text = "—"
        color = c["tx3"]
        bg = c["bg3"]
    else:
        # Movement in the conservative direction = green; relaxing = amber
        widening = (
            (delta > 0 and t.direction == "conservative_up")
            or (delta < 0 and t.direction == "conservative_down")
        )
        if abs(delta) < (0.5 if t.units in ("pct", "ratio") else 10):
            color = c["tx3"]
            bg = c["bg3"]
        elif widening:
            color = c["gn"]
            bg = c["gnbg"]
        else:
            color = c["yw"]
            bg = "#fef3c7"
        text = _format_delta(delta, t.units)
    return (
        f'<div style="background:{bg};color:{color};font-size:11px;'
        f'font-weight:700;padding:3px 7px;border-radius:6px;text-align:center;'
        f'min-width:62px">'
        f'<div style="font-size:9px;color:{c["tx3"]};font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.4px">{label}</div>'
        f'{text}</div>'
    )


def _format_delta(delta: float, units: str) -> str:
    if units in ("pct", "ratio"):
        # delta is in bps
        sign = "+" if delta > 0 else ""
        return f"{sign}{delta:.0f}bp"
    if units == "usd":
        sign = "+" if delta > 0 else ""
        return f"{sign}${delta:,.0f}"
    return f"{delta:+.2f}"


def _badge_for_source(t: calibration.Threshold) -> tuple[str, str]:
    c = config.COLORS
    if t.effective_source == "market":
        return c["bl"], "MARKET"
    if t.effective_source == "override":
        return c["rd"], "OVERRIDE"
    return c["tx3"], "FLOOR"


def _render_override_controls(t: calibration.Threshold) -> None:
    """Set / clear override for a single threshold. Brian-only path to
    compress a threshold below its locked floor."""
    c = config.COLORS

    if t.effective_source == "override":
        st.markdown(
            f"**Currently overridden**  \n"
            f"Value: `{t.format_value()}`  \n"
            f"Reason: {t.override_reason or '—'}  \n"
            f"Set: {t.override_set_at} by {t.override_set_by or 'unknown'}"
        )
        if st.button(
            "Clear override",
            key=f"clear_override_{t.name}",
            type="secondary",
        ):
            calibration.clear_override(t.name)
            st.rerun()
        return

    st.markdown(
        f"**Set override for {t.display_label}**  \n"
        f"<small style='color:{c['tx3']}'>"
        f"Floor: {calibration._format_for_units(t.floor_value, t.units)} · "
        f"Market: {calibration._format_for_units(t.market_value, t.units) if t.market_value is not None else '—'} · "
        f"Current effective: {t.format_value()}"
        f"</small>",
        unsafe_allow_html=True,
    )

    if t.units in ("pct", "ratio"):
        # Show as percent for sliders / inputs
        default_pct = t.effective_value * 100
        new_pct = st.number_input(
            "New value (%)",
            min_value=0.0,
            max_value=50.0,
            value=float(default_pct),
            step=0.05,
            key=f"override_val_{t.name}",
        )
        new_val = new_pct / 100.0
    elif t.units == "usd":
        new_val = st.number_input(
            "New value ($)",
            min_value=0.0,
            max_value=10_000_000.0,
            value=float(t.effective_value),
            step=1_000.0,
            key=f"override_val_{t.name}",
        )
    else:  # x / ratio (unlabeled)
        new_val = st.number_input(
            "New value",
            min_value=0.0,
            max_value=20.0,
            value=float(t.effective_value),
            step=0.05,
            key=f"override_val_{t.name}",
        )

    reason = st.text_input(
        "Reason (required)",
        key=f"override_reason_{t.name}",
        placeholder=(
            "e.g. 'Pilot deal, accept compressed cap due to off-market discount'"
        ),
    )

    if st.button(
        f"Apply override → {calibration._format_for_units(new_val, t.units)}",
        key=f"apply_override_{t.name}",
        type="primary",
        disabled=not reason.strip(),
    ):
        calibration.set_override(t.name, new_val, reason.strip())
        st.success("Override applied.")
        st.rerun()
