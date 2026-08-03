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
            st.success(f"Resolved {len(res.pocs)} contact(s) · stages "
                       f"{'→'.join(res.stages_run)} · run cost ${res.total_cost_usd:.2f}")
        except pipeline.BudgetExceeded as e:
            st.error(str(e))

    pocs = pipeline.load_pocs(org_id, prop_id)
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
                  "pm": "Property Manager", "lender": "Lender", "agent": "Agent",
                  "prior_owner": "Prior owner"}.get(role, role)

    with st.container(border=True):
        head = f"**{name}** · {role_label}"
        if person.get("deceased"):
            head += " · ⚠️ deceased flag"
        st.markdown(head)

        chain = poc.get("entity_chain") or []
        if chain:
            path = " → ".join(f"{c['entity_name']} ({c['jurisdiction']} {c['filing_id']}, "
                              f"conf {c['confidence']})" for c in chain)
            st.caption(f"Entity chain: {path} → **{name}**")

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
