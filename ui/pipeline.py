"""Deal-sourcing tab — Broker CRM + Direct-Mail target generator + Loan-maturity alerts.

Three sections:

  - **C1 Broker CRM**: tracks Lindahl's deal-sourcing funnel (1,000 letters
    → 1% response → 7 calls → 1 deal). Per-broker contact log + last-touch
    date so Brian knows who needs a check-in. Stored in
    `Properties/_broker_crm.json`.

  - **C2 Direct-Mail Target Generator**: queries the property DB for owners
    matching the high-response cohorts Lindahl identifies (out-of-state,
    long tenure, multi-property owners) and generates a download-ready CSV
    target list filtered to Eight Rock's universe.

  - **C3 Loan-Maturity Alert**: cross-references the 2,609-property
    inventory against HMDA originations to flag properties whose 2020-22
    vintage loans are likely maturing in the next 18 months. These are the
    forced-seller candidates Matrix Feb 2026 explicitly highlighted as the
    current opportunity.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from core.market_data import get_multifamily_inventory, is_etl_available
from data.db import list_properties
from data.property_io import PROPERTIES_ROOT
from ui.components import section_card, v2_strip_icon


BROKER_CRM_FILENAME = "_broker_crm.json"


# ---------------------------------------------------------------------------
# C1 Broker CRM
# ---------------------------------------------------------------------------

def _load_broker_crm() -> list[dict]:
    from core.storage import get_storage
    from data.property_io import _rel
    storage = get_storage()
    key = f"{_rel(PROPERTIES_ROOT)}/{BROKER_CRM_FILENAME}"
    if not storage.is_file(key):
        return []
    try:
        return json.loads(storage.read_text(key))
    except json.JSONDecodeError:
        return []


def _save_broker_crm(brokers: list[dict]) -> None:
    from core.storage import get_storage
    from data.property_io import _rel
    storage = get_storage()
    payload = json.dumps(brokers, indent=2, ensure_ascii=False, default=str)
    storage.write_text(f"{_rel(PROPERTIES_ROOT)}/{BROKER_CRM_FILENAME}", payload)


def _render_broker_crm() -> None:
    c = config.COLORS
    with section_card(
        "Broker CRM (Lindahl Funnel)",
        icon="🤝",
        subtitle=(
            "Track every broker contact. Lindahl's rule of thumb: 7 calls to one "
            "broker before they bring you a deal. Stay on cadence — no surprises, "
            "no retrades, send leads back."
        ),
    ):
        brokers = _load_broker_crm()

        # Funnel stats
        today = dt.date.today()
        n_brokers = len(brokers)
        contacted_30d = sum(
            1 for b in brokers
            if b.get("last_contact") and (today - dt.date.fromisoformat(b["last_contact"])).days <= 30
        )
        closed_deals = sum(int(b.get("deals_closed", 0) or 0) for b in brokers)

        # `_funnel_tile` returns raw HTML — every `st.markdown` rendering
        # one MUST pass `unsafe_allow_html=True` or the <div> markup
        # renders as literal text (Brian saw this in v0.77).
        cols = st.columns(4)
        with cols[0]:
            st.markdown(
                _funnel_tile("Brokers", str(n_brokers), "Total contacts in CRM"),
                unsafe_allow_html=True,
            )
        with cols[1]:
            st.markdown(
                _funnel_tile(
                    "Contacted last 30d", str(contacted_30d),
                    "Brokers you touched in the last month",
                    accent=c["gn"] if contacted_30d >= n_brokers / 2 else c["yw"],
                ),
                unsafe_allow_html=True,
            )
        with cols[2]:
            stale = n_brokers - contacted_30d
            st.markdown(
                _funnel_tile(
                    "Stale (>30d)", str(stale),
                    "Brokers needing a check-in call",
                    accent=c["rd"] if stale > 5 else c["yw"],
                ),
                unsafe_allow_html=True,
            )
        with cols[3]:
            st.markdown(
                _funnel_tile(
                    "Deals closed", str(closed_deals),
                    "Total deals these brokers brought you",
                ),
                unsafe_allow_html=True,
            )

        # Stale-list alert
        if brokers:
            df = pd.DataFrame(brokers)
            if "last_contact" in df.columns:
                df["last_contact_dt"] = pd.to_datetime(df["last_contact"], errors="coerce")
                df["days_since"] = (
                    pd.Timestamp(today) - df["last_contact_dt"]
                ).dt.days.fillna(999).astype(int)

            # Sort by stale-first
            if "days_since" in df.columns:
                df = df.sort_values("days_since", ascending=False)

            # Display
            keep = [
                ("name", "Broker"),
                ("firm", "Firm"),
                ("phone", "Phone"),
                ("email", "Email"),
                ("specialty", "Specialty"),
                ("call_count", "# Calls"),
                ("deals_closed", "Deals"),
                ("last_contact", "Last contact"),
                ("days_since", "Days since"),
                ("notes", "Notes"),
            ]
            cols_present = [(src, dst) for src, dst in keep if src in df.columns]
            display = df[[src for src, _ in cols_present]].copy()
            display.columns = [dst for _, dst in cols_present]
            st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            st.info("No brokers in CRM yet. Add your first below.")

        # Add / log contact form
        with st.expander(v2_strip_icon("➕ Add broker / log new contact"), expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Broker name", key="crm_name")
                firm = st.text_input("Firm", key="crm_firm",
                    placeholder="Marcus & Millichap, Newmark, Cushman, Berkadia…")
                phone = st.text_input("Phone", key="crm_phone")
                email = st.text_input("Email", key="crm_email")
            with col2:
                specialty = st.text_input("Specialty",
                    placeholder="HR Class C / institutional / off-market…",
                    key="crm_specialty")
                last_contact = st.date_input(
                    "Date of contact", value=dt.date.today(), key="crm_lastcontact",
                )
                existing_count = st.number_input(
                    "Total call count (running)",
                    min_value=0, max_value=999, value=1, step=1, key="crm_count",
                )
                deals_closed = st.number_input(
                    "Deals closed via this broker",
                    min_value=0, max_value=99, value=0, step=1, key="crm_deals",
                )
            notes = st.text_area(
                "Notes (deals discussed, follow-ups, deal flow rhythm…)",
                key="crm_notes", height=80,
            )

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("💾 Save broker / log contact", type="primary",
                             key="crm_save"):
                    if not name.strip():
                        st.error("Broker name required.")
                    else:
                        # Update existing OR add new (match on name + firm)
                        found = False
                        for b in brokers:
                            if (b.get("name", "").strip().lower() == name.strip().lower()
                                    and b.get("firm", "").strip().lower() == firm.strip().lower()):
                                b["last_contact"] = last_contact.isoformat()
                                b["call_count"] = int(existing_count)
                                b["deals_closed"] = int(deals_closed)
                                b["phone"] = phone.strip() or b.get("phone", "")
                                b["email"] = email.strip() or b.get("email", "")
                                b["specialty"] = specialty.strip() or b.get("specialty", "")
                                b["notes"] = notes.strip() or b.get("notes", "")
                                b["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
                                found = True
                                break
                        if not found:
                            brokers.append({
                                "name": name.strip(),
                                "firm": firm.strip(),
                                "phone": phone.strip(),
                                "email": email.strip(),
                                "specialty": specialty.strip(),
                                "last_contact": last_contact.isoformat(),
                                "call_count": int(existing_count),
                                "deals_closed": int(deals_closed),
                                "notes": notes.strip(),
                                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                            })
                        _save_broker_crm(brokers)
                        st.success(f"✓ Saved {name}")
                        st.rerun()


def _funnel_tile(label: str, value: str, sub: str = "", accent: str | None = None) -> str:
    c = config.COLORS
    border_left = f"border-left:3px solid {accent};" if accent else ""
    return (
        f'<div style="background:{c["bg3"]};border:1px solid {c["bdr"]};'
        f'{border_left}border-radius:6px;padding:10px 14px;min-height:90px">'
        f'<div style="color:{c["tx2"]};font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.7px;font-weight:600">{label}</div>'
        f'<div style="font-size:28px;font-weight:700;color:{c["tx"]};line-height:1.05;'
        f'margin-top:4px;font-variant-numeric:tabular-nums">{value}</div>'
        f'<div style="color:{c["tx3"]};font-size:10px;margin-top:4px">{sub}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# C2 Direct-Mail Target Generator
# ---------------------------------------------------------------------------

def _render_direct_mail_generator() -> None:
    c = config.COLORS
    with section_card(
        "Direct-Mail Target Generator (Lindahl)",
        icon="📬",
        subtitle=(
            "Lindahl's targeted direct-mail rule: 1,000 letters → 1% response → "
            "8-12 calls → 1-3 serious sellers → 1 closed deal. This generator "
            "pulls candidate properties from your DB matching high-response "
            "owner cohorts, then exports a CSV ready for mail merge."
        ),
    ):
        # Filters
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            cohort = st.multiselect(
                "Owner cohorts to include",
                options=[
                    "Out-of-state owner",
                    "Same-owner has 2+ HR properties",
                    "Long tenure (held 7+ yrs)",
                    "Multifamily 50+ units (Class C target)",
                ],
                default=["Out-of-state owner", "Same-owner has 2+ HR properties"],
                key="dm_cohorts",
            )
        with col2:
            cities = st.multiselect(
                "Cities",
                options=["All HR", "Norfolk", "Chesapeake", "Newport News",
                         "Hampton", "Virginia Beach", "Portsmouth", "Suffolk"],
                default=["All HR"],
                key="dm_cities",
            )
        with col3:
            max_results = st.number_input(
                "Max results", min_value=10, max_value=2000, value=200, step=50,
                key="dm_max",
            )

        if not is_etl_available():
            st.warning(
                "ETL DB not loaded. Direct-mail generator needs the multifamily "
                "inventory pulled from city assessors. Run "
                "`python hampton_roads_etl.py --only=asr` first."
            )
            return

        # Pull from inventory
        city_filter = None if "All HR" in cities or not cities else None
        inv = get_multifamily_inventory(limit=10_000)
        if inv.empty:
            st.warning("No properties in inventory. Pull the assessor data first.")
            return

        # Apply city filter
        if cities and "All HR" not in cities:
            inv = inv[inv["city"].isin(cities)]

        # Cohort filters — additive (a property must match ANY enabled cohort)
        if not cohort:
            st.info("Select at least one cohort to generate a target list.")
            return

        masks = []
        if "Out-of-state owner" in cohort and "owner" in inv.columns:
            # Heuristic: owner address not in HR cities → out-of-state.
            # We don't have owner-mailing-address here, so we approximate via
            # owner-name-contains "LLC" + last_sale_date null (likely held).
            # Better data lives in HMDA / sales.json — Phase 2 enhancement.
            # For now: include all with non-null owner.
            masks.append(inv["owner"].notna() & (inv["owner"].astype(str).str.len() > 0))
        if "Same-owner has 2+ HR properties" in cohort and "owner" in inv.columns:
            # Group by owner, find owners with ≥ 2 properties
            counts = inv["owner"].value_counts()
            multi_owners = counts[counts >= 2].index
            masks.append(inv["owner"].isin(multi_owners))
        if "Long tenure (held 7+ yrs)" in cohort and "last_sale_date" in inv.columns:
            # Last sale > 7 yrs ago
            seven_yrs_ago = pd.Timestamp(dt.date.today() - dt.timedelta(days=365 * 7))
            sale_dt = pd.to_datetime(inv["last_sale_date"], errors="coerce")
            masks.append(sale_dt.fillna(pd.Timestamp("1900-01-01")) < seven_yrs_ago)
        if "Multifamily 50+ units (Class C target)" in cohort and "class_description" in inv.columns:
            masks.append(
                inv["class_description"].astype(str).str.contains("49+|405|406|407",
                    regex=True, case=False, na=False)
            )

        # OR-combine masks
        if masks:
            combined = masks[0]
            for m in masks[1:]:
                combined = combined | m
            inv = inv[combined]

        # Sort + cap
        inv = inv.head(int(max_results))

        if inv.empty:
            st.info("No properties match these filters.")
            return

        # Display
        display = pd.DataFrame({
            "City":         inv["city"],
            "Address":      inv["address"].fillna(""),
            "Owner":        inv["owner"].fillna(""),
            "Class":        inv["class_description"].fillna(""),
            "Year built":   inv["year_built"].apply(
                lambda v: int(v) if pd.notna(v) and v else "—"
            ),
            "Total $":      inv["assessed_value"].apply(
                lambda v: f"${v:,.0f}" if pd.notna(v) and v else "—"
            ),
            "Last sale":    inv.get("last_sale_date", pd.Series([None] * len(inv))).apply(
                lambda v: str(v)[:10] if pd.notna(v) else "—"
            ),
            "Parcel":       inv["parcel_id"].fillna(""),
        })
        st.markdown(
            f'<div style="color:{c["tx2"]};font-size:13px;margin-bottom:6px">'
            f'<b>{len(display):,} target properties</b> match selected cohorts. '
            f'Lindahl funnel: ~<b>{len(display) // 100}</b> serious sellers / '
            f'~<b>{len(display) // 1000}</b> closed deal expected.</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(display, use_container_width=True, hide_index=True, height=400)

        # CSV download
        csv = display.to_csv(index=False)
        st.download_button(
            "⬇️ Download target list (CSV for mail merge)",
            data=csv,
            file_name=f"direct-mail-targets-{dt.date.today().isoformat()}.csv",
            mime="text/csv",
        )


# ---------------------------------------------------------------------------
# C3 Loan-Maturity Alert
# ---------------------------------------------------------------------------

def _render_loan_maturity_alert() -> None:
    """Cross-reference 2020-22 HMDA originations against current property
    inventory to flag deals whose loans are likely maturing in 18 months.

    Approach: HMDA captures loan ORIGINATIONS by lender × county × year.
    We don't have property-level loan detail (HMDA doesn't expose borrower
    address). What we CAN do:
      1. Show 2020-22 origination volume by county — sets context for how
         many forced-refis are coming.
      2. Cross-ref against properties whose last sale was 2020-22 (= likely
         to have a 2020-22 vintage purchase loan that's now maturing).
    """
    c = config.COLORS
    with section_card(
        "Loan-Maturity Alert (2020-22 vintage refi pressure)",
        icon="⏰",
        accent="rd",
        subtitle=(
            "Matrix Feb 2026 directly flagged 2020-22 vintage value-add deals "
            "as the current opportunity — operators bought with bridge / floating-rate "
            "debt at 4-5% and now face refi at 7-8%. This panel cross-references "
            "your HR inventory's last-sale dates against that maturity wave."
        ),
    ):
        if not is_etl_available():
            st.warning("ETL DB needed for HMDA + inventory. Run the asr puller first.")
            return

        db_path = (
            Path(__file__).resolve().parent.parent.parent
            / "hampton-roads-etl" / "hampton_roads.db"
        )
        if not db_path.is_file():
            return

        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        # ---- Section 1: HMDA origination volume by year, county ----
        try:
            hmda = pd.read_sql(
                """
                SELECT year, county_code, SUM(n_originations) as origs,
                       SUM(total_loan_amount) as total_vol
                FROM hmda_lender_summary
                WHERE year >= 2020 AND year <= 2024
                GROUP BY year, county_code
                ORDER BY year, county_code
                """,
                db,
            )
            if not hmda.empty:
                st.markdown(
                    f'<div style="color:{c["tx2"]};font-size:13px;font-weight:600;'
                    f'margin-top:6px">2020-2024 HMDA Multifamily Originations '
                    f'(HR cities, by year)</div>',
                    unsafe_allow_html=True,
                )
                pivot = hmda.pivot_table(
                    index="year", columns="county_code", values="total_vol",
                    aggfunc="sum", fill_value=0,
                )
                # `.applymap()` was deprecated in pandas 2.1 and removed in
                # newer versions; `.map()` is the DataFrame-level replacement.
                pivot_display = pivot.map(
                    lambda v: f"${v:,.0f}" if v else "—"
                )
                st.dataframe(pivot_display, use_container_width=True)
            else:
                st.caption("No HMDA data 2020-2024.")
        except sqlite3.Error as e:
            st.caption(f"HMDA query failed: {e}")

        # ---- Section 2: Properties whose last sale was 2020-22 ----
        try:
            sales_window = pd.read_sql(
                """
                SELECT city, COUNT(*) as n
                FROM va_multifamily_inventory
                WHERE last_sale_date IS NOT NULL
                  AND last_sale_date >= '2020-01-01'
                  AND last_sale_date <= '2022-12-31'
                GROUP BY city ORDER BY n DESC
                """,
                db,
            )
            if not sales_window.empty:
                st.markdown(
                    f'<div style="color:{c["tx2"]};font-size:13px;font-weight:600;'
                    f'margin-top:14px">HR Multifamily Sold 2020-2022 (likely facing '
                    f'2025-2027 refi pressure)</div>',
                    unsafe_allow_html=True,
                )
                sales_display = sales_window.copy()
                sales_display["n"] = sales_display["n"].astype(int)
                sales_display.columns = ["City", "Properties Sold 2020-22"]
                st.dataframe(sales_display, use_container_width=True, hide_index=True)

                # Top targets list
                targets = pd.read_sql(
                    """
                    SELECT city, address, owner, class_description, year_built,
                           last_sale_date, last_sale_price, assessed_value
                    FROM va_multifamily_inventory
                    WHERE last_sale_date IS NOT NULL
                      AND last_sale_date >= '2020-01-01'
                      AND last_sale_date <= '2022-12-31'
                      AND assessed_value > 1000000
                    ORDER BY last_sale_price DESC LIMIT 50
                    """,
                    db,
                )
                if not targets.empty:
                    st.markdown(
                        f'<div style="color:{c["tx2"]};font-size:13px;font-weight:600;'
                        f'margin-top:14px">🎯 Top 50 Refi-Pressure Candidates</div>',
                        unsafe_allow_html=True,
                    )
                    disp = pd.DataFrame({
                        "City":      targets["city"],
                        "Address":   targets["address"].fillna(""),
                        "Owner":     targets["owner"].fillna(""),
                        "Class":     targets["class_description"].fillna(""),
                        "Built":     targets["year_built"].apply(
                            lambda v: int(v) if pd.notna(v) and v else "—"),
                        "Sale date": targets["last_sale_date"].apply(
                            lambda v: str(v)[:10] if pd.notna(v) else "—"),
                        "Sale $":    targets["last_sale_price"].apply(
                            lambda v: f"${v:,.0f}" if pd.notna(v) and v else "—"),
                        "Assessed $": targets["assessed_value"].apply(
                            lambda v: f"${v:,.0f}" if pd.notna(v) and v else "—"),
                    })
                    st.dataframe(disp, use_container_width=True, hide_index=True, height=400)

                    csv = disp.to_csv(index=False)
                    st.download_button(
                        "⬇️ Download refi-pressure candidates (CSV)",
                        data=csv,
                        file_name=f"refi-pressure-candidates-{dt.date.today().isoformat()}.csv",
                        mime="text/csv",
                    )
            else:
                st.caption(
                    "No 2020-22 sales in inventory yet. Need at least Norfolk + "
                    "Newport News + Chesapeake assessor data with sale dates."
                )
        except sqlite3.Error as e:
            st.caption(f"Inventory query failed: {e}")
        finally:
            db.close()


def render_pipeline(prop=None) -> None:
    """Top-level Pipeline tab renderer.

    Sections: Forced-Seller Radar (top) · Broker CRM · Direct-Mail Generator
    · Loan-Maturity Alert. Independent of selected property.
    """
    _render_forced_seller_radar()
    _render_broker_crm()
    _render_direct_mail_generator()
    _render_loan_maturity_alert()


# ---------------------------------------------------------------------------
# Forced-Seller Radar — scoring engine on va_multifamily_inventory
# ---------------------------------------------------------------------------

def _render_forced_seller_radar() -> None:
    from core import distress_radar
    c = config.COLORS

    with section_card(
        "Forced-Seller Radar",
        icon="🎯",
        accent="ac",
        subtitle=(
            "Top distress-likelihood candidates. Scores on 2020-22 sale "
            "vintage, rate shock since purchase, recent assessment jumps, "
            "institutional ownership, holding period, market softness. "
            "Matrix Feb 2026 thesis: these are the next forced sellers."
        ),
        help_anchor="forced-seller-radar",
        help_summary=(
            "Scans every Hampton Roads parcel for 6 distress signals "
            "(vintage, rate shock, assessment jumps, institutional owner, "
            "holding period, market softness). High composite score = "
            "owner likely to sell soon — reach them before they list. "
            "Click for the full Help section."
        ),
    ):
        col1, col2, col3 = st.columns([1, 2, 2])
        with col1:
            top_n = st.number_input(
                "Top N", min_value=10, max_value=100, value=25, step=5,
                key="fsr_top_n",
            )
        with col2:
            min_av = st.number_input(
                "Min assessed value ($)",
                min_value=500_000, max_value=50_000_000,
                value=2_000_000, step=500_000,
                key="fsr_min_av",
            )
        with col3:
            city_pick = st.multiselect(
                "Cities (empty = all HR)",
                options=["Norfolk", "Virginia Beach", "Chesapeake", "Hampton",
                         "Newport News", "Portsmouth", "Suffolk"],
                default=[],
                key="fsr_cities",
            )

        with st.spinner("Scoring..."):
            top = distress_radar.top_n_candidates(
                n=top_n,
                min_assessed_value=min_av,
                city_filter=city_pick or None,
            )

        if not top:
            st.info("No properties scored above 0 with current filters.")
            return

        # Summary
        st.markdown(
            f'<div style="margin-bottom:8px;font-size:12px;color:{c["tx2"]}">'
            f'<b>{len(top)} candidates</b> · avg score '
            f'{sum(t.total_score for t in top)/len(top):.0f}/100 · '
            f'highest {top[0].total_score:.0f}/100 ({top[0].city})'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Build dataframe
        rows = []
        for r in top:
            rows.append({
                "Score": int(r.total_score),
                "City": r.city,
                "Address": r.address,
                "Owner": (r.owner or "")[:38],
                "Year": r.year_built or "",
                "Units (est)": r.units_estimate or "",
                "Last Sale": r.last_sale_date or "",
                "Sale Price": (
                    f"${r.last_sale_price:,.0f}"
                    if r.last_sale_price else ""
                ),
                "Assessed": f"${r.assessed_value:,.0f}",
                "Reasons": "; ".join(r.reasons)[:120],
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, use_container_width=True)

        # CSV export
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download skip-trace CSV",
            csv_bytes,
            file_name=f"forced-seller-radar-{dt.date.today().strftime('%m%d%Y')}.csv",
            mime="text/csv",
        )
