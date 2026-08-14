"""Owner Intelligence panel — Module A surface (spec §4, FR-A1/A4/A7).

One-click "Resolve Contacts" on a property: LLC piercing -> principal, phone/
email waterfall, A/B/F grading, and the compliance gate rendered as red locks.
Gated by the §10.4 `skip_trace` module grant and `run_skiptrace` action.

Currently backed by deterministic mock vendors (no spend, repeatable). Wire real
BatchData/Trestle/VA-SCC keys later via ER_SKIPTRACE_PROVIDERS=live — no UI
change. Requires Postgres (poc_records store); shows a notice otherwise.
"""

from __future__ import annotations

import streamlit as st

from core.permissions import Permissions
from core.skiptrace import pipeline
from data import pg

_GRADE_BADGE = {
    "A": ("#15803d", "#dcfce7", "A"),
    "B": ("#b45309", "#fef3c7", "B"),
    "F": ("#b91c1c", "#fee2e2", "F"),
}


def render_owner_intel(prop: dict | None) -> None:
    if not prop:
        st.info("Select a property to resolve its owner contacts.")
        return

    st.subheader("🔎 Owner Intelligence")
    st.caption("Pierce the LLC to the true decision-maker and resolve "
               "compliance-scrubbed phones, emails, and mailing addresses "
               "(Module A). Contact data is produced with AI off.")

    perms = st.session_state.get("perms")
    org_id = st.session_state.get("org_id")

    if isinstance(perms, Permissions) and not perms.can_open("skip_trace"):
        st.info(f"🔒 Owner Intelligence isn't included in your role "
                f"(`{perms.role_preset}`).")
        return
    if not pg.is_configured() or not org_id:
        st.warning("Owner Intelligence needs the PostgreSQL POC store and an "
                   "organization context (run on the pilot server with a "
                   "database configured).", icon="⚠️")
        return

    prop_id = str(prop.get("property_id"))
    owner = (prop.get("owner") or "").strip()
    if not owner:
        st.info("This property has no owner-of-record to anchor on.")
        return

    import os
    _demo = os.environ.get("ER_SKIPTRACE_PROVIDERS", "mock").lower() != "live"
    if _demo:
        st.warning(
            "**Demo mode** — no skip-trace vendor keys are configured, so "
            "any names, phones and emails below are realistic placeholders, "
            "not real contact data. Set `ER_SKIPTRACE_PROVIDERS=live` plus "
            "vendor keys for real traces.", icon="🧪")

    typ, worst = pipeline.estimate_cost(prop)
    can_run = not isinstance(perms, Permissions) or perms.can("run_skiptrace")

    c1, c2 = st.columns([2, 3])
    with c1:
        st.metric("Est. cost / owner", f"${typ:.2f}", help=f"Worst case ${worst:.2f} "
                  "(multi-layer LLC / expansion market)")
    with c2:
        st.caption(f"Owner of record: **{owner}**"
                   + (f"  ·  entity — LLC piercing will run" if pipeline.looks_like_entity(owner)
                      else "  ·  individual"))
        from core.skiptrace import providers as _prov
        stt = _prov.get_registry().status
        live_any = any("live" in v for v in stt.values())
        prov_line = " · ".join(f"{k}: **{v}**" for k, v in stt.items())
        st.caption(("🟢 " if live_any else "🧪 ") + f"Providers — {prov_line}")
        if not live_any:
            st.caption("Mock providers = deterministic, **$0 real spend**. Add vendor "
                       "keys + `ER_SKIPTRACE_PROVIDERS=live` for real data.")
        st.caption(f"Month-to-date org spend: ${pipeline.month_to_date_spend(org_id):.2f}")

    disabled = not can_run
    if st.button(f"🔎 Resolve Contacts (~${typ:.2f})", type="primary", disabled=disabled,
                 help=None if can_run else "Your role can't run skip trace (run_skiptrace)."):
        try:
            with st.spinner("Piercing entity, tracing, validating, compliance-scrubbing…"):
                res = pipeline.resolve_contacts(org_id, prop)
            # Honest banner: "Resolved 2 contact(s)" with zero phones/emails
            # is a failure wearing a green box (owner 2026-08-11). Count what
            # was actually delivered, and warn when it's nothing.
            got = (f"{res.phones_found} phone(s) · {res.emails_found} "
                   f"email(s) · {len(res.pocs)} contact card(s) · run cost "
                   f"${res.total_cost_usd:.2f}")
            bc = sum(1 for p in res.pocs if p.get("business_contact"))
            if res.phones_found or res.emails_found or bc:
                st.success(f"Resolved: {got}"
                           + (f" · {bc} business contact(s)" if bc else ""))
            else:
                st.warning(f"No contact data resolved — {got}. See the "
                           "provider trace below for what each vendor "
                           "answered.", icon="⚠️")
            ptrace = getattr(res, "provider_trace", None) or []
            if ptrace:
                bad = sum(1 for t in ptrace if t.get("outcome") == "error")
                with st.expander(
                        f"Provider trace — {len(ptrace)} call(s)"
                        + (f", {bad} error(s)" if bad else ""),
                        expanded=bool(bad)):
                    icon_by = {"hit": "✅", "miss": "▫️", "error": "❌",
                               "skip": "⏭️"}
                    for t in ptrace:
                        st.markdown(
                            f"{icon_by.get(t['outcome'], '·')} "
                            f"`{t['vendor']}` {t['op']} — **{t['outcome']}**"
                            + (f": {t['detail']}" if t.get("detail") else ""))
        except pipeline.BudgetExceeded as e:
            st.error(str(e))
        except Exception as e:
            # A resolve/persist failure must degrade to a message, never a
            # red traceback over the whole page (2026-08-12: a poc_records
            # role CheckViolation took down the market page pre-demo).
            st.error("Contact resolution hit a storage error — the rest of "
                     "the page is unaffected. Detail for the log: "
                     f"{type(e).__name__}: {e}")

    try:
        pocs = pipeline.load_pocs(org_id, prop_id)
    except Exception as e:
        st.error("Could not read saved contacts — the rest of the page is "
                 f"unaffected. Detail for the log: {type(e).__name__}: {e}")
        return
    if not pocs:
        st.caption("No contacts resolved yet — click **Resolve Contacts**.")
        return

    for poc in pocs:
        _render_poc(poc)


def _render_poc(poc: dict) -> None:
    role = poc["role"]
    person = poc.get("person") or {}
    name = person.get("full_name", "—")
    role_label = {"owner": "Owner", "principal": "Principal (LLC-pierced)",
                  "entity_unpierced": "Entity · no individual on record",
                  "pm": "Property Manager", "lender": "Lender", "agent": "Agent",
                  "prior_owner": "Prior owner"}.get(role, role)

    with st.container(border=True):
        head = f"**{name}** · {role_label}"
        if person.get("deceased"):
            head += " · ⚠️ deceased flag"
        st.markdown(head)

        chain = poc.get("entity_chain") or []
        if chain and role != "entity_unpierced":
            path = " → ".join(f"{c['entity_name']} ({c['jurisdiction']} {c['filing_id']}, "
                              f"conf {c['confidence']})" for c in chain)
            st.caption(f"Entity chain: {path} → **{name}**")

        # When the LLC could not be pierced to a human, say so plainly and
        # route the user to the reachable contact instead of showing empty
        # phone/email lines that read like a failed lookup.
        if role == "entity_unpierced":
            st.markdown(f"ℹ️ _{person.get('unpierced_note', 'no individual on the state record')}_")
            _render_business_contact(poc.get("business_contact"), poc.get("business_contact_note"))
            prov = poc.get("provenance") or []
            if prov:
                vendors = ", ".join(sorted({p['vendor'] for p in prov}))
                total = sum(float(p.get('cost_usd') or 0) for p in prov)
                st.caption(f"Provenance: {vendors} · resolved cost ${total:.2f}")
            return

        if person.get("age_band"):
            st.caption(f"age {person['age_band']}")

        phones = poc.get("phones") or []
        if phones:
            for ph in phones:
                fg, bg, letter = _GRADE_BADGE.get(ph["grade"], ("#555", "#eee", "?"))
                badge = (f'<span style="background:{bg};color:{fg};font-weight:700;'
                         f'padding:1px 7px;border-radius:6px;font-size:11px">{letter}</span>')
                if ph["callable"]:
                    line = f'{badge}  📞 <b>{ph["e164"]}</b> · {ph["line_type"]} · callable'
                else:
                    line = (f'{badge}  🔒 <span style="color:#b91c1c">{ph["e164"]}</span> · '
                            f'{ph["line_type"]} · <i>blocked: {ph["reason"]}</i>')
                st.markdown(line, unsafe_allow_html=True)
        else:
            st.markdown("📞 _no phone resolved_")

        emails = poc.get("emails") or []
        if emails:
            for em in emails:
                st.markdown(f'✉️ <b>{em["address"]}</b> · grade {em["grade"]} '
                            f'(deliverability {em["deliverability"]})',
                            unsafe_allow_html=True)
        else:
            st.markdown("✉️ _no email resolved_")

        # Mailing / known addresses (§4.5 poc_record.addresses) — resolved by
        # the trace but never shown before; half of "see more information".
        for a in (poc.get("addresses") or []):
            formatted = a.get("formatted") if isinstance(a, dict) else a
            kind = a.get("kind", "mailing") if isinstance(a, dict) else "mailing"
            if formatted:
                st.markdown(f'🏠 {formatted} · _{kind}_')

        relatives = poc.get("relatives") or []
        if relatives:
            names = ", ".join(
                ((r.get("name") if isinstance(r, dict) else str(r))
                 + (f" ({r['relation']})" if isinstance(r, dict) and r.get("relation") else ""))
                for r in relatives if (r.get("name") if isinstance(r, dict) else r))
            if names:
                st.caption(f"Relatives / associates: {names}")

        _render_business_contact(poc.get("business_contact"), poc.get("business_contact_note"))

        others = poc.get("other_properties") or []
        if others:
            st.caption(f"Portfolio: also owns {len(others)} other parcel(s) "
                       f"(shared owner-of-record).")

        prov = poc.get("provenance") or []
        if prov:
            vendors = ", ".join(sorted({p["vendor"] for p in prov}))
            total = sum(float(p.get("cost_usd") or 0) for p in prov)
            comp = poc.get("compliance") or {}
            stamp = comp.get("expires_at")
            st.caption(f"Provenance: {vendors} · resolved cost ${total:.2f}"
                       + (f" · DNC stamp valid to {stamp[:10]}" if stamp else ""))


def _render_business_contact(bc: dict | None, note: str | None = None) -> None:
    """The firm's directory contact — for a management company or an
    institutional owner with no individual on record. A business main line,
    so it's a manual call (not compliance-stamped for the dialer)."""
    if not bc:
        if note:                       # live provider ran, found nothing
            st.markdown(f"🏢 _{note}_")
        return
    who = bc.get("contact_name")
    title = bc.get("contact_title")
    header = "🏢 **Business contact**"
    if who:
        header += f" — {who}" + (f", {title}" if title else "")
    st.markdown(header)
    if bc.get("phone"):
        st.markdown(f'&nbsp;&nbsp;📞 <b>{bc["phone"]}</b> · <i>main line — manual call</i>',
                    unsafe_allow_html=True)
    if bc.get("email"):
        st.markdown(f'&nbsp;&nbsp;✉️ {bc["email"]}', unsafe_allow_html=True)
    if bc.get("website"):
        st.markdown(f'&nbsp;&nbsp;🌐 {bc["website"]}')
