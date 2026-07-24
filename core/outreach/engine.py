"""Outreach dispatch — the single chokepoint for every outbound touch (§5).

Design invariant (AC-B2 + AC-A3): a touch can only be dispatched through
:func:`attempt_touch`, which
  1. evaluates the §4.4 compliance gate,
  2. writes an ``outreach_touches`` row with the full rule trace — for BOTH
     allowed and blocked attempts, so the audit export shows what was refused,
  3. dispatches only when the gate allowed it,
  4. records the relationship-graph edge (B5).

There is no code path that dispatches without logging: the dispatcher is invoked
from inside this function only.

Deterministic; no LLM (Section 11). AI-personalized artifacts (B2) are optional
and always have a template fallback — see core/outreach/artifacts.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from core.compliance import rules
from data import pg


@dataclass
class TouchResult:
    allowed: bool
    touch_id: str | None
    decision: rules.Decision
    outcome: str | None = None

    @property
    def reason(self) -> str:
        return self.decision.reason


def attempt_touch(org_id: str, *, channel: str, subtype: str = "manual_dial",
                  e164: str | None = None, email: str | None = None,
                  person_name: str | None = None, property_id: str | None = None,
                  poc_id: str | None = None, state: str | None = None,
                  phone_record: dict | None = None, actor_user_id: str | None = None,
                  campaign_id: str | None = None, purpose: str = "acquisition",
                  dispatcher: Callable[[], str] | None = None) -> TouchResult:
    """Evaluate the gate, log the attempt with its trace, dispatch only if allowed.

    ``dispatcher`` performs the real-world action (place the call, render the
    letter, send the email) and returns an outcome string. It is invoked ONLY
    after the gate passes. Omit it to run in "check + log" mode.
    """
    decision = rules.evaluate(
        org_id, channel=channel, subtype=subtype, e164=e164, email=email,
        state=state, phone_record=phone_record, purpose=purpose)

    outcome: str | None = None
    if decision.allowed and dispatcher is not None:
        outcome = dispatcher()
    elif not decision.allowed:
        outcome = "blocked"

    touch_id = _log_touch(
        org_id, property_id=property_id, poc_id=poc_id, person_name=person_name,
        channel=channel, subtype=subtype, e164=e164, email=email,
        allowed=decision.allowed, rule_trace=decision.trace_json(), outcome=outcome,
        campaign_id=campaign_id, actor_user_id=actor_user_id)

    if decision.allowed and person_name:
        record_edge(org_id, from_kind="user", from_id=str(actor_user_id or "system"),
                    to_kind="poc", to_id=person_name, edge="contacted")

    return TouchResult(decision.allowed, touch_id, decision, outcome)


def _log_touch(org_id: str, **kw) -> str:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO outreach_touches
                 (org_id, property_id, poc_id, person_name, channel, subtype,
                  e164, email, allowed, rule_trace, outcome, campaign_id, actor_user_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (org_id, kw.get("property_id"), kw.get("poc_id"), kw.get("person_name"),
             kw["channel"], kw.get("subtype"), kw.get("e164"), kw.get("email"),
             kw["allowed"], json.dumps(kw.get("rule_trace") or []), kw.get("outcome"),
             kw.get("campaign_id"), kw.get("actor_user_id")))
        tid = str(cur.fetchone()["id"])
        conn.commit()
        return tid


# ---------------------------------------------------------------------------
# B1 — dial list: grade-A/B, callable numbers only
# ---------------------------------------------------------------------------

def callable_targets(pocs: list[dict]) -> list[dict]:
    """Flatten resolved POCs into a dial list of ONLY callable numbers (B1).

    A number that failed the Module A compliance stamp never enters the list, so
    the dialer cannot surface it (AC-A3).
    """
    out = []
    for poc in pocs:
        person = (poc.get("person") or {})
        for ph in (poc.get("phones") or []):
            if not ph.get("callable"):
                continue
            out.append({
                "poc_id": str(poc.get("id")) if poc.get("id") else None,
                "property_id": poc.get("property_id"),
                "person_name": person.get("full_name"),
                "role": poc.get("role"),
                "e164": ph.get("e164"),
                "grade": ph.get("grade"),
                "line_type": ph.get("line_type"),
                "phone_record": ph,
            })
    out.sort(key=lambda r: (r["grade"] != "A", r["person_name"] or ""))
    return out


# ---------------------------------------------------------------------------
# B5 — relationship graph
# ---------------------------------------------------------------------------

def record_edge(org_id: str, *, from_kind: str, from_id: str, to_kind: str,
                to_id: str, edge: str, weight: float = 1.0) -> None:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO relationship_edges (org_id, from_kind, from_id, to_kind,
                                               to_id, edge, weight)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (org_id, from_kind, from_id, to_kind, to_id, edge, weight))
        conn.commit()


# ---------------------------------------------------------------------------
# AC-B2 — audit export
# ---------------------------------------------------------------------------

def export_touches(org_id: str, days: int = 90) -> list[dict]:
    """Audit-exportable touch log: channel, timestamp, rule trace, outcome."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ts, channel, subtype, person_name, e164, email, allowed,
                      outcome, rule_trace
                 FROM outreach_touches
                WHERE org_id=%s AND ts >= now() - make_interval(days => %s)
                ORDER BY ts DESC""", (org_id, days))
        return [dict(r) for r in cur.fetchall()]


def export_touches_csv(org_id: str, days: int = 90) -> str:
    import csv
    import io

    rows = export_touches(org_id, days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "channel", "subtype", "person", "phone", "email",
                "allowed", "outcome", "rules_passed", "rules_failed"])
    for r in rows:
        trace = r.get("rule_trace") or []
        passed = [t["rule"] for t in trace if t.get("passed")]
        failed = [f"{t['rule']}({t.get('detail','')})" for t in trace if not t.get("passed")]
        w.writerow([r["ts"], r["channel"], r.get("subtype"), r.get("person_name"),
                    r.get("e164"), r.get("email"), r["allowed"], r.get("outcome"),
                    "|".join(passed), "|".join(failed)])
    return buf.getvalue()
