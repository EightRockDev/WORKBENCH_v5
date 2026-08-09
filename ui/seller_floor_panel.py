"""UI panel for the Seller Floor reverse-engineering tool.

Renders on the Returns & Waterfall tab. Brian enters/confirms:
  - Seller's purchase price (defaults from va_multifamily_inventory if found)
  - Purchase year
  - Asking price (current)
  - Optionally: LTV / rate / cost-seg overrides

Output: 3 floor tiles (breakeven, 5% IRR, 10% IRR), gap analysis vs ask,
LOI-ready anchor-offer language.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

import streamlit as st

import config
from core import seller_floor as sf
from ui.components import section_card


from core.etl_location import etl_db as _resolve_etl_db
_ETL_DB = _resolve_etl_db()   # in-repo -> data/ -> legacy sibling (2026-08-09)


def _parse_sale_date_to_year(value: Any) -> int | None:
    """Same robust parser as distress_radar — handles unix-ms + ISO strings."""
    if value is None or value == "":
        return None
    try:
        v = float(value)
        if v > 1e9:
            return dt.date.fromtimestamp(v / 1000.0).year
    except (TypeError, ValueError, OSError):
        pass
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return dt.date.fromisoformat(s).year
    except ValueError:
        return None


def _lookup_assessor_sale(prop: dict[str, Any]) -> tuple[float, int] | None:
    """Best-effort lookup of (last_sale_price, year) from va_multifamily_inventory."""
    if not _ETL_DB.is_file():
        return None
    city = prop.get("city")
    address = (prop.get("address") or "").upper()
    if not city or not address:
        return None
    try:
        with sqlite3.connect(f"file:{_ETL_DB}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT address, last_sale_price, last_sale_date "
                "FROM va_multifamily_inventory "
                "WHERE city = ? AND last_sale_price > 100000",
                (city,),
            ).fetchall()
    except sqlite3.Error:
        return None

    needle = address[:12]
    for addr, price, date in rows:
        if addr and needle in addr.upper():
            year = _parse_sale_date_to_year(date)
            if year:
                return float(price), year
    return None


def _latest_sale_from_subject_folder(folder) -> tuple[float, int] | None:
    """Brian 5/29 v2.0.37 — read the latest sale from the property folder's
    Sale History (sales.json — same data shown on the Subject tab) so the
    Seller Floor panel auto-populates with what you already see there.

    Falls back to None if the folder isn't there, sales.json is missing,
    or no row has both a price and a date."""
    if folder is None or not hasattr(folder, "path"):
        return None
    try:
        from data.property_io import load_sales
        data = load_sales(folder.path)
    except Exception:
        return None
    if not data:
        return None

    # Normalize: legacy list, or dict with last_3_apartment_sales / sales.
    rows: list[dict] = []
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        for key in ("last_3_apartment_sales", "sales", "history"):
            v = data.get(key)
            if isinstance(v, list):
                rows.extend(r for r in v if isinstance(r, dict))

    best_year = 0
    best_price = 0.0
    for r in rows:
        price = r.get("price") or r.get("sale_price") or r.get("amount")
        date_val = (
            r.get("date") or r.get("sale_date") or r.get("recording_date")
        )
        try:
            p = float(price) if price not in (None, "") else 0.0
        except (TypeError, ValueError):
            p = 0.0
        if p < 100_000:  # skip LLC↔LLC $1 transfers + tiny noise
            continue
        yr = _parse_sale_date_to_year(date_val)
        if not yr:
            continue
        if yr > best_year:
            best_year = yr
            best_price = p
    if best_year and best_price:
        return best_price, best_year
    return None


def render_seller_floor_panel(prop: dict[str, Any], folder=None) -> None:
    if not prop:
        return
    c = config.COLORS

    with section_card(
        "Seller Floor Reverse-Engineering",
        icon="🎯",
        accent="ac",
        subtitle=(
            "Estimates seller's economic floor (loan + prepay + tax) so the "
            "LOI lands just above their breakeven — not at their ask."
        ),
    ):
        # Brian 5/29 v2.0.37 — prefer the property's own Sale History
        # (same data on the Subject tab) over the assessor ETL lookup.
        # The Sale History is closer to what the analyst is reading on
        # screen; the assessor row is a fallback when no folder data.
        folder_hit = _latest_sale_from_subject_folder(folder)
        if folder_hit:
            default_price, default_year = folder_hit
            data_source = "subject Sale History"
        else:
            assessor_hit = _lookup_assessor_sale(prop)
            default_price = assessor_hit[0] if assessor_hit else 0.0
            default_year = assessor_hit[1] if assessor_hit else 2020
            data_source = "assessor record" if assessor_hit else None

        pid = prop.get("property_id", "noid")
        if data_source and default_price > 0:
            st.caption(
                f"Auto-filled from the {data_source}: "
                f"${default_price:,.0f} in {int(default_year)}. "
                "Override below if you have better info."
            )

        col1, col2, col3 = st.columns(3)
        with col1:
            purchase_price = st.number_input(
                "Seller's purchase price ($)",
                min_value=0.0, value=float(default_price), step=100_000.0,
                help="Last sale price from assessor data. Override if you have better info.",
                key=f"sf_pp_{pid}",
            )
        with col2:
            purchase_year = st.number_input(
                "Purchase year",
                min_value=1970, max_value=dt.date.today().year,
                value=int(default_year), step=1,
                key=f"sf_py_{pid}",
            )
        with col3:
            asking_price = st.number_input(
                "Current asking price ($)",
                min_value=0.0, value=0.0, step=100_000.0,
                help="Drives gap analysis + LOI anchor.",
                key=f"sf_ask_{pid}",
            )

        with st.expander("Override assumptions (advanced)", expanded=False):
            col4, col5, col6 = st.columns(3)
            with col4:
                ltv_pct = st.slider("Origination LTV %", 50, 80, 70, 1, key=f"sf_ltv_{pid}")
            with col5:
                rate_pct = st.slider(
                    "Origination rate %", 2.5, 8.5, 0.0, 0.25,
                    help="0 = era default", key=f"sf_rate_{pid}",
                )
            with col6:
                cost_seg = st.checkbox(
                    "Assume cost segregation", value=True,
                    help="Industry standard for HR Class C 1980s-90s vintage.",
                    key=f"sf_seg_{pid}",
                )

        if purchase_price <= 0:
            st.info(
                "Enter the seller's purchase price to compute the floor. "
                + ("" if assessor_hit else
                   "(Couldn't find a sale record in assessor data for this address — look it up manually.)")
            )
            return

        analysis = sf.analyze_seller_floor(
            purchase_price=purchase_price,
            purchase_year=int(purchase_year),
            asking_price=asking_price if asking_price > 0 else None,
            use_cost_segregation=cost_seg,
            ltv_override=ltv_pct / 100.0,
            rate_override=rate_pct / 100.0 if rate_pct > 0 else None,
        )

        col_be, col_5, col_10 = st.columns(3)
        with col_be:
            st.markdown(_tile(
                "Breakeven Floor",
                f"${analysis.economic_floor_breakeven:,.0f}",
                "Net zero to seller", color=c["rd"], c=c,
            ), unsafe_allow_html=True)
        with col_5:
            st.markdown(_tile(
                "5% IRR Floor",
                f"${analysis.economic_floor_5pct_irr:,.0f}",
                "Seller's 'I'd consider' price", color=c["yw"], c=c,
            ), unsafe_allow_html=True)
        with col_10:
            st.markdown(_tile(
                "10% IRR Floor",
                f"${analysis.economic_floor_10pct_irr:,.0f}",
                "Seller's 'I'd be happy' price", color=c["gn"], c=c,
            ), unsafe_allow_html=True)

        st.markdown(
            f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
            f'letter-spacing:0.7px;font-weight:600;margin-top:14px;margin-bottom:6px">'
            f'Component breakdown (at breakeven price)</div>',
            unsafe_allow_html=True,
        )
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric(
            "Loan balance",
            f"${analysis.est_current_loan_balance:,.0f}",
            f"@ {analysis.est_origination_rate*100:.1f}% orig rate",
        )
        col_b.metric(
            "Prepay penalty",
            f"${analysis.est_prepay_penalty:,.0f}",
            f"{analysis.prepay_structure}",
        )
        col_c.metric(
            "Total tax",
            f"${analysis.est_total_tax:,.0f}",
            f"${analysis.est_depreciation_recapture_tax:,.0f} recapture",
        )
        col_d.metric(
            "Closing costs",
            f"${analysis.est_closing_costs:,.0f}", "2% of sale",
        )

        if asking_price > 0:
            gap = analysis.ask_minus_floor or 0
            gap_pct = (analysis.ask_premium_pct or 0)
            gap_color = c["gn"] if gap_pct > 0.15 else (c["yw"] if gap_pct > 0.05 else c["rd"])
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:3px solid {gap_color};border-radius:6px;'
                f'padding:14px;margin-top:14px">'
                f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
                f'font-weight:600;letter-spacing:0.5px">Ask vs Floor Gap</div>'
                f'<div style="font-size:20px;color:{gap_color};font-weight:700;'
                f'margin-top:4px">${gap:,.0f} '
                f'<span style="font-size:14px;color:{c["tx2"]}">'
                f'({gap_pct*100:+.0f}% over breakeven · {analysis.negotiation_room} room)'
                f'</span></div></div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="background:{c["bg3"]};border-left:3px solid {c["ac"]};'
                f'border-radius:4px;padding:12px;margin-top:8px">'
                f'<div style="font-size:11px;color:{c["tx3"]};text-transform:uppercase;'
                f'font-weight:600;letter-spacing:0.5px;margin-bottom:4px">'
                f'LOI Anchor Recommendation</div>'
                f'<div style="font-size:22px;color:{c["ac3"]};font-weight:700">'
                f'Offer ${analysis.loi_anchor_offer:,.0f}</div>'
                f'<div style="font-size:13px;color:{c["tx"]};margin-top:8px;'
                f'line-height:1.5">{analysis.loi_justification}</div></div>',
                unsafe_allow_html=True,
            )

        with st.expander("Show full analysis rationale", expanded=False):
            for line in analysis.rationale:
                st.markdown(f"- {line}")
            st.caption("Assumptions are industry defaults. Override sliders if you know better.")


def _tile(label: str, value: str, sub: str, color: str, c: dict) -> str:
    return (
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:3px solid {color};border-radius:6px;padding:10px 14px">'
        f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
        f'letter-spacing:0.6px;font-weight:600">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color};'
        f'font-variant-numeric:tabular-nums;line-height:1.1;margin-top:3px">{value}</div>'
        f'<div style="color:{c["tx3"]};font-size:11px;margin-top:2px">{sub}</div>'
        f'</div>'
    )
