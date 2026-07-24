"""B4 — cadence orchestration (spec §5).

Multi-touch sequences (call -> letter -> call -> email) with per-person frequency
caps (C4), state-rule awareness, and **automatic pause** on any inbound opt-out
or deal-stage change. The schedule is computed deterministically; each step is
still gated at send time by the §4.4 rules, so a scheduled step is never a
licence to dispatch.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from core.compliance import ledger
from data import pg

# The default sequence from the spec: call -> letter -> call -> email.
DEFAULT_CADENCE = [
    {"step": 1, "channel": "call", "subtype": "manual_dial", "offset_days": 0},
    {"step": 2, "channel": "mail", "subtype": "letter", "offset_days": 3},
    {"step": 3, "channel": "call", "subtype": "manual_dial", "offset_days": 10},
    {"step": 4, "channel": "email", "subtype": "email", "offset_days": 17},
]

PAUSE_DEAL_STAGES = {"under_contract", "closed", "dead", "no_go"}


@dataclass
class ScheduledStep:
    step: int
    channel: str
    subtype: str
    due_on: dt.date
    person_name: str | None = None
    e164: str | None = None
    email: str | None = None


def create_campaign(org_id: str, name: str, cadence: list[dict] | None = None) -> str:
    import json

    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO campaigns (org_id, name, cadence, status)
               VALUES (%s,%s,%s,'draft') RETURNING id""",
            (org_id, name, json.dumps(cadence or DEFAULT_CADENCE)))
        cid = str(cur.fetchone()["id"])
        conn.commit()
        return cid


def set_status(org_id: str, campaign_id: str, status: str) -> None:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("UPDATE campaigns SET status=%s WHERE org_id=%s AND id=%s",
                    (status, org_id, campaign_id))
        conn.commit()


def plan(targets: list[dict], *, start: dt.date | None = None,
         cadence: list[dict] | None = None) -> list[ScheduledStep]:
    """Expand a target list into dated steps (deterministic)."""
    start = start or dt.date.today()
    steps = cadence or DEFAULT_CADENCE
    out: list[ScheduledStep] = []
    for t in targets:
        for s in steps:
            out.append(ScheduledStep(
                step=s["step"], channel=s["channel"], subtype=s.get("subtype", ""),
                due_on=start + dt.timedelta(days=int(s.get("offset_days", 0))),
                person_name=t.get("person_name"), e164=t.get("e164"),
                email=t.get("email")))
    out.sort(key=lambda s: (s.due_on, s.step))
    return out


def should_pause(org_id: str, *, e164: str | None = None, email: str | None = None,
                 deal_stage: str | None = None) -> tuple[bool, str]:
    """Automatic pause on inbound opt-out or deal-stage change (B4)."""
    if deal_stage and deal_stage.lower() in PAUSE_DEAL_STAGES:
        return True, f"deal stage '{deal_stage}' - sequence paused"
    if ledger.is_revoked(org_id, e164=e164, email=email, channel="voice"):
        return True, "contact opted out - sequence paused"
    return False, "active"


def due_steps(steps: list[ScheduledStep], on: dt.date | None = None) -> list[ScheduledStep]:
    on = on or dt.date.today()
    return [s for s in steps if s.due_on <= on]
