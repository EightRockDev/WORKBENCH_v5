"""Module D UI — Inbox -> Deal (spec §6.2).

Three surfaces: the **confirm queue** (low-confidence extractions awaiting one
click), the **pipeline** built with zero manual entry, and **term-sheet history**
captured from lender mail.

The queue is the point of the module: nothing below the confidence gate is ever
written silently, so this is where a human spends ten seconds instead of ten
minutes of data entry.
"""

from __future__ import annotations

import streamlit as st

from core import inbox
from core.inbox import engine
from core.permissions import Permissions
from data import pg

_CAT_ICON = {"broker": "🏢", "lender": "🏦", "attorney": "⚖️", "lp": "💼", "other": "📧"}
_STAGES = ["lead", "screening", "loi", "under_contract", "closed", "dead", "no_go"]


def render_inbox() -> None:
    st.subheader("📥 Inbox → Deal")
    st.caption("Broker, lender and attorney mail becomes pipeline records with no "
               "manual entry. Anything the extractor isn't confident about waits "
               "here for one click instead of being written silently.")

    perms, org_id = st.session_state.get("perms"), st.session_state.get("org_id")
    if isinstance(perms, Permissions) and not perms.can_open("documents"):
        st.info(f"🔒 Inbox → Deal isn't included in your role (`{perms.role_preset}`).")
        return
    if not pg.is_configured() or not org_id:
        st.warning("Inbox → Deal needs the PostgreSQL store and an organization "
                   "context.", icon="⚠️")
        return

    status = inbox.provider_status()
    c1, c2 = st.columns([3, 1])
    c1.caption(("🟢 " if status.startswith("live") else "🧪 ") +
               f"Mail source: **{status}**"
               + ("" if status.startswith("live") else
                  " — set `ER_INBOX_PROVIDER=graph` + `MS_GRAPH_TOKEN` in .env "
                  "to connect your real Outlook mailbox."))
    if c2.button("🔄 Sync inbox", type="primary"):
        results = inbox.sync_inbox(org_id)
        auto = sum(1 for r in results if r.status == "auto_applied")
        queued = sum(1 for r in results if r.status == "queued")
        st.success(f"Ingested {len(results)} message(s) — {auto} auto-applied, "
                   f"{queued} queued for confirm.")
        st.rerun()

    q = engine.list_queue(org_id)
    t_queue, t_pipe, t_terms, t_all = st.tabs(
        [f"✅ Confirm queue ({len(q)})", "📊 Pipeline", "🏦 Term sheets", "📧 All mail"])
    with t_queue:
        _render_queue(org_id, q)
    with t_pipe:
        _render_pipeline(org_id)
    with t_terms:
        _render_terms(org_id)
    with t_all:
        _render_all(org_id)


def _render_queue(org_id: str, q: list[dict]) -> None:
    if not q:
        st.success("Nothing waiting. Everything ingested either cleared the "
                   "confidence gate or wasn't deal-related.")
        return
    st.caption("These extractions fell below the confidence gate. Correct anything "
               "that's wrong and confirm — or dismiss.")
    for m in q:
        f = ((m.get("extracted") or {}).get("fields")) or {}
        conf = (m.get("extracted") or {}).get("confidence", 0)
        with st.container(border=True):
            st.markdown(f"{_CAT_ICON.get(m['category'], '📧')} **{m['subject'] or '(no subject)'}**  \n"
                        f"from {m.get('from_name') or ''} <{m.get('from_email')}> · "
                        f"classify {float(m.get('confidence') or 0):.0%} · "
                        f"extract {float(conf):.0%}")
            with st.expander("Message body"):
                st.text(m.get("body") or "")
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Deal name", value=f.get("name") or "", key=f"n-{m['id']}")
            addr = c1.text_input("Address", value=f.get("address") or "", key=f"a-{m['id']}")
            city = c2.text_input("City", value=f.get("city") or "", key=f"c-{m['id']}")
            state = c2.text_input("State", value=f.get("state") or "", key=f"s-{m['id']}")
            units = c3.number_input("Units", 0, 5000, int(f.get("units") or 0),
                                    key=f"u-{m['id']}")
            price = c3.number_input("Asking price", 0.0, 1e9,
                                    float(f.get("asking_price") or 0), step=50_000.0,
                                    key=f"p-{m['id']}")
            b1, b2 = st.columns([1, 5])
            if b1.button("✅ Confirm", key=f"ok-{m['id']}", type="primary"):
                if not name.strip():
                    st.error("A deal name (or address) is required.")
                else:
                    engine.confirm_message(org_id, str(m["id"]), overrides={
                        "name": name.strip(), "address": addr.strip() or None,
                        "city": city.strip() or None, "state": state.strip() or None,
                        "units": int(units) or None,
                        "asking_price": float(price) or None},
                        actor_user_id=_uid())
                    st.success(f"'{name}' added to the pipeline.")
                    st.rerun()
            if b2.button("Dismiss", key=f"no-{m['id']}"):
                engine.dismiss_message(org_id, str(m["id"]), actor_user_id=_uid())
                st.rerun()


def _render_pipeline(org_id: str) -> None:
    deals = engine.list_deals(org_id)
    if not deals:
        st.info("No pipeline records yet — sync the inbox to create them automatically.")
        return
    st.caption(f"{len(deals)} deal(s), created with zero manual entry.")
    for d in deals:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 2])
            price = f"${float(d['asking_price']):,.0f}" if d.get("asking_price") else "—"
            cap = f"{float(d['cap_rate'])*100:.2f}%" if d.get("cap_rate") else "—"
            c1.markdown(f"**{d['name']}**  \n{d.get('address') or ''} "
                        f"{d.get('city') or ''} {d.get('state') or ''}")
            c2.markdown(f"{d.get('units') or '—'} units · {price}  \ncap {cap}")
            new_stage = c3.selectbox("Stage", _STAGES, index=_STAGES.index(d["stage"]),
                                     key=f"st-{d['id']}", label_visibility="collapsed")
            if new_stage != d["stage"]:
                with pg.org_connection(org_id) as conn, conn.cursor() as cur:
                    cur.execute("UPDATE deals SET stage=%s WHERE org_id=%s AND id=%s",
                                (new_stage, org_id, d["id"]))
                    conn.commit()
                st.rerun()
            if d.get("broker_email"):
                c3.caption(f"via {d['broker_email']}")


def _render_terms(org_id: str) -> None:
    ts = engine.list_term_sheets(org_id)
    if not ts:
        st.info("No term sheets captured yet. Lender mail with rate/LTV/amortization "
                "is parsed automatically.")
        return
    for t in ts:
        rate = f"{float(t['rate'])*100:.2f}%" if t.get("rate") else "—"
        ltv = f"{float(t['ltv'])*100:.0f}%" if t.get("ltv") else "—"
        proceeds = f"${float(t['proceeds']):,.0f}" if t.get("proceeds") else "—"
        st.markdown(f"- 🏦 **{t.get('lender') or 'Lender'}** · rate {rate} · LTV {ltv} · "
                    f"{t.get('amort_years') or '—'}yr amort · {t.get('io_years') or 0}yr IO · "
                    f"{t.get('term_years') or '—'}yr term · proceeds {proceeds} "
                    f"· {t['received_at']:%Y-%m-%d}")


def _render_all(org_id: str) -> None:
    msgs = engine.list_messages(org_id)
    if not msgs:
        st.info("No mail ingested yet.")
        return
    for m in msgs[:50]:
        badge = {"auto_applied": "✅", "queued": "⏳", "confirmed": "✅",
                 "dismissed": "🚫", "new": "•"}.get(m["status"], "•")
        st.markdown(f"{badge} {_CAT_ICON.get(m['category'], '📧')} "
                    f"**{m['subject'] or '(no subject)'}** — {m.get('from_email')} · "
                    f"{m['category']} {float(m.get('confidence') or 0):.0%} · "
                    f"_{m['status']}_")


def _uid():
    u = st.session_state.get("user")
    return getattr(u, "id", None)
