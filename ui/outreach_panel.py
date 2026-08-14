"""Module B UI — Compliant Outreach (spec §5).

Velocity inside a gate. The dial list shows ONLY numbers that passed the Module A
compliance stamp; every dial/letter/email routes through
``core.outreach.engine.attempt_touch``, so a touch cannot be dispatched without
the §4.4 gate evaluating first and the rule trace being logged (AC-B2).

Deliberate product stance (B3): there is no button for prerecorded / AI-voice /
ringless voicemail to a non-consented cell. The gate hard-blocks it and the UI
explains why, rather than shipping the unsafe default.
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from core.compliance import ledger, rules
from core.outreach import artifacts, cadence, engine
from core.permissions import Permissions
from core.skiptrace import pipeline as sk
from data import pg


def _perms() -> Permissions | None:
    p = st.session_state.get("perms")
    return p if isinstance(p, Permissions) else None


def render_outreach(prop: dict | None = None) -> None:
    st.subheader("📞 Compliant Outreach")
    st.caption("Every touch passes the TCPA/DNC compliance gate before it is sent, "
               "and is logged with the rules it passed (Module B).")

    perms, org_id = _perms(), st.session_state.get("org_id")
    if perms is not None and not perms.can_open("outreach"):
        st.info(f"🔒 Outreach isn't included in your role (`{perms.role_preset}`).")
        return
    if not pg.is_configured() or not org_id:
        st.warning("Outreach needs the PostgreSQL store and an organization context.",
                   icon="⚠️")
        return

    can_send = perms is None or perms.can("send_outreach")
    # Same failure mode as Owner Intelligence above (2026-08-13): unguarded
    # DB work here crashed the whole Market tab, not just this panel.
    try:
        _render_sweep_queue()
    except Exception as e:
        st.error("Outreach queue unavailable — the rest of the page is "
                 f"unaffected. Detail for the log: {type(e).__name__}: {e}")
        return
    tabs = st.tabs(["☎️ Call list", "✉️ Direct mail", "🧾 Audit log", "🚫 Opt-outs"])

    with tabs[0]:
        _render_call_list(org_id, prop, can_send)
    with tabs[1]:
        _render_mail(org_id, prop, can_send)
    with tabs[2]:
        _render_audit(org_id)
    with tabs[3]:
        _render_optouts(org_id)


def _render_sweep_queue() -> None:
    """Sweep targets routed from GRANITE Alerts (spec 6.1: alert routing
    to the Outreach Engine) - the day starts with this list."""
    from core.alerts import mark_worked, outreach_queue
    from core.phase0 import find_workbench_db
    db = find_workbench_db()
    rows = outreach_queue(db) if db else []
    if not rows:
        return
    st.markdown(f"**🎯 Sweep queue — {len(rows)} routed targets** "
                "(from GRANITE Alerts; oldest first)")
    for r in rows[:25]:
        col_a, col_b = st.columns([6, 1])
        with col_a:
            st.markdown(f"**{r['headline']}**  \n"
                        f"{r['detail']} · queued {r['queued_at'][:10]} — "
                        "open it in Deal Analysis, run Resolve Contacts, "
                        "then dial from the call list below.")
        with col_b:
            if st.button("Worked", key=f"or_q_{r['alert_id']}"):
                mark_worked(db, r["alert_id"])
                st.rerun()
    st.divider()


# ---------------------------------------------------------------------------
# B1 — call list (callable numbers only)
# ---------------------------------------------------------------------------

def _render_call_list(org_id: str, prop: dict | None, can_send: bool) -> None:
    if not prop:
        st.info("Select a property to load its resolved contacts.")
        return
    pocs = sk.load_pocs(org_id, str(prop.get("property_id")))
    if not pocs:
        st.info("No resolved contacts yet — run **Resolve Contacts** on the "
                "Due Diligence tab first.")
        return

    targets = engine.callable_targets(pocs)
    blocked = [(p.get("person", {}).get("full_name"), ph)
               for p in pocs for ph in (p.get("phones") or []) if not ph.get("callable")]

    st.caption(f"**{len(targets)}** callable · **{len(blocked)}** blocked by the "
               "compliance gate · quiet hours 8:00–21:00 called-party local time")

    if not targets:
        st.warning("No callable numbers for this property. Blocked numbers are "
                   "listed below with the reason.", icon="🔒")

    for t in targets:
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"**{t['person_name']}** · {t['role']}  \n"
                        f"{t['e164']} · grade {t['grade']} · {t['line_type']}")
            # Live pre-check so the user sees the verdict BEFORE spending a dial.
            d = rules.evaluate(org_id, channel="call", subtype="manual_dial",
                               e164=t["e164"], state=(prop or {}).get("state"),
                               phone_record=t["phone_record"])
            if d.allowed:
                c1.caption("✅ gate: clear to dial now")
            else:
                c1.caption(f"⛔ gate: {d.reason}")
            if c2.button("☎️ Log manual dial", key=f"dial-{t['e164']}",
                         disabled=not can_send or not d.allowed, type="primary"):
                res = engine.attempt_touch(
                    org_id, channel="call", subtype="manual_dial", e164=t["e164"],
                    person_name=t["person_name"], property_id=t.get("property_id"),
                    state=(prop or {}).get("state"), phone_record=t["phone_record"],
                    actor_user_id=_uid(), dispatcher=lambda: "dialed")
                if res.allowed:
                    st.success(f"Logged. Rules passed: "
                               f"{sum(1 for r in res.decision.trace if r.passed)}")
                else:
                    st.error(f"Blocked: {res.reason}")
                st.rerun()
            with c2.expander("Talking points"):
                st.code(artifacts.render_talking_points(_grounding_for(prop, t["person_name"])),
                        language=None)

    if blocked:
        with st.expander(f"🔒 {len(blocked)} number(s) blocked by the compliance gate"):
            for name, ph in blocked:
                st.markdown(f"- **{name}** · {ph.get('e164')} — _{ph.get('reason')}_")

    st.caption("ℹ️ Prerecorded / AI-voice / ringless voicemail to a cell is not "
               "offered: it requires prior express **written** consent under the "
               "TCPA. The gate blocks it by design (spec §4.4 C3).")


# ---------------------------------------------------------------------------
# B2/B3 — direct mail
# ---------------------------------------------------------------------------

def _render_mail(org_id: str, prop: dict | None, can_send: bool) -> None:
    st.markdown("Generate a **deduplicated** letter batch grounded in deed chain, "
                "loan maturity and assessed value (no LLM required).")
    if not prop:
        st.info("Select a property, or use the pipeline to build a larger batch.")
        return
    pocs = sk.load_pocs(org_id, str(prop.get("property_id")))
    recips = []
    for p in pocs:
        addr = next((a.get("formatted") for a in (p.get("addresses") or [])
                     if a.get("kind") in ("mailing", "current")), None)
        if addr:
            recips.append({
                "owner_name": (p.get("person") or {}).get("full_name"),
                "mailing_address": addr,
                "property_name": prop.get("name"),
                "property_id": prop.get("property_id"),
                "units": prop.get("units"), "city": prop.get("city"),
                "last_sale_year": prop.get("last_sold_year"),
                "last_sale_amount": prop.get("last_sold_amount"),
                "portfolio_count": len(p.get("other_properties") or []) + 1,
            })
    if not recips:
        st.info("No mailing addresses on the resolved contacts yet.")
        return
    if st.button("✉️ Build letter batch", disabled=not can_send, type="primary"):
        batch = artifacts.build_letter_batch(recips, sender_phone="(757) 555-0100")
        st.success(f"{batch.count} letter(s) · {batch.duplicates_removed} duplicate(s) removed")
        st.download_button("⬇️ Download print-ready HTML",
                           artifacts.batch_to_html(batch),
                           file_name="outreach-letters.html", mime="text/html")
        st.download_button("⬇️ Download mail-merge CSV",
                           artifacts.batch_to_csv(batch),
                           file_name="outreach-letters.csv", mime="text/csv")
        st.text_area("Preview", batch.letters[0]["body"], height=300)


# ---------------------------------------------------------------------------
# AC-B2 — audit log
# ---------------------------------------------------------------------------

def _render_audit(org_id: str) -> None:
    rows = engine.export_touches(org_id, days=90)
    st.caption(f"{len(rows)} touch(es) in the last 90 days. Every attempt is "
               "recorded — including blocked ones — with the rules it evaluated.")
    if not rows:
        st.info("No outreach touches logged yet.")
        return
    st.download_button("⬇️ Export audit CSV", engine.export_touches_csv(org_id),
                       file_name="outreach-audit.csv", mime="text/csv")
    for r in rows[:40]:
        icon = "✅" if r["allowed"] else "⛔"
        st.markdown(f"{icon} **{r['channel']}/{r.get('subtype') or '-'}** · "
                    f"{r.get('person_name') or '-'} · {r.get('e164') or r.get('email') or '-'} · "
                    f"{r['ts']:%Y-%m-%d %H:%M} · _{r.get('outcome') or ''}_")
        failed = [t for t in (r.get("rule_trace") or []) if not t.get("passed")]
        if failed:
            st.caption("   blocked by: " + "; ".join(f"{t['rule']} — {t['detail']}"
                                                     for t in failed))


# ---------------------------------------------------------------------------
# C5 — opt-out capture
# ---------------------------------------------------------------------------

def _render_optouts(org_id: str) -> None:
    st.markdown("Record an opt-out. It is honored **immediately across every "
                "channel** and added to your internal do-not-call list (§4.4 C5).")
    c1, c2, c3 = st.columns([3, 2, 1])
    phone = c1.text_input("Phone (E.164, e.g. +17575550100)", key="opt-phone")
    source = c2.selectbox("How was it received?",
                          ["inbound_call", "sms_stop", "email_unsub", "letter_reply", "manual"],
                          key="opt-src")
    if c3.button("Record", type="primary"):
        if phone:
            ledger.record_revocation(org_id, e164=phone.strip(), source=source)
            st.success(f"{phone} opted out — all channels blocked from now on.")
            st.rerun()
        else:
            st.error("Enter a phone number.")
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT e164, email, scope, source, received_at FROM revocations
                        WHERE org_id=%s ORDER BY received_at DESC LIMIT 25""", (org_id,))
        rows = cur.fetchall()
    if rows:
        st.caption(f"{len(rows)} recent opt-out(s)")
        for r in rows:
            st.markdown(f"- 🚫 {r['e164'] or r['email']} · {r['scope']} · "
                        f"via {r['source']} · {r['received_at']:%Y-%m-%d}")


# ---------------------------------------------------------------------------

def _uid():
    u = st.session_state.get("user")
    return getattr(u, "id", None)


def _grounding_for(prop: dict | None, name: str | None) -> artifacts.Grounding:
    prop = prop or {}
    return artifacts.Grounding(
        owner_name=name or "Property Owner",
        property_name=prop.get("name"), property_address=prop.get("address"),
        units=prop.get("units"), city=prop.get("city"),
        last_sale_year=prop.get("last_sold_year"),
        last_sale_amount=prop.get("last_sold_amount"),
        sender_phone="(757) 555-0100")
