"""Module A resolution pipeline — stages S1–S7 (spec §4.2), deterministic.

    S1 ENTITY ANCHOR     owner-of-record + mailing address from the property row
    S2 PORTFOLIO CHAIN   group parcels sharing owner/mailing address (free)
    S3 ENTITY RESOLUTION state registry: entity -> officers (recurse to depth 4)
    S4 PERSON SKIP TRACE vendor waterfall, cheapest first, stop on grade-A match
    S5 VALIDATION        phone/email validation + A/B/F grading
    S6 COMPLIANCE STAMP  DNC/litigator stamp; callable computed ONLY here
    S7 PERSIST & MONITOR poc_records upsert + provenance + spend ledger

Compliance invariant (AC-A3): ``callable`` is set exclusively by
:func:`_stamp_phone` from a valid, unexpired compliance stamp. No other code
path writes ``callable=True`` — the dialer/export layer reads this field and a
number without a stamp physically cannot be emitted.

Cost telemetry (AC-A4): every provider call's ``cost_usd`` is accumulated into
the run result, written per-call to ``skiptrace_spend``, and checked against the
tenant's hard monthly budget cap (FR-A5) BEFORE paid tiers execute.

No LLM anywhere (Section 11) — registry lookups, vendor APIs, validation only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass, field

from core.skiptrace import providers as prov
from data import pg

# §4.4 C1: federal DNC scrub validity — 31 days.
DNC_STAMP_DAYS = 31
# FR-A6 freshness horizons (enforced on use by callers; stored on the record).
PHONE_FRESH_DAYS = 90
EMAIL_FRESH_DAYS = 180
# Default hard monthly cap (FR-A5) when the org hasn't set one (USD).
DEFAULT_MONTHLY_BUDGET = 100.0

_ENTITY_RE = re.compile(
    r"\b(llc|l\.l\.c\.|lp|l\.p\.|llp|inc\.?|corp\.?|company|trust|partners|holdings)\b",
    re.IGNORECASE)


class BudgetExceeded(RuntimeError):
    """Raised before any paid call when the run would blow the monthly cap."""


@dataclass
class ResolveResult:
    property_id: str
    portfolio_id: str | None
    pocs: list[dict] = field(default_factory=list)   # §4.5 poc_record dicts
    total_cost_usd: float = 0.0
    spend_lines: list[dict] = field(default_factory=list)
    stages_run: list[str] = field(default_factory=list)

    @property
    def owner_resolved(self) -> bool:
        return any(p["role"] in ("owner", "principal") and p["phones"] for p in self.pocs)

    @property
    def grade_a_phones(self) -> int:
        return sum(1 for p in self.pocs for ph in p["phones"] if ph["grade"] == "A")


def looks_like_entity(name: str) -> bool:
    return bool(name and _ENTITY_RE.search(name))


def estimate_cost(prop: dict) -> tuple[float, float]:
    """(typical, worst-case) cost preview for one property (FR-A5), from the
    §4.3 unit-economics table."""
    entity = looks_like_entity(prop.get("owner") or "")
    typical = 0.02 + 0.12 + 4 * 0.035 + (0.0 if entity else 0.0)   # ~$0.28
    worst = 0.02 + 0.12 + 0.25 + 6 * 0.035 + (2.00 if entity else 0.0)
    return round(typical, 2), round(worst, 2)


# ---------------------------------------------------------------------------
# Budget (FR-A5) — hard monthly cap per tenant, checked before paid tiers
# ---------------------------------------------------------------------------

def month_to_date_spend(org_id: str) -> float:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(cost_usd), 0) AS s FROM skiptrace_spend
                WHERE org_id = %s AND ts >= date_trunc('month', now())""", (org_id,))
        return float(cur.fetchone()["s"])


def monthly_budget(org_id: str) -> float:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT buy_box_config FROM organizations WHERE id = %s", (org_id,))
        row = cur.fetchone()
        cfg = row["buy_box_config"] if row else {}
        try:
            return float(cfg.get("skiptrace_budget_usd", DEFAULT_MONTHLY_BUDGET))
        except (TypeError, ValueError, AttributeError):
            return DEFAULT_MONTHLY_BUDGET


def check_budget(org_id: str, projected_cost: float) -> None:
    cap = monthly_budget(org_id)
    mtd = month_to_date_spend(org_id)
    if mtd + projected_cost > cap:
        raise BudgetExceeded(
            f"Monthly skip-trace budget cap would be exceeded: "
            f"${mtd:.2f} spent + ~${projected_cost:.2f} projected > ${cap:.2f} cap. "
            f"Raise the cap in org settings (buy_box_config.skiptrace_budget_usd).")


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def resolve_contacts(org_id: str, prop: dict, *, registry=None,
                     persist: bool = True) -> ResolveResult:
    """One-click 'Resolve Contacts' for a property (FR-A1). Idempotent: re-runs
    replace the property's previous POC set inside one transaction."""
    reg = registry or prov.get_registry()
    res = ResolveResult(property_id=str(prop.get("property_id")), portfolio_id=None)
    now = dt.datetime.now(dt.timezone.utc)

    # --- S1 ENTITY ANCHOR --------------------------------------------------
    owner_name = (prop.get("owner") or "").strip()
    state = (prop.get("state") or "VA").strip() or "VA"
    mailing = (prop.get("owner_address") or "").strip() or None
    res.stages_run.append("S1")
    if not owner_name:
        return res  # nothing to anchor on; empty result (caller shows guidance)

    # --- S2 PORTFOLIO CHAIN (free) -----------------------------------------
    res.portfolio_id = f"PF-{abs(hash((owner_name.lower(), mailing or ''))) % 10**10:010d}"
    other_props = _portfolio_siblings(owner_name, res.property_id)
    res.stages_run.append("S2")

    # Budget gate before ANY paid call (FR-A5). Only for persisted (real) runs;
    # a dry run (persist=False) is a preview and never touches the spend ledger.
    if persist:
        _typ, worst = estimate_cost(prop)
        check_budget(org_id, worst)

    # --- S3 ENTITY RESOLUTION (recurse to depth 4, §4.2/FR-A3) --------------
    entity_chain: list[dict] = []
    person_name = owner_name
    if looks_like_entity(owner_name):
        current = owner_name
        for _hop in range(4):
            sos = reg.sos.resolve_entity(current, state)
            if sos is None:
                break
            res.total_cost_usd += sos.cost_usd
            res.spend_lines.append(_spend(org_id, sos.vendor, sos.query_id, sos.cost_usd))
            entity_chain.append({
                "entity_name": sos.entity_name, "jurisdiction": sos.jurisdiction,
                "filing_id": sos.filing_id, "officers": sos.officers,
                "registered_agent": sos.registered_agent,
                "confidence": round(sos.confidence, 2),
            })
            nxt = sos.officers[0] if sos.officers else None
            if not nxt:
                break
            person_name = nxt
            if not looks_like_entity(nxt):
                break               # reached a human
            current = nxt           # LLC owned by LLC — recurse
    res.stages_run.append("S3")

    # --- S4 PERSON SKIP TRACE (waterfall, stop on grade-A, §4.2) ------------
    validated_phones: list[dict] = []
    validated_emails: list[dict] = []
    candidate = None
    for tier in reg.trace_waterfall:
        cand = tier.trace_person(person_name, mailing, state)
        if cand is None:
            continue
        res.total_cost_usd += cand.cost_usd
        res.spend_lines.append(_spend(org_id, cand.vendor, cand.query_id, cand.cost_usd))
        candidate = cand

        # --- S5 VALIDATION & GRADING for this tier's contacts ---------------
        for e164 in cand.phones:
            v = reg.validation.validate_phone(e164, person_name)
            res.total_cost_usd += v.cost_usd
            res.spend_lines.append(_spend(org_id, v.vendor, v.query_id, v.cost_usd))
            validated_phones.append(_stamp_phone(v, now))
        for addr in cand.emails:
            ev = reg.validation.validate_email(addr)
            res.total_cost_usd += ev.cost_usd
            res.spend_lines.append(_spend(org_id, ev.vendor, ev.query_id, ev.cost_usd))
            validated_emails.append({
                "address": addr,
                "deliverability": round(ev.deliverability, 2),
                "grade": "A" if ev.deliverability >= 0.8 else ("B" if ev.deliverability >= 0.6 else "F"),
            })
        if any(p["grade"] == "A" for p in validated_phones):
            break                   # grade-A match — stop the waterfall (§4.2)
    res.stages_run += ["S4", "S5", "S6"]

    # Safeguard: when the owner is an LLC (entity_chain non-empty) but the
    # pierce came from a MOCK SOS, the principal name is fabricated — so the
    # phones, real as they are, belong to a guessed person. Never present
    # them as callable. This catches the common half-live state: BatchData
    # live, SOS still on mock. Individual owners (no entity_chain) are
    # unaffected — their name is the deed's, not a guess.
    sos_status = str((getattr(reg, "status", None) or {}).get("sos", ""))
    if entity_chain and "mock" in sos_status.lower():
        for p in validated_phones:
            p["callable"] = False
            p["reason"] = ("principal unverified — LLC piercing is on mock "
                           "SOS; enable a live SOS (Cobalt/VA SCC) before "
                           "dialing")

    # --- assemble poc_records (§4.5 contract) -------------------------------
    provenance = [
        {"field": "phones/emails", "vendor": s["vendor"], "query_id": s["query_id"],
         "cost_usd": s["cost_usd"], "retrieved_at": now.isoformat()}
        for s in res.spend_lines
    ]
    role = "principal" if entity_chain else "owner"
    person = {
        "full_name": person_name,
        "age_band": candidate.age_band if candidate else None,
        "deceased": bool(candidate and candidate.deceased),
    }
    poc = {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "property_id": res.property_id,
        "portfolio_id": res.portfolio_id,
        "role": role,
        "person": person,
        "entity_chain": entity_chain,
        "phones": validated_phones,
        "emails": validated_emails,
        "addresses": (candidate.addresses if candidate else
                      ([{"formatted": mailing, "kind": "mailing"}] if mailing else [])),
        "relatives": (candidate.relatives if candidate else []),
        "other_properties": other_props,
        "provenance": provenance,
        "compliance": {
            "stamped_at": now.isoformat(),
            "expires_at": (now + dt.timedelta(days=DNC_STAMP_DAYS)).isoformat(),
            "revocations": [],
        },
    }
    res.pocs.append(poc)

    # PM of record from the property row (no trace cost — reference data).
    pm = (prop.get("management_company") or "").strip()
    if pm and pm.lower() not in ("self-managed", "self managed"):
        res.pocs.append({
            "id": str(uuid.uuid4()), "org_id": org_id,
            "property_id": res.property_id, "portfolio_id": res.portfolio_id,
            "role": "pm", "person": {"full_name": pm, "age_band": None, "deceased": False},
            "entity_chain": [], "phones": [], "emails": [], "addresses": [],
            "relatives": [], "other_properties": [],
            "provenance": [{"field": "person.full_name", "vendor": "property-record",
                            "query_id": "-", "cost_usd": 0.0,
                            "retrieved_at": now.isoformat()}],
            "compliance": {"stamped_at": None, "expires_at": None, "revocations": []},
        })

    res.total_cost_usd = round(res.total_cost_usd, 4)

    # --- S7 PERSIST & MONITOR ----------------------------------------------
    if persist:
        _persist(org_id, res)
        res.stages_run.append("S7")
    return res


# ---------------------------------------------------------------------------
# S6 — the ONLY place `callable` is computed (AC-A3)
# ---------------------------------------------------------------------------

def _stamp_phone(v: prov.PhoneValidation, now: dt.datetime) -> dict:
    """Grade a validated phone and stamp its compliance state (§4.2 S5–S6).

    Grade A = active + line type known + name-match >= 0.8 + no litigator flag
    (§4.2 S5). ``callable`` requires: grade A or B, a valid unexpired DNC stamp,
    not on the federal DNC, and no litigator flag (§4.4 C1/C2). The reason a
    number is not callable is stored for the UI's red lock (FR-A4).
    """
    if v.active and v.line_type != "unknown" and v.name_match >= 0.8 and not v.litigator:
        grade = "A"
    elif v.active and not v.litigator:
        grade = "B"
    else:
        grade = "F"

    reasons = []
    if v.dnc_federal:
        reasons.append("federal DNC")
    if v.dnc_states:
        reasons.append("state DNC: " + ",".join(v.dnc_states))
    if v.litigator:
        reasons.append("litigator flag")
    if grade == "F":
        reasons.append("failed validation")

    stamp = {
        "federal": v.dnc_federal,
        "state": v.dnc_states,
        "scrubbed_at": now.isoformat(),
        "expires_at": (now + dt.timedelta(days=DNC_STAMP_DAYS)).isoformat(),
    }
    is_callable = grade in ("A", "B") and not v.dnc_federal and not v.litigator
    return {
        "e164": v.e164,
        "line_type": v.line_type,
        "grade": grade,
        "name_match": round(v.name_match, 2),
        "litigator": v.litigator,
        "dnc": stamp,
        "callable": is_callable,
        "reason": "; ".join(reasons) if reasons else "ok",
        "retrieved_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _spend(org_id: str, vendor: str, query_id: str, cost: float) -> dict:
    return {"org_id": org_id, "vendor": vendor, "query_id": query_id,
            "cost_usd": round(cost, 4)}


def _portfolio_siblings(owner_name: str, exclude_property_id: str) -> list[str]:
    """S2: other parcels with the same owner-of-record (free, from local data)."""
    try:
        from data.db import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT property_id FROM properties WHERE owner = ? AND property_id != ?",
                (owner_name, exclude_property_id)).fetchall()
            return [r["property_id"] for r in rows]
    except Exception:
        return []   # property store unavailable (e.g. unit tests) — non-fatal


def _persist(org_id: str, res: ResolveResult) -> None:
    """S7: replace this property's POC set + append spend, one transaction."""
    with pg.org_connection(org_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM poc_records WHERE org_id=%s AND property_id=%s",
                (org_id, res.property_id))
            for p in res.pocs:
                cur.execute(
                    """INSERT INTO poc_records
                         (id, org_id, property_id, portfolio_id, role, person,
                          entity_chain, phones, emails, addresses, relatives,
                          other_properties, provenance, compliance)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (p["id"], org_id, p["property_id"], p["portfolio_id"], p["role"],
                     json.dumps(p["person"]), json.dumps(p["entity_chain"]),
                     json.dumps(p["phones"]), json.dumps(p["emails"]),
                     json.dumps(p["addresses"]), json.dumps(p["relatives"]),
                     json.dumps(p["other_properties"]), json.dumps(p["provenance"]),
                     json.dumps(p["compliance"])))
            for s in res.spend_lines:
                cur.execute(
                    """INSERT INTO skiptrace_spend (org_id, vendor, query_id, cost_usd)
                       VALUES (%s,%s,%s,%s)""",
                    (s["org_id"], s["vendor"], s["query_id"], s["cost_usd"]))
        conn.commit()


def load_pocs(org_id: str, property_id: str) -> list[dict]:
    """Read the stored POC set for a property (RLS-scoped)."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM poc_records
                WHERE org_id=%s AND property_id=%s
                ORDER BY (role='owner') DESC, (role='principal') DESC, role""",
            (org_id, property_id))
        return [dict(r) for r in cur.fetchall()]
