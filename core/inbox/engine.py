"""Module D — Inbox -> Deal Engine (spec §6.2).

Pipeline: ingest -> classify -> extract -> **confidence gate** -> write.

    high confidence  -> pipeline record created/updated automatically
    low  confidence  -> queued for one-click human confirm, NEVER silently written

That gate is the spec's explicit requirement ("Confidence-gated: below-threshold
extractions queue for one-click human confirm rather than silently writing").
Every ingest is idempotent on (org, provider, external_id), so re-polling a
mailbox cannot duplicate deals.

Contacts accumulate into `crm_contacts` and into the **same relationship graph**
Module B writes to (B5) — inbound reality calibrates outbound targeting.

Deterministic; no LLM required (Section 11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.inbox import classify as clf
from core.inbox import extract as ex
from data import pg

# §6.2 gate. Both the classifier and the extraction must clear the bar for an
# automatic write; anything less queues for a human.
AUTO_APPLY_CLASSIFY = 0.60
AUTO_APPLY_EXTRACT = 0.70


@dataclass
class IngestResult:
    message_id: str
    category: str
    classify_confidence: float
    extract_confidence: float
    status: str                 # 'auto_applied' | 'queued' | 'new'
    deal_id: str | None = None
    term_sheet_id: str | None = None
    reason: str = ""

    @property
    def auto_applied(self) -> bool:
        return self.status == "auto_applied"


def ingest_message(org_id: str, msg: dict, owner_user_id: str | None = None) -> IngestResult:
    """Ingest one message for ONE user.

    ``owner_user_id`` owns the raw message: it is written to
    ``inbox_messages.owner_user_id`` and the row is only ever readable through
    a user-scoped connection, so a colleague cannot read it (RLS enforces this
    at the database layer). The DEAL extracted from it is org-visible.
    """
    c = clf.classify(from_email=msg.get("from_email"), subject=msg.get("subject"),
                     body=msg.get("body"), attachments=msg.get("attachments"))

    if c.category == "lender":
        e = ex.extract_terms(subject=msg.get("subject"), body=msg.get("body"))
    else:
        e = ex.extract_deal(subject=msg.get("subject"), body=msg.get("body"),
                            attachments=msg.get("attachments"))

    relevant = clf.is_deal_relevant(c)
    clears = (c.confidence >= AUTO_APPLY_CLASSIFY and e.confidence >= AUTO_APPLY_EXTRACT)
    if not relevant:
        status, reason = "new", f"category '{c.category}' does not drive pipeline records"
    elif clears:
        status, reason = "auto_applied", "confidence cleared the gate"
    else:
        status = "queued"
        reason = (f"below gate (classify {c.confidence:.2f}/{AUTO_APPLY_CLASSIFY}, "
                  f"extract {e.confidence:.2f}/{AUTO_APPLY_EXTRACT}) - "
                  "queued for one-click confirm")

    message_id = _upsert_message(org_id, msg, c, e, status, owner_user_id)
    _upsert_contact(org_id, msg, c.category)

    deal_id = term_id = None
    if status == "auto_applied":
        deal_id, term_id = _apply(org_id, message_id, msg, c, e)

    return IngestResult(message_id, c.category, round(c.confidence, 3),
                        e.confidence, status, deal_id, term_id, reason)


def confirm_message(org_id: str, message_id: str, overrides: dict | None = None,
                    actor_user_id: str | None = None) -> IngestResult:
    """One-click human confirm of a queued extraction (§6.2)."""
    # User-scoped: a user can only confirm mail they own.
    with pg.user_connection(org_id, actor_user_id) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM inbox_messages WHERE id=%s", (message_id,))
        row = cur.fetchone()
    if row is None:
        raise LookupError("message not found")

    stored = dict(row["extracted"] or {})
    fields = dict(stored.get("fields") or {})
    fields.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})

    msg = {"from_email": row["from_email"], "from_name": row["from_name"],
           "subject": row["subject"], "body": row["body"],
           "received_at": row["received_at"]}
    c = clf.Classification(row["category"] or "broker", float(row["confidence"] or 0))
    e = ex.Extraction(fields=fields, confidences=stored.get("confidences") or {},
                      evidence=list(stored.get("evidence") or []) + ["human confirmed"])

    deal_id, term_id = _apply(org_id, message_id, msg, c, e)
    with pg.user_connection(org_id, actor_user_id) as conn, conn.cursor() as cur:
        cur.execute("""UPDATE inbox_messages SET status='confirmed', deal_id=%s,
                          extracted=%s WHERE id=%s""",
                    (deal_id, json.dumps(e.as_dict()), message_id))
        conn.commit()
    _audit(org_id, actor_user_id, "inbox.confirm", message_id, {"deal_id": deal_id})
    return IngestResult(message_id, c.category, c.confidence, e.confidence,
                        "confirmed", deal_id, term_id, "human confirmed")


def dismiss_message(org_id: str, message_id: str, actor_user_id: str | None = None) -> None:
    with pg.user_connection(org_id, actor_user_id) as conn, conn.cursor() as cur:
        cur.execute("UPDATE inbox_messages SET status='dismissed' WHERE id=%s",
                    (message_id,))
        conn.commit()
    _audit(org_id, actor_user_id, "inbox.dismiss", message_id, {})


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------

def _apply(org_id, message_id, msg, c, e) -> tuple[str | None, str | None]:
    if c.category == "lender":
        deal_id = _match_deal(org_id, e.fields)
        term_id = _insert_term_sheet(org_id, deal_id, message_id, e.fields)
        return deal_id, term_id
    deal_id = _upsert_deal(org_id, msg, e.fields)
    if deal_id:
        _record_edge(org_id, msg.get("from_email"), deal_id, c.category)
    # Ingest the same extract as PROPERTY DATA (owner 2026-08-11): matched
    # to a backbone parcel, one muni_records kind='assessor-email' row the
    # spine merges per-parcel (COALESCE - never overrides assessor facts).
    # Only gate-clearing mail reaches _apply, so the §6.2 confidence gate
    # covers this write too. Additive - never blocks.
    _link_property(org_id, message_id, msg, e)
    return deal_id, None


def _link_property(org_id, message_id, msg, e) -> None:
    try:
        from core.inbox import property_link
        property_link.link_message(org_id, str(message_id), msg, e,
                                   status="applied")
    except Exception:
        pass


def _upsert_message(org_id, msg, c, e, status, owner_user_id=None) -> str:
    # Raw mail is PER-USER private: written and read through a user-scoped
    # connection whose RLS policy requires both org and user context.
    with pg.user_connection(org_id, owner_user_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO inbox_messages
                 (org_id, owner_user_id, provider, external_id, from_email, from_name,
                  subject, body, received_at, attachments, category, confidence,
                  classifier, status, extracted)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, now()), %s,%s,%s,%s,%s,%s)
               ON CONFLICT (org_id, owner_user_id, provider, external_id) DO UPDATE
                 SET category=EXCLUDED.category, confidence=EXCLUDED.confidence,
                     extracted=EXCLUDED.extracted
               RETURNING id""",
            (org_id, owner_user_id, msg.get("provider", "mock"), str(msg.get("external_id")),
             msg.get("from_email"), msg.get("from_name"), msg.get("subject"),
             msg.get("body"), msg.get("received_at"),
             json.dumps(msg.get("attachments") or []), c.category,
             round(c.confidence, 3), c.classifier, status, json.dumps(e.as_dict())))
        mid = str(cur.fetchone()["id"])
        conn.commit()
        return mid


def _upsert_deal(org_id, msg, f: dict) -> str | None:
    name = f.get("name") or f.get("address")
    if not name:
        return None
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT id FROM deals WHERE org_id=%s AND lower(name)=lower(%s)
                        LIMIT 1""", (org_id, name))
        row = cur.fetchone()
        if row:
            did = str(row["id"])
            cur.execute(
                """UPDATE deals SET address=COALESCE(%s,address), city=COALESCE(%s,city),
                       state=COALESCE(%s,state), units=COALESCE(%s,units),
                       asking_price=COALESCE(%s,asking_price), cap_rate=COALESCE(%s,cap_rate),
                       broker_email=COALESCE(%s,broker_email)
                     WHERE org_id=%s AND id=%s""",
                (f.get("address"), f.get("city"), f.get("state"), f.get("units"),
                 f.get("asking_price"), f.get("cap_rate"), msg.get("from_email"),
                 org_id, did))
        else:
            cur.execute(
                """INSERT INTO deals (org_id, name, address, city, state, units,
                                      asking_price, cap_rate, stage, source, broker_email)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'lead','inbox',%s) RETURNING id""",
                (org_id, name, f.get("address"), f.get("city"), f.get("state"),
                 f.get("units"), f.get("asking_price"), f.get("cap_rate"),
                 msg.get("from_email")))
            did = str(cur.fetchone()["id"])
        conn.commit()
        return did


def _match_deal(org_id, f: dict) -> str | None:
    """Best-effort attach of a term sheet to an existing deal."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT id FROM deals WHERE org_id=%s
                        ORDER BY updated_at DESC LIMIT 1""", (org_id,))
        row = cur.fetchone()
        return str(row["id"]) if row else None


def _insert_term_sheet(org_id, deal_id, message_id, f: dict) -> str:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO term_sheets (org_id, deal_id, message_id, lender, rate, ltv,
                                        amort_years, io_years, term_years, proceeds, raw)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (message_id) WHERE message_id IS NOT NULL DO UPDATE
                 SET deal_id=EXCLUDED.deal_id, lender=EXCLUDED.lender, rate=EXCLUDED.rate,
                     ltv=EXCLUDED.ltv, amort_years=EXCLUDED.amort_years,
                     io_years=EXCLUDED.io_years, term_years=EXCLUDED.term_years,
                     proceeds=EXCLUDED.proceeds, raw=EXCLUDED.raw
               RETURNING id""",
            (org_id, deal_id, message_id, f.get("lender"), f.get("rate"), f.get("ltv"),
             f.get("amort_years"), f.get("io_years"), f.get("term_years"),
             f.get("proceeds"), json.dumps(f)))
        tid = str(cur.fetchone()["id"])
        conn.commit()
        return tid


def _upsert_contact(org_id, msg, role) -> None:
    email = (msg.get("from_email") or "").strip().lower()
    if not email:
        return
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO crm_contacts (org_id, email, name, company, role)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (org_id, email) DO UPDATE
                 SET last_seen=now(), message_count=crm_contacts.message_count+1,
                     name=COALESCE(crm_contacts.name, EXCLUDED.name)""",
            (org_id, email, msg.get("from_name"), email.split("@")[-1], role))
        conn.commit()


def _record_edge(org_id, from_email, deal_id, category) -> None:
    if not from_email:
        return
    try:
        from core.outreach.engine import record_edge
        record_edge(org_id, from_kind=category, from_id=from_email,
                    to_kind="deal", to_id=deal_id, edge="referred")
    except Exception:
        pass    # graph is additive; never block ingest on it


def _audit(org_id, actor, action, target, after) -> None:
    try:
        with pg.org_connection(org_id) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_log (org_id, actor_user_id, action, target, after)
                   VALUES (%s,%s,%s,%s,%s)""",
                (org_id, actor, action, str(target), json.dumps(after)))
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def list_queue(org_id: str, user_id: str, limit: int = 50) -> list[dict]:
    """Only the owning user's queued mail (RLS-enforced)."""
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM inbox_messages WHERE status='queued'
                        ORDER BY received_at DESC LIMIT %s""", (limit,))
        return [dict(r) for r in cur.fetchall()]


def list_messages(org_id: str, user_id: str, limit: int = 100) -> list[dict]:
    """Only the owning user's mail (RLS-enforced)."""
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM inbox_messages
                        ORDER BY received_at DESC LIMIT %s""", (limit,))
        return [dict(r) for r in cur.fetchall()]


def list_deals(org_id: str, limit: int = 100) -> list[dict]:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM deals WHERE org_id=%s
                        ORDER BY updated_at DESC LIMIT %s""", (org_id, limit))
        return [dict(r) for r in cur.fetchall()]


def list_term_sheets(org_id: str, deal_id: str | None = None) -> list[dict]:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        if deal_id:
            cur.execute("""SELECT * FROM term_sheets WHERE org_id=%s AND deal_id=%s
                            ORDER BY received_at DESC""", (org_id, deal_id))
        else:
            cur.execute("""SELECT * FROM term_sheets WHERE org_id=%s
                            ORDER BY received_at DESC LIMIT 100""", (org_id,))
        return [dict(r) for r in cur.fetchall()]
