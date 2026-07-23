"""Owner Portal — per-property LP/GP capital + distributions + IR.

Five sections:

  1. Term Sheet Evaluator (placeholder — AI extraction lands in a follow-on)
  2. LP/GP Capital Ledger (fully working — add investors, call capital,
     record distributions, K-1 XLSX export)
  3. Distribution Engine (fully working — preview the 8% pref / ROC /
     70-30 waterfall for a quarterly cash pot; commit to ledger)
  4. IR Updates (markdown editor placeholder — Graph email-send deferred)
  5. Property Operations (AppFolio export upload placeholder — AI summary
     of "this week's PM directive" deferred)

All sections scope to the currently-selected property in the sidebar.
LP/GP data lives in ``<property-folder>/lp_ledger.json`` (atomic write
via core/storage.py — works in local + future Graph modes).
"""

from __future__ import annotations

import datetime as dt
import io
import os
from pathlib import Path
from typing import Any

import streamlit as st

import config
from core import distribution_engine as dist
from core import lp_gp_ledger as lg
from data.property_io import PropertyFolder
from ui.components import section_card, v2_strip_icon


# ---------------------------------------------------------------------------
# Public entry point — called from app.py
# ---------------------------------------------------------------------------

def render_owner_portal(prop: dict[str, Any], folder: PropertyFolder | None) -> None:
    if folder is None or folder.path is None:
        st.info(
            "This property doesn't have an on-disk folder yet. Add one via the "
            "sidebar before using Investors."
        )
        return

    fp = folder.path
    ledger = lg.load(fp)
    if not ledger.deal_id:
        ledger.deal_id = prop.get("name") or fp.name

    _render_header(ledger, prop)

    sec1, sec2, sec3, sec4, sec5 = st.tabs([
        "📄 Term Sheets",
        "💰 LP/GP Capital",
        "📤 Distribution",
        "✉️ IR Updates",
        "🏗️ Property Ops",
    ])
    with sec1:
        _render_term_sheets(fp)
    with sec2:
        _render_capital_ledger(ledger, fp)
    with sec3:
        _render_distribution_engine(ledger, fp, prop)
    with sec4:
        _render_ir_updates(fp)
    with sec5:
        _render_property_ops(fp)


# ---------------------------------------------------------------------------
# Header — KPI tiles
# ---------------------------------------------------------------------------

def _render_header(ledger: lg.Ledger, prop: dict[str, Any]) -> None:
    c = config.COLORS

    n_lps = len(ledger.lps())
    n_gps = len(ledger.gps())
    total_accrued_pref = sum(
        lg.compute_accrued_pref(ledger, lp.investor_id) for lp in ledger.lps()
    )

    with section_card(
        f"Investors — {ledger.deal_id}",
        icon="💼",
        accent="ac",
        subtitle=(
            f"{n_lps} LP{'s' if n_lps != 1 else ''} · {n_gps} GP{'s' if n_gps != 1 else ''} · "
            f"last updated {ledger.as_of or 'never'}"
        ),
    ):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(_tile(
                "Raise Target",
                f"${ledger.raise_target:,.0f}" if ledger.raise_target else "—",
                f"Remaining: ${ledger.remaining_to_raise:,.0f}" if ledger.raise_target else "Set in Capital tab",
                color=c["ac3"], c=c,
            ), unsafe_allow_html=True)
        with col2:
            st.markdown(_tile(
                "Committed",
                f"${ledger.total_committed:,.0f}",
                f"{n_lps} LP{'s' if n_lps != 1 else ''}",
                color=c["bl"], c=c,
            ), unsafe_allow_html=True)
        with col3:
            st.markdown(_tile(
                "Called",
                f"${ledger.total_called:,.0f}",
                f"{(ledger.total_called/ledger.total_committed*100) if ledger.total_committed else 0:.0f}% of commit",
                color=c["gn"], c=c,
            ), unsafe_allow_html=True)
        with col4:
            color_pref = c["rd"] if total_accrued_pref > 1000 else c["tx3"]
            st.markdown(_tile(
                "Accrued Pref",
                f"${total_accrued_pref:,.0f}",
                f"Across all LPs · 8% NC",
                color=color_pref, c=c,
            ), unsafe_allow_html=True)


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


# ---------------------------------------------------------------------------
# Section 1: Term Sheets (placeholder)
# ---------------------------------------------------------------------------

def _render_term_sheets(folder: Path) -> None:
    with section_card("Term Sheet Evaluator", icon="📄"):
        st.markdown(
            "**Coming soon.** Drop one or more term sheet PDFs or DOCX files "
            "below; the workbench will use Claude to extract LTV, rate, IO, "
            "amortization, prepay penalty, recourse, reserves, and closing "
            "costs from each, then score them side-by-side against the "
            "current calibrated MIN_DEBT_YIELD + INTEREST_RATE market context."
        )
        files = st.file_uploader(
            "Drop term sheets here (saved to property folder — AI extraction in next release)",
            type=["pdf", "docx"],
            accept_multiple_files=True,
            key=f"ts_upload_{folder.name}",
        )
        if files:
            target_dir = folder / "term-sheets"
            target_dir.mkdir(parents=True, exist_ok=True)
            for f in files:
                (target_dir / f.name).write_bytes(f.getbuffer())
                st.success(f"Saved {f.name}")


# ---------------------------------------------------------------------------
# Section 2: LP/GP Capital Ledger (fully working)
# ---------------------------------------------------------------------------

def _render_capital_ledger(ledger: lg.Ledger, folder: Path) -> None:
    c = config.COLORS

    with section_card("Deal-level Settings", icon="⚙️"):
        new_target = st.number_input(
            "Raise target ($)",
            min_value=0.0,
            value=float(ledger.raise_target),
            step=10_000.0,
            help="Total equity Eight Rock is raising for this deal.",
        )
        if new_target != ledger.raise_target:
            ledger.raise_target = new_target
            lg.save(folder, ledger)
            st.success("Raise target updated.")

    with section_card("Investors", icon="👥",
                      subtitle="Named LP/GP subscribers; commitments, capital called, distributions paid."):
        if not ledger.investors:
            st.caption("No investors yet — add one below.")
        else:
            import pandas as pd
            rows = []
            for inv in ledger.investors:
                accrued = lg.compute_accrued_pref(ledger, inv.investor_id) if inv.kind == "LP" else 0.0
                rows.append({
                    "Name": inv.name,
                    "Email": inv.email or "—",
                    "Kind": inv.kind,
                    "Commitment": f"${inv.commitment:,.0f}",
                    "Called": f"${inv.called_capital:,.0f}",
                    "Distributions": f"${inv.distributions_received:,.0f}",
                    "Unreturned": f"${inv.unreturned_capital:,.0f}",
                    "Accrued Pref": f"${accrued:,.0f}" if inv.kind == "LP" else "—",
                    "Notes": inv.notes[:40] if inv.notes else "",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

            # Brian 5/29 v2.0.37 — pick an existing investor to edit or
            # delete. Two-step delete with explicit checkbox confirm so
            # a single misclick can't wipe a record.
            with st.expander(v2_strip_icon("✏️ Edit / delete an investor"), expanded=False):
                names = {f"{inv.name} ({inv.kind})": inv for inv in ledger.investors}
                pick = st.selectbox(
                    "Choose investor",
                    options=list(names.keys()),
                    key=f"edit_inv_pick_{folder.name}",
                )
                tgt = names[pick]
                with st.form(f"edit_investor_{folder.name}_{tgt.investor_id}",
                             clear_on_submit=False):
                    c1, c2, c3 = st.columns([3, 1, 2])
                    with c1:
                        e_name = st.text_input("Name", value=tgt.name)
                    with c2:
                        e_kind = st.selectbox(
                            "Kind", ["LP", "GP"],
                            index=0 if tgt.kind == "LP" else 1,
                        )
                    with c3:
                        e_commit = st.number_input(
                            "Commitment ($)",
                            min_value=0.0, value=float(tgt.commitment),
                            step=10_000.0,
                        )
                    e_email = st.text_input(
                        "Email",
                        value=tgt.email,
                        placeholder="investor@example.com",
                    )
                    e_notes = st.text_input("Notes (optional)", value=tgt.notes)

                    col_save, col_del = st.columns([1, 1])
                    with col_save:
                        save_btn = st.form_submit_button("💾 Save changes", type="primary")
                    with col_del:
                        confirm_del = st.checkbox(
                            f"Confirm: delete {tgt.name}",
                            key=f"del_confirm_{tgt.investor_id}",
                        )
                        del_btn = st.form_submit_button("🗑️ Delete investor")

                    if save_btn and e_name.strip():
                        lg.update_investor(
                            ledger, tgt.investor_id,
                            name=e_name.strip(),
                            kind=e_kind,
                            commitment=e_commit,
                            notes=e_notes,
                            email=e_email.strip(),
                        )
                        lg.save(folder, ledger)
                        st.success(f"Updated {e_name}.")
                        st.rerun()
                    if del_btn and confirm_del:
                        lg.remove_investor(ledger, tgt.investor_id)
                        lg.save(folder, ledger)
                        st.success(f"Removed {tgt.name}.")
                        st.rerun()
                    elif del_btn and not confirm_del:
                        st.warning(
                            "Tick the confirm box before deleting — "
                            "this also removes any recorded events for "
                            "this investor."
                        )

        with st.expander(v2_strip_icon("➕ Add investor"), expanded=False):
            with st.form(f"add_investor_{folder.name}", clear_on_submit=True):
                col1, col2, col3 = st.columns([3, 1, 2])
                with col1:
                    new_name = st.text_input("Name", placeholder="Alice Smith")
                with col2:
                    new_kind = st.selectbox("Kind", ["LP", "GP"], index=0)
                with col3:
                    new_commit = st.number_input(
                        "Commitment ($)",
                        min_value=0.0, value=0.0, step=10_000.0,
                    )
                # Brian 5/29 v2.0.37 — email captured so IR-update sends
                # can target each investor directly.
                new_email = st.text_input(
                    "Email",
                    placeholder="investor@example.com",
                    help="Used to send IR updates from the workbench.",
                )
                new_notes = st.text_input(
                    "Notes (optional)",
                    placeholder="subscribed 2024-03-15",
                )
                submitted = st.form_submit_button("Add investor", type="primary")
                if submitted and new_name.strip():
                    lg.add_investor(
                        ledger,
                        new_name.strip(),
                        new_commit,
                        new_kind,
                        new_notes,
                        email=new_email.strip(),
                    )
                    lg.save(folder, ledger)
                    st.success(f"Added {new_name}.")
                    st.rerun()

    with section_card("Record an Event", icon="📝",
                      subtitle="Capital call or distribution to a specific investor."):
        if not ledger.investors:
            st.caption("Add an investor first.")
            return

        col1, col2, col3, col4 = st.columns([3, 1, 2, 2])
        with col1:
            inv_pick = st.selectbox(
                "Investor",
                options=[f"{i.name} ({i.kind})" for i in ledger.investors],
                key=f"event_inv_{folder.name}",
            )
            inv_idx = [f"{i.name} ({i.kind})" for i in ledger.investors].index(inv_pick)
            inv = ledger.investors[inv_idx]
        with col2:
            ev_type = st.selectbox(
                "Type", ["capital_call", "distribution"],
                key=f"event_type_{folder.name}",
            )
        with col3:
            amount = st.number_input(
                "Amount ($)", min_value=0.0, value=0.0, step=1_000.0,
                key=f"event_amt_{folder.name}",
            )
        with col4:
            ev_date = st.date_input(
                "Date", value=dt.date.today(),
                key=f"event_date_{folder.name}",
            )

        tier = "pref"
        if ev_type == "distribution":
            tier = st.selectbox(
                "Tier (which waterfall layer this distribution comes from)",
                ["pref", "roc", "residual", "promote"],
                key=f"event_tier_{folder.name}",
            )

        ev_notes = st.text_input(
            "Notes", key=f"event_notes_{folder.name}",
            placeholder="Initial subscription / Q4 2024 pref distribution / etc.",
        )

        if st.button(
            f"Record {ev_type.replace('_', ' ')} of ${amount:,.0f} to {inv.name}",
            key=f"event_submit_{folder.name}",
            type="primary",
            disabled=amount <= 0,
        ):
            if ev_type == "capital_call":
                lg.record_capital_call(ledger, inv.investor_id, amount, ev_date.isoformat(), ev_notes)
            else:
                lg.record_distribution(ledger, inv.investor_id, amount, tier, ev_date.isoformat(), ev_notes)
            lg.save(folder, ledger)
            st.success(f"Recorded.")
            st.rerun()

    # ---- Event history + K-1 export ----
    with section_card("Event History", icon="📜"):
        if not ledger.events:
            st.caption("No events recorded yet.")
        else:
            import pandas as pd
            rows = []
            for ev in sorted(ledger.events, key=lambda e: e.date, reverse=True):
                inv = ledger.investor(ev.investor_id)
                rows.append({
                    "Date": ev.date,
                    "Investor": inv.name if inv else ev.investor_id,
                    "Type": ev.type.replace("_", " ").title(),
                    "Amount": f"${ev.amount:,.0f}",
                    "Tier": (ev.tier or "").upper(),
                    "Notes": ev.notes,
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        # XLSX export
        if ledger.investors:
            if st.button("📥 Export K-1 ready XLSX", key=f"export_k1_{folder.name}"):
                import tempfile
                with tempfile.NamedTemporaryFile(
                    "wb", suffix=".xlsx", delete=False,
                ) as tmp:
                    tmp_path = Path(tmp.name)
                lg.export_k1_xlsx(ledger, tmp_path)
                with open(tmp_path, "rb") as f:
                    st.download_button(
                        "Click to download",
                        f.read(),
                        file_name=f"{ledger.deal_id.replace(' ', '-')}-K1-{dt.date.today().strftime('%m%d%Y')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Section 3: Distribution Engine (fully working)
# ---------------------------------------------------------------------------

def _render_distribution_engine(ledger: lg.Ledger, folder: Path, prop: dict[str, Any]) -> None:
    c = config.COLORS

    if not ledger.lps():
        st.info("Add at least one LP in the Capital tab before running a distribution.")
        return

    with section_card(
        "Distribution Engine",
        icon="📤",
        subtitle=(
            "Run Eight Rock's 8% pref / ROC / 70-30 waterfall on this quarter's "
            "cash pot. Preview first, commit when you're happy. Commit writes "
            "events to the ledger."
        ),
    ):
        col1, col2 = st.columns([2, 3])
        with col1:
            cash_to_distribute = st.number_input(
                "Cash to distribute ($)",
                min_value=0.0, value=0.0, step=1_000.0,
                help="Quarterly operating cash flow or sale proceeds available for distribution",
                key=f"dist_cash_{folder.name}",
            )
        with col2:
            as_of = st.date_input(
                "As-of date",
                value=dt.date.today(),
                help="Pref accrues through this date; events get tagged with it",
                key=f"dist_date_{folder.name}",
            )

        if cash_to_distribute <= 0:
            st.caption("Enter an amount above to preview the waterfall.")
            return

        plan = dist.preview_distribution(ledger, cash_to_distribute, as_of)

        # ---- Mechanism trace ----
        st.markdown(
            f'<div style="background:{c["bg3"]};border-left:3px solid {c["ac"]};'
            f'border-radius:4px;padding:10px 14px;margin-top:8px;margin-bottom:8px">'
            f'<div style="font-size:11px;color:{c["tx3"]};font-weight:600;'
            f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">'
            f'Waterfall Trace</div>'
            + "".join(
                f'<div style="font-size:13px;color:{c["tx"]};line-height:1.5">• {t}</div>'
                for t in plan.trace
            )
            + f'</div>',
            unsafe_allow_html=True,
        )

        # ---- Per-investor payments table ----
        import pandas as pd
        rows = []
        for p in plan.payments:
            rows.append({
                "Investor": p.investor_name,
                "Kind": p.kind,
                "Pref": f"${p.pref_paid:,.0f}",
                "ROC": f"${p.roc_paid:,.0f}",
                "Residual": f"${p.residual_paid:,.0f}",
                "Promote": f"${p.promote_paid:,.0f}",
                "Total Check": f"${p.total:,.0f}",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        st.caption(
            f"Total paid: ${plan.total_paid:,.0f}  ·  "
            f"Available: ${plan.available_cash:,.0f}  ·  "
            f"Remaining (should be ~$0): ${plan.cash_remaining:,.0f}"
        )

        # ---- Commit ----
        col_commit, _ = st.columns([2, 5])
        with col_commit:
            if st.button(
                "💾 Commit to ledger",
                key=f"dist_commit_{folder.name}",
                type="primary",
                help="Records each non-zero amount as a distribution event tagged with its tier",
            ):
                n = dist.apply_distribution(
                    ledger, plan,
                    notes=f"Q{(as_of.month - 1) // 3 + 1} {as_of.year} distribution",
                )
                lg.save(folder, ledger)
                st.success(f"Recorded {n} distribution events. Ledger updated.")
                st.rerun()


# ---------------------------------------------------------------------------
# Section 4: IR Updates (placeholder)
# ---------------------------------------------------------------------------

def _render_ir_updates(folder: Path) -> None:
    with section_card(
        "Investor Relations Updates",
        icon="✉️",
        subtitle="Quarterly LP updates archived in the property folder.",
    ):
        st.markdown(
            "**Markdown editor live; Microsoft Graph LP email-send deferred** "
            "to a follow-on session. For now: write your quarterly update "
            "below, hit save, and the file lands in `ir-updates/<date>.md` "
            "in this property's folder. You can email the file content "
            "manually until the Graph integration lands."
        )

        # Templated sections — gives Brian a starting structure
        default_template = """## This Quarter's KPIs
- Occupancy:
- Average rent:
- Collections:
- NOI trend:

## Capex Progress
-
-

## Operating Highlights
-

## Next Quarter Plan
-

## Risks / Watch Items
-
"""

        ir_dir = folder / "ir-updates"
        latest_file = None
        if ir_dir.is_dir():
            files = sorted(ir_dir.glob("*.md"), reverse=True)
            if files:
                latest_file = files[0]

        default_content = (
            latest_file.read_text(encoding="utf-8")
            if latest_file else default_template
        )
        if latest_file:
            st.caption(f"Loaded latest: `{latest_file.name}`")

        content = st.text_area(
            "Quarterly update (markdown)",
            value=default_content,
            height=400,
            key=f"ir_content_{folder.name}",
        )

        col1, col2, _ = st.columns([1, 1, 4])
        with col1:
            if st.button("💾 Save", key=f"ir_save_{folder.name}", type="primary"):
                ir_dir.mkdir(parents=True, exist_ok=True)
                stamp = dt.date.today().strftime("%Y-Q") + str((dt.date.today().month - 1) // 3 + 1)
                target = ir_dir / f"{stamp}.md"
                target.write_text(content, encoding="utf-8")
                st.success(f"Saved {target.name}")
        with col2:
            st.button(
                "📧 Send to LPs",
                key=f"ir_send_{folder.name}",
                disabled=True,
                help="Microsoft Graph LP email-send lands in a future session",
            )


# ---------------------------------------------------------------------------
# Section 5: Property Operations (placeholder)
# ---------------------------------------------------------------------------

def _render_property_ops(folder: Path) -> None:
    with section_card(
        "Property Operations (AppFolio uploads)",
        icon="🏗️",
        subtitle="Drop AppFolio exports (rent rolls, owner statements, work orders, capex).",
    ):
        st.markdown(
            "**Upload now; AI 'this week's PM directive' summary lands next "
            "release.** Files are saved to `ops-uploads/<date>/<filename>` "
            "in this property folder."
        )
        uploaded = st.file_uploader(
            "Drop AppFolio reports",
            type=["pdf", "xlsx", "xls", "csv", "docx"],
            accept_multiple_files=True,
            key=f"ops_upload_{folder.name}",
        )
        if uploaded:
            ops_dir = folder / "ops-uploads" / dt.date.today().isoformat()
            ops_dir.mkdir(parents=True, exist_ok=True)
            for f in uploaded:
                (ops_dir / f.name).write_bytes(f.getbuffer())
                st.success(f"Saved {f.name}")

        # Show existing uploads
        ops_root = folder / "ops-uploads"
        if ops_root.is_dir():
            dates = sorted([p for p in ops_root.iterdir() if p.is_dir()], reverse=True)
            if dates:
                with st.expander(f"Past uploads ({len(dates)} dates)", expanded=False):
                    for d in dates[:10]:
                        files = sorted(d.iterdir())
                        st.markdown(f"**{d.name}** — {len(files)} file{'s' if len(files) != 1 else ''}")
                        for f in files:
                            st.caption(f"  • {f.name} · {f.stat().st_size // 1024} KB")
