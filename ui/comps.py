"""Comps tab — Subject vs Market benchmarks, Bucket 1 / Bucket 2 tables, clickable lat-lng map.

Top of tab: subject-property metrics compared directly to:
  - HUD Fair Market Rent (by bedroom count, for the subject's county)
  - DoD BAH floor (E-5 / E-6 / E-7 with-deps, for the subject's ZIP)
  - Local supply pipeline (TTM 5+ unit permits in the subject's city)
  - Local unemployment + macro context (10Y / 30Y mortgage)
  - Active multifamily lenders in the subject's county

Below: traditional property-record comps (Bucket 1 / Bucket 2) + clickable map.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pydeck as pdk
import streamlit as st

import config
from core.comps import Comp, get_comps
from core.provenance import all_keys, color_for, description_for, label_for
from core.market_data import (
    get_acs_for_city,
    get_bah_floor_for_zip,
    get_etl_metadata,
    get_fmr_for_county,
    get_hr_aggregate_permits_trend,
    get_local_unemployment,
    get_macro_indicators,
    get_nearby_lihtc,
    get_permits_trend_for_city,
    get_supply_pipeline,
    get_top_multifamily_lenders,
    is_etl_available,
)
from data.db import list_properties
from data.property_io import PropertyFolder, load_sources
from ui.components import section_card, v2_strip_icon
from ui.etl_notice import render_etl_missing_notice


# ---------------------------------------------------------------------------
# Subject metrics — preferring rent-roll over the property record when available
# ---------------------------------------------------------------------------

def _resolve_subject_metrics(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> dict[str, Any]:
    """Resolve the 4 quoted-on-card subject metrics with rent-roll priority.

    Priority chain (per metric):
      1. Curated shortcuts in `sources.json` (rt/sf/oc/rf) — these are the
         authoritative values Brian validated when the rent roll was loaded.
      2. Computed from `sources.json -> rentRoll.summary` if shortcuts missing.
      3. Fall back to the property record's `prop` dict.
      4. None (rendered as '—').

    Returns dict:
      {
        avg_rent / avg_sqft / rent_psf / occupancy: float | None,
        units / year_built / asset_class: forwarded from prop,
        source_label: 'Rent Roll' | '8R' | 'User input' | 'Mixed',
        source_date: ISO date string or None,
        source_file: str or None,
      }

    The card uses `source_label`/`source_date` for a provenance badge so
    it's obvious whether the numbers came from the latest rent roll vs
    the (often-stale) backbone record.
    """
    out: dict[str, Any] = {
        "avg_rent": prop.get("avg_rent"),
        "avg_sqft": prop.get("avg_sqft"),
        "rent_psf": prop.get("rent_per_sqft"),
        "occupancy": prop.get("occupancy_pct"),
        "units": prop.get("units"),
        "year_built": prop.get("year_built"),
        "asset_class": prop.get("asset_class"),
        "source_label": None,
        "source_date": None,
        "source_file": None,
    }
    # Source of the base record. 8R-backbone rows also have no legacy_id, so
    # check the id prefix BEFORE the no-legacy_id-means-custom heuristic —
    # otherwise every self-sourced property mislabels as "User input".
    if str(prop.get("property_id") or "").startswith("8R-"):
        out["source_label"] = "8R Backbone"
    else:
        is_user_input = (prop.get("status") == "Custom") or (prop.get("legacy_id") in (None, ""))
        out["source_label"] = "User input" if is_user_input else "8R"

    if folder is None:
        return out

    sources = load_sources(folder.path)
    if not sources or not isinstance(sources, dict):
        return out

    # ---- Pass 1: curated shortcuts (most authoritative) ----
    def _shortcut(key: str) -> tuple[Any, str | None, str | None]:
        v = sources.get(key)
        if isinstance(v, dict) and "value" in v:
            return v.get("value"), v.get("source"), v.get("date")
        return None, None, None

    rt_v, rt_src, rt_date = _shortcut("rt")
    sf_v, sf_src, sf_date = _shortcut("sf")
    oc_v, oc_src, oc_date = _shortcut("oc")
    rf_v, rf_src, rf_date = _shortcut("rf")

    used_sources = set()
    used_dates = set()
    if rt_v is not None:
        out["avg_rent"] = float(rt_v)
        if rt_src: used_sources.add(rt_src)
        if rt_date: used_dates.add(rt_date)
    if sf_v is not None:
        out["avg_sqft"] = float(sf_v)
        if sf_src: used_sources.add(sf_src)
        if sf_date: used_dates.add(sf_date)
    if oc_v is not None:
        # Occupancy in shortcuts is stored as percentage (e.g., 93.1) — convert
        # to fraction so it matches prop["occupancy_pct"] convention (0.913).
        oc_float = float(oc_v)
        out["occupancy"] = oc_float / 100.0 if oc_float > 1.0 else oc_float
        if oc_src: used_sources.add(oc_src)
        if oc_date: used_dates.add(oc_date)
    if rf_v is not None:
        out["rent_psf"] = float(rf_v)
        if rf_src: used_sources.add(rf_src)
        if rf_date: used_dates.add(rf_date)

    # ---- Pass 2: rentRoll.summary fallback for any metric still missing ----
    rr = sources.get("rentRoll")
    if isinstance(rr, dict):
        rr_summary = rr.get("summary") or {}
        rr_date = rr.get("date")
        rr_file = rr.get("file")
        rr_source_label = rr.get("source") or "Rent Roll"

        # actual rent per occupied unit (excludes vacant; notice tenants are
        # still paying so they count as occupied)
        if out["avg_rent"] is None:
            tar = rr_summary.get("totalActualRent")
            tu = rr_summary.get("totalUnits")
            vac = rr_summary.get("vacant", 0) or 0
            occupied_for_rent = (tu or 0) - vac
            if tar and occupied_for_rent > 0:
                out["avg_rent"] = float(tar) / float(occupied_for_rent)
                used_sources.add(rr_source_label)
                if rr_date: used_dates.add(rr_date)
        if out["avg_sqft"] is None and rr_summary.get("avgSqft") is not None:
            out["avg_sqft"] = float(rr_summary["avgSqft"])
            used_sources.add(rr_source_label)
            if rr_date: used_dates.add(rr_date)
        if out["occupancy"] is None and rr_summary.get("occupancyPct") is not None:
            out["occupancy"] = float(rr_summary["occupancyPct"]) / 100.0
            used_sources.add(rr_source_label)
            if rr_date: used_dates.add(rr_date)
        if out["rent_psf"] is None and out["avg_rent"] and out["avg_sqft"]:
            out["rent_psf"] = out["avg_rent"] / out["avg_sqft"]
            used_sources.add(rr_source_label)
            if rr_date: used_dates.add(rr_date)
        # If we ever pulled anything from rent roll, remember the file too
        if rr_source_label in used_sources and rr_file:
            out["source_file"] = rr_file

    # ---- Build the source badge text ----
    if used_sources:
        if len(used_sources) == 1:
            out["source_label"] = next(iter(used_sources))
        else:
            out["source_label"] = " · ".join(sorted(used_sources))
    if used_dates:
        # Most-recent date wins
        out["source_date"] = sorted(used_dates)[-1]
    return out


# ---------------------------------------------------------------------------
# Subject vs Market section
# ---------------------------------------------------------------------------

def _render_data_source_key() -> None:
    """Color-coded source provenance key. Pinned to the top of the Comps
    tab so it's always one click away — addresses Brian's ask:

      'identify (I like the use of color coding - with a key to tell me
      what colors mean what) the sources of the information - and realize
      that the sources of information may come from different locations
      in different scenarios'
    """
    c = config.COLORS
    with st.expander(v2_strip_icon("🎨 Data Source Color Key"), expanded=False):
        st.caption(
            "Every value in the workbench has a colored left-border "
            "indicating where it came from. Source can vary per metric — "
            "e.g., your subject's avg rent might come from a Rent Roll "
            "(green) while a comp's avg rent comes from the property record (yellow)."
        )
        for key in all_keys():
            color = color_for(key)
            label = label_for(key)
            desc = description_for(key)
            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:4px solid {color};border-radius:4px;'
                f'padding:8px 12px;margin-bottom:6px">'
                f'<div style="display:flex;align-items:baseline;gap:10px">'
                f'<span style="display:inline-block;width:14px;height:14px;'
                f'background:{color};border-radius:3px"></span>'
                f'<span style="color:{c["tx"]};font-size:13px;font-weight:600">'
                f'{label}</span></div>'
                f'<div style="color:{c["tx2"]};font-size:11px;margin-top:4px;'
                f'line-height:1.45;margin-left:24px">{desc}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _delta_badge(subject: float | None, benchmark: float | None) -> str:
    """Return an inline-HTML badge showing subject vs benchmark gap.

    Convention: positive % = subject is BELOW benchmark (room to grow).
    For Eight Rock, big gaps below market floors are bullish (organic upside).
    """
    c = config.COLORS
    if not subject or not benchmark:
        return f'<span style="color:{c["tx3"]};font-size:11px">—</span>'
    gap = (benchmark - subject) / subject  # positive = subject below benchmark
    pct = gap * 100
    if pct > 15:
        color, label = c["gn"], f"+{pct:.0f}% upside"
    elif pct > 0:
        color, label = c["yw"], f"+{pct:.0f}% upside"
    elif pct > -10:
        color, label = c["tx2"], f"{pct:.0f}% (at market)"
    else:
        color, label = c["rd"], f"{pct:.0f}% (above market)"
    return (
        f'<span style="color:{color};font-size:11px;font-weight:600;'
        f'background:rgba(255,255,255,0.04);padding:1px 6px;border-radius:8px">'
        f'{label}</span>'
    )


def _money(v: float | None) -> str:
    return f"${v:,.0f}" if v else "—"


def _money_per_mo(v: float | None) -> str:
    return f"${v:,.0f}/mo" if v else "—"


def _subject_card(prop: dict[str, Any], folder: PropertyFolder | None) -> dict[str, Any]:
    """Left card: subject property metrics — the values everything is
    benchmarked against. Pulls from rent roll first, the record second, user
    input third. Returns the resolved metrics dict so the BAH/FMR
    comparison cards can use the same `avg_rent` value.
    """
    c = config.COLORS
    m = _resolve_subject_metrics(prop, folder)

    units = m.get("units")
    avg_rent = m.get("avg_rent")
    avg_sqft = m.get("avg_sqft")
    rent_psf = m.get("rent_psf")
    occ = m.get("occupancy")
    yr = m.get("year_built")
    cls = m.get("asset_class")

    rows = [
        ("Avg rent", _money_per_mo(avg_rent)),
        ("Avg sqft", f"{int(avg_sqft):,}" if avg_sqft else "—"),
        ("$/sqft", f"${rent_psf:.2f}" if rent_psf else "—"),
        ("Occupancy", f"{occ*100:.0f}%" if occ is not None else "—"),
        ("Units", str(int(units)) if units else "—"),
        ("Year built", str(int(yr)) if yr else "—"),
        ("Class", cls or "—"),
    ]
    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:5px 0;'
        f'border-bottom:1px solid {c["bdr"]}">'
        f'<span style="color:{c["tx3"]};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.5px">{label}</span>'
        f'<span style="color:{c["tx"]};font-size:13px;font-weight:600">{val}</span>'
        f'</div>'
        for label, val in rows
    )

    # Source provenance badge — show where each metric came from. Color by
    # source: rent roll = green (live), 8R = grey (record), User input = gold.
    source_label = m.get("source_label") or "8R"
    source_date = m.get("source_date")
    if "Rent Roll" in source_label or "T-12" in source_label:
        badge_color = c["src_rr"]
    elif "User" in source_label:
        badge_color = c["ac2"]
    elif "8R" in source_label:
        badge_color = c.get("src_8r", c["src_8r"])
    else:
        badge_color = c["src_8r"]
    date_str = f" · {source_date}" if source_date else ""
    badge_html = (
        f'<div style="margin-top:8px;padding-top:6px;border-top:1px solid {c["bdr"]}">'
        f'<span style="color:{badge_color};font-size:10px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.5px">'
        f'Source: {source_label}{date_str}</span></div>'
    )

    st.markdown(
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:4px solid {c["rd"]};border-radius:6px;'
        f'padding:10px 14px">'
        f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
        f'letter-spacing:0.7px;margin-bottom:6px">Subject</div>'
        f'{rows_html}{badge_html}</div>',
        unsafe_allow_html=True,
    )
    return m


def _bah_compare_card(zip_code: str | None, subject_rent: float | None) -> None:
    """Right card: BAH floor for subject's ZIP, with comparison to subject avg rent."""
    c = config.COLORS
    bah = get_bah_floor_for_zip(zip_code) if zip_code else pd.DataFrame()
    if bah.empty:
        st.markdown(
            f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
            f'border-radius:6px;padding:10px 14px;height:100%">'
            f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.7px;margin-bottom:6px">Military Floor (BAH with-deps)</div>'
            f'<div style="color:{c["tx3"]};font-style:italic;font-size:12px">'
            f'No BAH data for ZIP {zip_code or "—"}</div></div>',
            unsafe_allow_html=True,
        )
        return

    mha_name = bah.iloc[0]["mha_name"] or bah.iloc[0]["mha_code"]
    year = int(bah.iloc[0]["effective_year"])
    rows_html = ""
    for _, row in bah.iterrows():
        rate = int(row["monthly_rate"])
        badge = _delta_badge(subject_rent, rate)
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:5px 0;border-bottom:1px solid {c["bdr"]}">'
            f'<div><span style="color:{c["tx2"]};font-size:12px;font-weight:600">'
            f'{row["paygrade"]} </span>'
            f'<span style="color:{c["tx"]};font-size:13px">${rate:,}/mo</span></div>'
            f'<div>{badge}</div></div>'
        )
    st.markdown(
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:4px solid {c["src_etl"]};border-radius:6px;padding:10px 14px">'
        f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
        f'letter-spacing:0.7px;margin-bottom:6px">'
        f'Military Floor (BAH with-deps)</div>'
        f'{rows_html}'
        f'<div style="margin-top:8px;padding-top:6px;border-top:1px solid {c["bdr"]}">'
        f'<span style="color:{c["src_etl"]};font-size:10px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.5px">'
        f'Source: DoD BAH (ETL) · {mha_name} · FY{year} · ZIP {zip_code}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _fmr_compare_card(city: str | None, subject_rent: float | None) -> None:
    """Middle card: HUD FMR by bedroom count, with comparison to subject avg rent."""
    c = config.COLORS
    fmr = get_fmr_for_county(city) if city else None
    if not fmr:
        st.markdown(
            f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
            f'border-radius:6px;padding:10px 14px;height:100%">'
            f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.7px;margin-bottom:6px">HUD Fair Market Rent</div>'
            f'<div style="color:{c["tx3"]};font-style:italic;font-size:12px">'
            f'No FMR data for {city or "—"}</div></div>',
            unsafe_allow_html=True,
        )
        return

    rows_html = ""
    for label, key in (
        ("Studio", "efficiency"), ("1BR", "one_bedroom"),
        ("2BR", "two_bedroom"), ("3BR", "three_bedroom"),
        ("4BR", "four_bedroom"),
    ):
        v = fmr.get(key)
        if v is None:
            continue
        # Subject is compared to FMR-2BR as the most-typical Class C unit.
        # Show the badge ONLY on the 2BR row to avoid noise.
        badge = _delta_badge(subject_rent, v) if key == "two_bedroom" else ""
        badge_html = f'<div>{badge}</div>' if badge else ''
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:5px 0;border-bottom:1px solid {c["bdr"]}">'
            f'<div><span style="color:{c["tx2"]};font-size:12px;font-weight:600">{label} </span>'
            f'<span style="color:{c["tx"]};font-size:13px">${v:,}/mo</span></div>'
            f'{badge_html}</div>'
        )
    st.markdown(
        f'<div style="background:{c["bg2"]};border:1px solid {c["bdr"]};'
        f'border-left:4px solid {c["src_etl"]};border-radius:6px;padding:10px 14px">'
        f'<div style="color:{c["tx3"]};font-size:10px;text-transform:uppercase;'
        f'letter-spacing:0.7px;margin-bottom:6px">HUD Fair Market Rent</div>'
        f'{rows_html}'
        f'<div style="margin-top:8px;padding-top:6px;border-top:1px solid {c["bdr"]}">'
        f'<span style="color:{c["src_etl"]};font-size:10px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.5px">'
        f'Source: HUD FMR (ETL) · {city} · FY{fmr["year"]} · 2BR comp anchor</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _ctx_tile(
    label: str,
    value: str,
    *,
    sub: str = "",
    value_color: str | None = None,
    accent: str | None = None,
) -> str:
    """Build one Local Context tile as standalone HTML.

    Custom HTML beats `st.metric` here because Streamlit's metric font is
    clamped to viewport width — at 5 columns wide, the value font shrinks
    to 15px and becomes hard to read. Inline styles with explicit 28px
    weight-700 lock in the size regardless of column width.
    """
    c = config.COLORS
    val_color = value_color or c["tx"]
    border_left = f"border-left:3px solid {accent};" if accent else ""
    sub_html = (
        f'<div style="color:{c["tx2"]};font-size:11px;line-height:1.35;'
        f'margin-top:4px">{sub}</div>' if sub else ""
    )
    return (
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'{border_left}border-radius:6px;padding:10px 14px;'
        f'min-height:96px;display:flex;flex-direction:column;justify-content:space-between">'
        f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600">{label}</div>'
        f'<div style="font-size:28px;font-weight:700;color:{val_color};'
        f'line-height:1.05;margin-top:6px;font-variant-numeric:tabular-nums">'
        f'{value}</div>'
        f'{sub_html}</div>'
    )


def _render_acs_demographics(prop: dict[str, Any]) -> None:
    """Census ACS 5-year demographic panel for the subject's city.

    Renter %, median household income, median gross rent, vacancy rate.
    Subject's avg rent is benchmarked against the area's median gross rent
    (the most direct comp metric — answers "is my asking rent realistic
    for the typical renter in this city?").
    """
    c = config.COLORS
    city = prop.get("city")
    if not city:
        return
    acs = get_acs_for_city(city)
    if not acs:
        return

    subject_rent = prop.get("avg_rent")
    median_rent = acs.get("median_gross_rent")
    rent_delta_html = ""
    if subject_rent and median_rent and median_rent > 0:
        ratio = float(subject_rent) / float(median_rent)
        diff_pct = (ratio - 1.0) * 100
        if ratio < 0.85:
            color = c["gn"]
            label = f"{abs(diff_pct):.0f}% below median"
        elif ratio > 1.15:
            color = c["rd"]
            label = f"+{diff_pct:.0f}% above median"
        else:
            color = c["tx2"]
            label = f"{diff_pct:+.0f}% vs median"
        rent_delta_html = (
            f'<span style="color:{color};font-size:11px;font-weight:600;'
            f'background:rgba(255,255,255,0.04);padding:2px 8px;'
            f'border-radius:8px;margin-left:6px">{label}</span>'
        )

    st.markdown(
        f'<div style="margin-top:12px;color:{c["tx2"]};font-size:11px;'
        f'text-transform:uppercase;letter-spacing:0.7px;font-weight:600">'
        f'Demographics — Census ACS 5-Year ({acs.get("acs_year") or "—"})</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(5)
    # Population
    with cols[0]:
        pop = acs.get("population")
        st.markdown(_ctx_tile(
            f"{city} Population",
            f"{pop:,}" if pop else "—",
            sub=f"Census ACS · {acs.get('acs_year')}",
            accent=c["src_etl"],
        ), unsafe_allow_html=True)
    # Median household income
    with cols[1]:
        mhi = acs.get("median_household_income")
        st.markdown(_ctx_tile(
            "Median HH Income",
            f"${mhi:,}" if mhi else "—",
            sub="Census ACS",
            accent=c["src_etl"],
        ), unsafe_allow_html=True)
    # Renter percentage
    with cols[2]:
        rp = acs.get("renter_pct")
        rp_color = c["gn"] if rp and rp > 0.50 else (c["yw"] if rp and rp > 0.35 else c["tx"])
        sub_label = "RENTER MAJORITY" if rp and rp > 0.50 else (
            "MIXED" if rp and rp > 0.35 else "OWNER MAJORITY"
        )
        st.markdown(_ctx_tile(
            f"{city} Renter %",
            f"{rp*100:.1f}%" if rp else "—",
            sub=f'<b style="color:{rp_color}">{sub_label}</b><br>' +
                f"{acs.get('renter_occupied_hh') or 0:,} of {(acs.get('renter_occupied_hh') or 0) + (acs.get('owner_occupied_hh') or 0):,} HH",
            value_color=rp_color,
            accent=rp_color,
        ), unsafe_allow_html=True)
    # Median gross rent + delta vs subject
    with cols[3]:
        mgr = acs.get("median_gross_rent")
        st.markdown(_ctx_tile(
            "Median Gross Rent",
            f"${mgr:,}/mo" if mgr else "—",
            sub=f"vs subject:{rent_delta_html}" if rent_delta_html else "Census ACS",
            accent=c["src_etl"],
        ), unsafe_allow_html=True)
    # Housing vacancy rate
    with cols[4]:
        vp = acs.get("vacancy_pct")
        st.markdown(_ctx_tile(
            "Housing Vacancy",
            f"{vp*100:.1f}%" if vp else "—",
            sub=f"{acs.get('vacant_housing_units') or 0:,} of "
                f"{acs.get('total_housing_units') or 0:,} units · ACS",
            accent=c["src_etl"],
        ), unsafe_allow_html=True)


def _local_context_strip(prop: dict[str, Any]) -> None:
    """Pipeline + unemployment + macro tile row.

    Uses custom HTML tiles (`_ctx_tile`) instead of `st.metric` so values
    stay readable when 5 columns are squeezed side-by-side.
    """
    c = config.COLORS
    city = prop.get("city")
    pipeline = get_supply_pipeline(city, months_back=12) if city else None
    unemployment = get_local_unemployment(city) if city else None
    macro = get_macro_indicators()

    series = {row["series_id"]: row for _, row in macro.iterrows()} if not macro.empty else {}

    tiles: list[str] = []

    # Tile 1: Supply pipeline (color value by unit count — green tight, yellow moderate, red active)
    if pipeline:
        units = pipeline["ttm_units"]
        if units < 50:
            color = c["gn"]
            cue = "TIGHT SUPPLY"
        elif units < 200:
            color = c["yw"]
            cue = "MODERATE PIPELINE"
        else:
            color = c["rd"]
            cue = "ACTIVE PIPELINE"
        latest = pipeline["latest_permit_month"] or "—"
        tiles.append(_ctx_tile(
            "TTM 5+ Unit Permits",
            f"{units:,}",
            sub=f"<b style='color:{color}'>{cue}</b><br>{pipeline['city']} · last permit {latest}",
            value_color=color,
            accent=color,
        ))
    else:
        tiles.append(_ctx_tile("TTM 5+ Unit Permits", "—", sub="No BPS data"))

    # Tile 2: Unemployment
    if unemployment:
        tiles.append(_ctx_tile(
            f"{unemployment['city']} Unemployment",
            f"{unemployment['rate']:.1f}%",
            sub=f"BLS LAUS · {unemployment['year']}-{unemployment['month']:02d}",
        ))
    else:
        tiles.append(_ctx_tile("Unemployment", "—", sub="No BLS data"))

    # Tile 3: 10Y Treasury
    ten = series.get("DGS10")
    if ten is not None:
        tiles.append(_ctx_tile(
            "10-Year Treasury",
            f"{float(ten['value']):.2f}%",
            sub=f"FRED · {str(ten['date'])[:10]}",
        ))
    else:
        tiles.append(_ctx_tile("10-Year Treasury", "—", sub="No FRED data"))

    # Tile 4: 30Y Mortgage
    mort = series.get("MORTGAGE30US")
    if mort is not None:
        tiles.append(_ctx_tile(
            "30-Year Mortgage",
            f"{float(mort['value']):.2f}%",
            sub=f"FRED · {str(mort['date'])[:10]}",
        ))
    else:
        tiles.append(_ctx_tile("30-Year Mortgage", "—", sub="No FRED data"))

    # Tile 5: HR HPI
    hpi = series.get("ATNHPIUS47260Q")
    if hpi is not None:
        tiles.append(_ctx_tile(
            "Hampton Roads MSA HPI",
            f"{float(hpi['value']):,.1f}",
            sub=f"FRED · {str(hpi['date'])[:10]}",
        ))
    else:
        tiles.append(_ctx_tile("Hampton Roads MSA HPI", "—", sub="No FRED data"))

    # Render each tile in its own column at full width (no st.metric clamp)
    cols = st.columns(len(tiles))
    for col, html in zip(cols, tiles):
        with col:
            st.markdown(html, unsafe_allow_html=True)


def _lender_table(city: str | None) -> None:
    if not city:
        return
    lenders = get_top_multifamily_lenders(city, top_n=5)
    if lenders.empty:
        return
    c = config.COLORS
    st.markdown(
        f'<div style="color:{c["tx2"]};font-size:13px;font-weight:600;margin-top:6px">'
        f'Active multifamily lenders — last 3 years HMDA, {city} county</div>',
        unsafe_allow_html=True,
    )
    display = lenders.copy()
    display["lender_name"] = display["lender_name"].fillna(
        display["lei"].apply(lambda x: f"[unresolved LEI {x[:8]}…]" if x else "[no LEI]")
    )
    display["originations"] = display["originations"].astype(int)
    display["total_volume"] = display["total_volume"].apply(
        lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
    )
    display["avg_median_loan"] = display["avg_median_loan"].apply(
        lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
    )
    display = display[["lender_name", "originations", "total_volume", "avg_median_loan"]]
    display.columns = ["Lender", "# Originations", "Total Volume (3yr)", "Avg Median Loan"]
    st.dataframe(display, use_container_width=True, hide_index=True)


def _render_permits_trend(prop: dict[str, Any]) -> None:
    """Two side-by-side line charts of new multifamily (5+ unit) permits:

      Left:  Subject's city — direct supply-pipeline indicator
      Right: All Hampton Roads cities aggregated — regional context

    Both pull from the Census Building Permits Survey (BPS) Place-level
    monthly file (so{YY}{MM}c.txt). 36 months of history.
    """
    city = prop.get("city")
    if not city:
        return
    c = config.COLORS
    city_df = get_permits_trend_for_city(city, months_back=36)
    hr_df = get_hr_aggregate_permits_trend(months_back=36)
    if city_df.empty and hr_df.empty:
        return

    st.markdown(
        f'<div style="color:{c["tx2"]};font-size:13px;font-weight:600;margin-top:14px">'
        f'New Multifamily Permits — 36-month trend  '
        f'<span style="color:{c["tx3"]};font-size:11px;font-weight:400">'
        f'(5+ unit buildings, Census BPS)</span></div>',
        unsafe_allow_html=True,
    )
    col_left, col_right = st.columns(2)

    with col_left:
        if not city_df.empty:
            ttm = int(city_df["units_5p"].tail(12).sum())
            st.markdown(
                f'<div style="color:{c["tx3"]};font-size:11px">'
                f'{city} only · <b>{ttm:,}</b> units permitted last 12 mo'
                f'</div>',
                unsafe_allow_html=True,
            )
            chart_df = city_df.set_index("year_month")[["units_5p"]].rename(
                columns={"units_5p": f"{city} units"},
            )
            st.line_chart(chart_df, height=180, color=[c["ac"]])
        else:
            st.caption(f"No city-level permit data for {city}.")

    with col_right:
        if not hr_df.empty:
            ttm_hr = int(hr_df["units_5p"].tail(12).sum())
            st.markdown(
                f'<div style="color:{c["tx3"]};font-size:11px">'
                f'<b>Hampton Roads-Wide</b> · all 7 cities aggregated · '
                f'<b>{ttm_hr:,}</b> units permitted last 12 mo'
                f'</div>',
                unsafe_allow_html=True,
            )
            chart_df = hr_df.set_index("year_month")[["units_5p"]].rename(
                columns={"units_5p": "Hampton Roads-Wide units"},
            )
            st.line_chart(chart_df, height=180, color=[c["bl"]])
        else:
            st.caption("No Hampton Roads-Wide permit data.")


# ETL source-short-name lookup: table_name → --only argument.
# Mirrors SOURCES in hampton-roads-etl/hampton_roads_etl.py. Some sources
# write multiple tables (HMDA writes 2, ASR writes 2, BAH writes 2) — they
# share the same short name.
_TABLE_TO_ETL_SHORT = {
    "census_acs":               "acs",
    "bls_laus":                 "bls",
    "fred_series":              "fred",
    "hud_fmr":                  "fmr",
    "census_bps":               "bps",
    "hmda_originations":        "hmda",
    "hmda_lender_summary":      "hmda",
    "hud_lihtc":                "lihtc",
    "bah_rates":                "bah",
    "bah_zip_mha":              "bah",
    "va_multifamily_inventory": "asr",
    "va_assessment_history":    "asr",
    "rent_listings":            "listings",
}


def _run_etl_refresh(only: str | None = None) -> None:
    """Run the Hampton Roads ETL via subprocess. Shows a live status in the UI.

    Per Brian 2026-05-29 (v2.0.9): replaces the prior "Re-run
    `python hampton_roads_etl.py` to refresh" caption. Buttons call this.

    Args:
        only: ETL short name (acs, bls, fred, fmr, bps, hmda, lihtc, bah,
              asr, listings). When None, runs the full ETL.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path as _P

    wb_root = _P(__file__).resolve().parent.parent.parent
    etl_dir = wb_root / "hampton-roads-etl"
    etl_script = etl_dir / "hampton_roads_etl.py"

    if not etl_script.exists():
        # The standalone ETL script belongs to the old repo layout and isn't
        # part of the v5 deployment. Public-data refresh will be wired into the
        # 8R data spine (Phase 0 / Module F); until then this is a no-op, shown
        # as a notice rather than a red error.
        st.info("Automated data refresh isn't wired into this deployment yet. "
                "Public sources (FRED / BLS / HUD / FMR / permits) will be "
                "refreshed by the 8R data backbone (Phase 0). This control "
                "activates once that pipeline lands.")
        return

    cmd = [_sys.executable, str(etl_script)]
    if only:
        cmd.extend(["--only", only])

    label = f"source '{only}'" if only else "all sources"

    with st.status(f"⚙ Running ETL for {label}…", expanded=True) as status:
        st.caption(f"$ {' '.join(cmd[1:])}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(etl_dir),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                status.update(
                    label=f"✅ ETL refresh complete ({label})",
                    state="complete",
                )
                # Show last few lines of stdout so Brian can see what happened
                tail = "\n".join(result.stdout.splitlines()[-10:])
                if tail:
                    st.code(tail, language=None)
                st.success("Reload the page to see fresh data in the comp tables.")
            else:
                status.update(
                    label=f"❌ ETL refresh failed (exit {result.returncode})",
                    state="error",
                )
                st.error("Stderr tail:")
                st.code(result.stderr[-800:] or "(empty)", language=None)
        except subprocess.TimeoutExpired:
            status.update(label="⏱ ETL timed out (>10 min)", state="error")
            st.error("ETL run exceeded the 10-minute limit and was terminated.")
        except (OSError, RuntimeError) as e:
            status.update(label="❌ Could not start ETL", state="error")
            st.error(f"{type(e).__name__}: {e}")


def _render_data_sources_v2() -> None:
    """Data Sources & Last Refresh panel — V2 (Brian 5/29 v2.0.9).

    Adds a 'Refresh All Sources' primary button at the top and individual
    Refresh buttons next to each source row. Replaces the prior
    'Re-run `python hampton_roads_etl.py` to refresh' caption text.
    """
    df = get_etl_metadata()
    c = config.COLORS

    # --- Refresh All button at the top ---
    col_btn, col_caption = st.columns([1, 4])
    with col_btn:
        if st.button(
            "🔄 Refresh All",
            key="etl_refresh_all",
            type="primary",
            use_container_width=True,
        ):
            _run_etl_refresh()
    with col_caption:
        st.caption(
            "Pulls all 10 ETL sources from FRED, BLS, property records, HUD, HMDA, "
            "and the listings scraper. Takes 30–90 seconds typically."
        )

    if df.empty:
        st.caption(
            "No ETL metadata yet — click **Refresh All** above to populate."
        )
        return

    st.markdown(
        f'<div style="font-size:12px;color:{c["tx3"]};margin:14px 0 8px 0">'
        f'Or refresh individual sources below:</div>',
        unsafe_allow_html=True,
    )

    # Brian 5/29 v2.0.17: refresh buttons sit IN THE TOP-RIGHT of each
    # source card, directly under the timestamp (was at the bottom of
    # the card). Each source is now a two-column row inside a marker'd
    # container: left = info (name/desc/rows/url), right = timestamp +
    # refresh button stacked. CSS :has() paints the gold-left-border
    # around the whole container.
    st.markdown(
        f"""
<style>
[data-testid="stVerticalBlock"]:has(> div > div > [data-testid="stMarkdownContainer"] > div > .v2-src-mark) {{
    background: {c["bg3"]};
    border: 1px solid {c["bdr"]};
    border-left: 3px solid {c["ac"]};
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 8px;
}}
.v2-src-mark {{ display: none; }}
/* Compact refresh button under the timestamp (right column). */
[data-testid="stVerticalBlock"]:has(> div > div > [data-testid="stMarkdownContainer"] > div > .v2-src-mark)
    [data-testid="stButton"] > button {{
    background: transparent;
    border: 1px solid {c["bdr"]};
    color: {c["tx2"]};
    padding: 2px 10px;
    font-size: 11px;
    min-height: 0;
    height: 26px;
    border-radius: 4px;
    margin-top: 4px;
    float: right;
}}
[data-testid="stVerticalBlock"]:has(> div > div > [data-testid="stMarkdownContainer"] > div > .v2-src-mark)
    [data-testid="stButton"] > button:hover {{
    border-color: {c["ac"]};
    color: {c["ac"]};
}}
</style>""",
        unsafe_allow_html=True,
    )

    for _, row in df.iterrows():
        display_name = row.get("display_name") or row.get("table_name", "—")
        desc = row.get("description") or ""
        url = row.get("source_url") or ""
        method = row.get("fetch_method") or ""
        row_count = int(row.get("row_count") or 0)
        iso = row.get("last_pull_at") or ""
        table_name = row.get("table_name") or ""
        stamp_pretty = _format_timestamp(iso)
        short_name = _TABLE_TO_ETL_SHORT.get(table_name)

        url_html = (
            f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="color:{c["ac2"]};text-decoration:none">{url}</a>'
            if url else
            f'<span style="color:{c["tx3"]}">—</span>'
        )

        with st.container():
            # Hidden marker — CSS :has() targets the parent container to
            # paint the gold-left-border card around all children.
            st.markdown(
                f'<div class="v2-src-mark" data-id="{table_name}"></div>',
                unsafe_allow_html=True,
            )
            # Two-column row: info on the left, timestamp + refresh stacked
            # on the right. Refresh button rendered as a Streamlit widget so
            # it actually fires on click.
            col_info, col_meta = st.columns([5, 1.4], vertical_alignment="top")
            with col_info:
                st.markdown(
                    f'<div style="color:{c["tx"]};font-size:14px;font-weight:600">'
                    f'{display_name}</div>'
                    f'<div style="color:{c["tx2"]};font-size:12px;margin-top:4px;'
                    f'line-height:1.45">{desc}</div>'
                    f'<div style="color:{c["tx3"]};font-size:11px;margin-top:6px">'
                    f'<b>{row_count:,}</b> rows · {method} · '
                    f'<code style="color:{c["tx3"]};font-size:10px">{table_name}</code>'
                    f'</div>'
                    f'<div style="font-size:11px;margin-top:4px;word-break:break-all">'
                    f'{url_html}</div>',
                    unsafe_allow_html=True,
                )
            with col_meta:
                st.markdown(
                    f'<div style="color:{c["tx2"]};font-size:11px;'
                    f'font-variant-numeric:tabular-nums;white-space:nowrap;'
                    f'text-align:right">⟳ {stamp_pretty}</div>',
                    unsafe_allow_html=True,
                )
                if short_name:
                    if st.button(
                        "🔄 Refresh",
                        key=f"etl_refresh_{table_name}",
                        use_container_width=False,
                    ):
                        _run_etl_refresh(only=short_name)


def _render_data_sources() -> None:
    """Bottom-of-Comps-tab data provenance panel.

    Reads `etl_metadata` (populated on every `write()` in the ETL pipeline)
    and lists every source with its display name, description, source URL,
    fetch method, row count, and last-pull timestamp.

    Hides itself if the metadata table is empty (fresh checkout).
    """
    df = get_etl_metadata()
    if df.empty:
        return

    c = config.COLORS

    # Collapsed by default per Brian 2026-05-08 — this is reference
    # material; analysts only crack it open when they want to verify
    # ETL freshness or trace a specific source URL. The outer
    # `section_card("Data Sources & Last Refresh", icon="📋")` provides
    # the always-visible heading; the expander is the on-demand drawer.
    with st.expander(
        f"Show provenance for {len(df)} ETL source(s)",
        expanded=False,
    ):
        st.caption(
            "Each row shows the source URL and the timestamp of the most-"
            "recent puller run that wrote to that table. Refresh is handled "
            "by the 8R data backbone (Phase 0)."
        )

        # Render each source as its own row-card so the description and URL
        # are readable. (st.dataframe truncates long URL cells.)
        for _, row in df.iterrows():
            display_name = row.get("display_name") or row.get("table_name", "—")
            desc         = row.get("description") or ""
            url          = row.get("source_url") or ""
            method       = row.get("fetch_method") or ""
            row_count    = int(row.get("row_count") or 0)
            iso          = row.get("last_pull_at") or ""
            table_name   = row.get("table_name") or ""

            # Format timestamp: "2026-05-07T13:07:01" → "2026-05-07 1:07 PM"
            stamp_pretty = _format_timestamp(iso)

            url_html = (
                f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="color:{c["ac2"]};text-decoration:none">{url}</a>'
                if url else
                f'<span style="color:{c["tx3"]}">—</span>'
            )

            st.markdown(
                f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
                f'border-left:3px solid {c["ac"]};border-radius:6px;'
                f'padding:10px 14px;margin-bottom:8px">'
                # Header row: display name + last-pull stamp
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:baseline;gap:12px">'
                f'<div style="color:{c["tx"]};font-size:14px;font-weight:600">'
                f'{display_name}</div>'
                f'<div style="color:{c["tx2"]};font-size:11px;'
                f'font-variant-numeric:tabular-nums;white-space:nowrap">'
                f'⟳ {stamp_pretty}'
                f'</div></div>'
                # Description
                f'<div style="color:{c["tx2"]};font-size:12px;margin-top:4px;'
                f'line-height:1.45">{desc}</div>'
                # Meta row: row count, fetch method, table name
                f'<div style="color:{c["tx3"]};font-size:11px;margin-top:6px">'
                f'<b>{row_count:,}</b> rows · {method} · '
                f'<code style="color:{c["tx3"]};font-size:10px">{table_name}</code>'
                f'</div>'
                # URL
                f'<div style="font-size:11px;margin-top:4px;'
                f'word-break:break-all">{url_html}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _format_timestamp(iso: str) -> str:
    """Convert '2026-05-07T13:07:01' to '2026-05-07 1:07 PM'.

    Returns the original string if parsing fails (covers date-only fallbacks).
    Windows-safe: strftime %-I isn't supported on Windows, so we strip the
    leading zero manually.
    """
    if not iso:
        return "—"
    try:
        import datetime as dt
        ts = dt.datetime.fromisoformat(iso)
        date_part = ts.strftime("%Y-%m-%d")
        time_part = ts.strftime("%I:%M %p")
        # Strip leading zero on hour: "01:07 PM" → "1:07 PM"
        if time_part.startswith("0"):
            time_part = time_part[1:]
        return f"{date_part} {time_part}"
    except (ValueError, TypeError):
        return iso


def _render_lihtc_nearby(prop: dict[str, Any]) -> None:
    """Table of LIHTC properties within 5 miles, sorted by distance.

    Highlights compliance windows: anything with `years_to_initial_end <= 5`
    is a near-term off-market opportunity (initial 15-yr compliance ending).
    """
    lat = prop.get("latitude")
    lng = prop.get("longitude")
    if lat is None or lng is None:
        return
    c = config.COLORS
    df = get_nearby_lihtc(float(lat), float(lng), max_miles=5.0, limit=20)
    if df.empty:
        return

    st.markdown(
        f'<div style="color:{c["tx2"]};font-size:13px;font-weight:600;margin-top:14px">'
        f'Nearby LIHTC properties (≤5 miles) — initial-compliance exits are off-market opportunities'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Build a flag column highlighting near-term compliance exits
    def _flag(years: int | float) -> str:
        try:
            y = int(years)
        except (TypeError, ValueError):
            return ""
        if y < 0:
            return "✅ exited initial"
        if y <= 5:
            return f"🎯 {y}y to exit"
        if y <= 10:
            return f"⏳ {y}y to exit"
        return ""

    display = pd.DataFrame({
        "Distance (mi)":  df["distance_miles"].round(2),
        "Project":        df["project_name"].fillna(""),
        "Address":        df["address"].fillna(""),
        "City":           df["city"].fillna(""),
        "Units":          df["n_units"].fillna(0).astype(int),
        "PIS":            df["year_placed_in_service"].fillna(0).astype(int).replace(0, "—"),
        "Initial end":    df["initial_compliance_end"].fillna(0).astype(int).replace(0, "—"),
        "Yrs to exit":    df["years_to_initial_end"].apply(
            lambda v: int(v) if pd.notna(v) else "—"
        ),
        "Status":         df["years_to_initial_end"].apply(_flag),
    })
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.caption(
        "🎯 ≤5 years to initial-compliance exit · ⏳ 6–10 years out · "
        "✅ already past initial 15-year period (qualified contract / rent restrictions may still apply via extended use)."
    )


def _render_subject_vs_market(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    """Top section of the Comps tab — subject metrics benchmarked against
    HUD FMR, BAH floor, supply pipeline, unemployment, macro, and lenders.

    Uses rent-roll-derived subject metrics when available so the FMR/BAH
    delta badges reflect today's in-place rent (not stale property record values).
    """
    c = config.COLORS  # palette tokens used in inline HTML below
    if not is_etl_available():
        render_etl_missing_notice("the subject-vs-market panel")
        return

    st.caption(
        "Subject metrics pulled from the latest rent roll when available, "
        "else the backbone record. Green badges = upside vs the floor; red = "
        "above market. Each card shows its data source at the bottom in "
        "the matching color."
    )

    zip_code = prop.get("zip")
    city = prop.get("city")

    col_subj, col_fmr, col_bah = st.columns(3)
    with col_subj:
        # _subject_card returns the resolved metrics so the comparison cards
        # below use the same (rent-roll-priority) avg_rent value.
        resolved = _subject_card(prop, folder)
    subject_rent = resolved.get("avg_rent")
    with col_fmr:
        _fmr_compare_card(city, subject_rent)
    with col_bah:
        _bah_compare_card(zip_code, subject_rent)

    st.markdown('<span class="er-anchor" id="anchor-context"></span>', unsafe_allow_html=True)
    _local_context_strip(prop)
    _render_acs_demographics(prop)
    st.markdown('<span class="er-anchor" id="anchor-permits"></span>', unsafe_allow_html=True)
    _render_permits_trend(prop)
    st.markdown('<span class="er-anchor" id="anchor-lenders"></span>', unsafe_allow_html=True)
    _lender_table(city)
    st.markdown('<span class="er-anchor" id="anchor-lihtc"></span>', unsafe_allow_html=True)
    _render_lihtc_nearby(prop)

    # NOTE: 📋 Data Sources & Last Refresh moved to the bottom of `render_comps`
    # (Brian 2026-05-08) — it's reference material, belongs after all the
    # per-deal panels rather than mid-tab between subject metrics and comps.


# ---------------------------------------------------------------------------
# Bucket comps + map
# ---------------------------------------------------------------------------

def _comp_to_row(c: Comp) -> dict[str, Any]:
    # Mystery-shop data — if we have scraped a rent_listings row for this
    # comp's property_id, surface the effective rent + concession alongside
    # the record asking rent. Tells Brian whether the comp's "advertised" rent
    # is real or includes concession discount we should price against.
    eff_rent, concession_chip = _lookup_mystery_shop(c)
    return {
        "Bucket": c.bucket,
        "Distance (mi)": round(c.distance_miles, 2),
        "Name": c.name,
        "City": c.city or "—",
        "Units": c.units or "—",
        "Year built": c.year_built or "—",
        "Avg rent": f"${c.avg_rent:,.0f}" if c.avg_rent else "—",
        "Effective": eff_rent,
        "Concession": concession_chip,
        "Avg sqft": f"{c.avg_sqft:,.0f}" if c.avg_sqft else "—",
        # $/sqft uses 2 decimal places + dollar sign per Brian's financial-format rule.
        "$/sqft": f"${c.rent_per_sqft:.2f}" if c.rent_per_sqft else "—",
        "Class": c.asset_class or "—",
        "Manager": c.manager or "—",
        "Owner": c.owner or "—",
    }


def _lookup_mystery_shop(c: Comp) -> tuple[str, str]:
    """Look up the most-recent rent_listings row for this comp's property_id.

    Returns (effective_rent_str, concession_str). "—" / "" if no data.
    Cached per-process via ``functools.lru_cache`` on a small helper.
    """
    if not c.property_id:
        return "—", ""
    rows = _get_listings_for_property(str(c.property_id))
    if not rows:
        return "—", ""
    # Find the row with effective rent available
    for r in rows:
        if r.get("effective_one_br_rent"):
            eff = f"${r['effective_one_br_rent']:,.0f}"
            concession = ""
            if r.get("concession_months_free", 0) > 0:
                concession = f"{r['concession_months_free']:.1f} mo free"
            elif r.get("concession_dollar_off", 0) > 0:
                concession = f"${r['concession_dollar_off']:,.0f} off"
            return eff, concession
    return "—", ""


@st.cache_data(ttl=300, show_spinner=False)
def _get_listings_for_property(property_id: str) -> list[dict]:
    """Cached lookup of rent_listings rows for a property."""
    import sqlite3
    from pathlib import Path
    db = Path(__file__).resolve().parent.parent.parent / "hampton-roads-etl" / "hampton_roads.db"
    if not db.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT effective_one_br_rent, concession_months_free, "
                "concession_dollar_off, concession_text, scrape_status "
                "FROM rent_listings WHERE property_id = ? "
                "AND scrape_status = 'success' "
                "ORDER BY scraped_at DESC",
                (property_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def render_comps(prop: dict[str, Any], folder: PropertyFolder | None = None) -> None:
    """Performance & Market tab — reordered 2026-05-29 v2.0.9 per Brian.

    Order locked by Brian (5/29 EOD):
      1. Rent Roll
      2. Comparables (combined Bucket 1 + Bucket 2; Bucket 1 highlighted in
         8-Rock gold as the preferred set — ≤3 mi, same class)
      3. Rent Listing URLs (with latest scrape results as small inline squares)
      4. Data Sources & Last Refresh (with Refresh All + per-source buttons
         wired to `python hampton_roads_etl.py --only <source>` via subprocess)

    REMOVED entirely per Brian's request (clutter, won't use):
      - Map (subject + comps clickable)
      - Nearby LIHTC properties (≤5 miles)

    DEFERRED — kept inside a collapsed expander below the fold to preserve
    functionality without cluttering the primary view:
      - Subject vs Market (FMR + BAH benchmarks)

    `folder` (the property's on-disk folder) is needed for rent roll loading
    and rent-roll-priority subject metrics.
    """
    lat = prop.get("latitude")
    lng = prop.get("longitude")
    cls = prop.get("asset_class")

    c = config.COLORS

    # Rent Roll MOVED to the Underwriting tab per Brian 5/29 v2.0.18 — it
    # already renders there alongside the Year-1 metrics, so keeping a copy
    # here was redundant.

    # ============================================================
    # 1. Comparables (combined Bucket 1 + Bucket 2; B1 in 8R gold)
    # ============================================================
    with section_card("Comparables", icon="🏘️"):
        if lat is None or lng is None:
            st.caption(
                "Subject property has no geocoded coordinates — comparables "
                "unavailable."
            )
        else:
            candidates = list_properties(require_latlng=True, limit=10_000)
            comps = get_comps(
                subject_id=prop["property_id"],
                subject_lat=float(lat),
                subject_lon=float(lng),
                subject_class=cls,
                candidates=candidates,
            )
            bucket1 = [c for c in comps if c.bucket == 1]
            bucket2 = [c for c in comps if c.bucket == 2]

            if not bucket1 and not bucket2:
                st.caption("No comparable properties found within range.")
            else:
                st.markdown(
                    f'<div style="display:flex;gap:18px;font-size:12px;'
                    f'color:{c["tx2"]};margin-bottom:8px;flex-wrap:wrap">'
                    f'<span><span style="display:inline-block;width:10px;'
                    f'height:10px;background:rgba(184,151,56,0.55);border:1px '
                    f'solid #B89738;border-radius:2px;vertical-align:middle;'
                    f'margin-right:6px"></span>'
                    f'<b style="color:#8C7028">Bucket 1 — preferred</b> '
                    f'(≤3 mi, same class {cls or "?"}) · {len(bucket1)} comps</span>'
                    f'<span><span style="display:inline-block;width:10px;'
                    f'height:10px;background:transparent;border:1px solid '
                    f'{c["bdr"]};border-radius:2px;vertical-align:middle;'
                    f'margin-right:6px"></span>'
                    f'<b>Bucket 2</b> (≤5 mi, any class) · {len(bucket2)} comps</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                rows = []
                for cb1 in bucket1:
                    r = _comp_to_row(cb1)
                    r["_bucket"] = "B1"
                    rows.append(r)
                for cb2 in bucket2:
                    r = _comp_to_row(cb2)
                    r["_bucket"] = "B2"
                    rows.append(r)
                df_all = pd.DataFrame(rows)

                def _highlight_b1(row):
                    is_b1 = row.get("_bucket") == "B1"
                    css = (
                        "background-color: rgba(184, 151, 56, 0.18); "
                        "border-left: 3px solid #B89738;"
                        if is_b1 else ""
                    )
                    return [css] * len(row)

                styler = df_all.style.apply(_highlight_b1, axis=1)
                if "_bucket" in df_all.columns:
                    try:
                        styler = styler.hide(["_bucket"], axis="columns")
                    except (AttributeError, TypeError):
                        styler = df_all.drop(columns=["_bucket"]).style.apply(
                            _highlight_b1, axis=1,
                        )
                st.dataframe(styler, use_container_width=True, hide_index=True)

    # ============================================================
    # 2. Rent Listing URLs (panel includes Latest scrape result squares)
    # ============================================================
    with section_card("Rent Listing URLs", icon="🔗"):
        from ui.listings_panel import render_listing_urls_panel
        render_listing_urls_panel(prop)

    # ============================================================
    # 3. Data Sources & Last Refresh (Refresh All + per-source buttons)
    # ============================================================
    with section_card("Data Sources & Last Refresh", icon="📋"):
        _render_data_sources_v2()

    # ============================================================
    # Subject vs Market — preserved below the fold (deferred per Brian).
    # Wrapped in a collapsed expander so it stays accessible without
    # cluttering the primary view.
    # ============================================================
    with st.expander(v2_strip_icon("⚙ More market context (Subject vs Market)"), expanded=False):
        _render_subject_vs_market(prop, folder)
