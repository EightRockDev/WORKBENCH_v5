"""Inbox -> property-details bridge (owner ask 2026-08-11: "you should be
reading emails from O365 ... and populating property details from them").

Module D extracted deal facts from mail (address, units, asking price, cap
rate, broker) but they stopped at the Pg ``deals`` pipeline - the property
detail page never saw them. This module closes that gap:

    extracted fields -> match against the property backbone (properties_8r,
    by normalized address + city) -> one org-visible ``property_email_intel``
    row per source message -> rendered as "Inbox Intel" on the property
    detail page, with provenance (who sent it, when, confidence).

Privacy model unchanged (§6.2): raw mail stays per-user private; what lands
here is the same org-visible extract that already flows into ``deals``, and
ONLY from messages that cleared the confidence gate (auto-applied) or were
human-confirmed. Queued/below-gate mail never surfaces org-wide.

Unmatched intel is kept too (keyed by normalized address) - the moment that
address gets a property card, its history is already waiting.

Degradation: no Postgres -> every call is a silent no-op; the bridge must
never break ingest or the page it feeds.
"""

from __future__ import annotations

import json
from typing import Any

from data import pg

# Facts worth showing on a property card, in display order.
INTEL_FIELDS = ("units", "asking_price", "cap_rate", "name", "address",
                "city", "state")

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS property_email_intel (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL,
    property_key  text NOT NULL,
    matched       boolean NOT NULL DEFAULT false,
    message_id    text NOT NULL,
    from_email    text,
    from_name     text,
    subject       text,
    received_at   timestamptz,
    status        text,
    fields        jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence    real,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, message_id)
)"""


def norm_key(address: str | None, city: str | None = None) -> str:
    """Stable matching key for an address (reuses the user-property
    normalizer so both sides agree on what 'same address' means)."""
    from core.user_properties import norm_addr
    base = norm_addr(address or "")
    return f"{base}|{(city or '').strip().lower()}" if base else ""


def match_property(fields: dict[str, Any]) -> dict[str, Any] | None:
    """The backbone property this extraction is about, or None.

    Matches on normalized address; a city, when extracted, must agree
    (many localities share street names - '100 Main St' alone is not an
    identity)."""
    address = (fields.get("address") or "").strip()
    if not address:
        return None
    city = (fields.get("city") or "").strip()
    try:
        from core.user_properties import norm_addr
        from data import db
        want = norm_addr(address)
        if not want:
            return None
        # db.DB_PATH read at call time (the default arg binds at import).
        with db.get_connection(db.DB_PATH) as conn:
            sql = ("SELECT property_id, address, city, units FROM "
                   "properties_8r WHERE address IS NOT NULL")
            params: tuple = ()
            if city:
                sql += " AND lower(city) = lower(?)"
                params = (city,)
            for row in conn.execute(sql, params):
                if norm_addr(row["address"] or "") == want:
                    return dict(row)
    except Exception:
        return None
    return None


def link_message(org_id: str, message_id: str, msg: dict,
                 extraction, status: str) -> str | None:
    """Match + record intel for one gate-clearing message. Returns the
    property key written, or None. Never raises into ingest."""
    try:
        fields = dict(getattr(extraction, "fields", None) or {})
        if not any(fields.get(k) for k in ("address", "name", "units",
                                           "asking_price")):
            return None
        prop = match_property(fields)
        if prop is not None:
            key, matched = str(prop["property_id"]), True
        else:
            key = norm_key(fields.get("address"), fields.get("city"))
            matched = False
            if not key:
                return None
        _write_intel(org_id, key, matched, message_id, msg, fields,
                     float(getattr(extraction, "confidence", 0.0) or 0.0),
                     status)
        return key
    except Exception:
        return None      # additive enrichment - never block ingest


def _write_intel(org_id, key, matched, message_id, msg, fields, confidence,
                 status) -> None:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        # Defensive create: keeps the bridge working on a host whose
        # pilot_schema migration hasn't run yet (RLS arrives with the
        # migration; org_connection scoping applies either way).
        cur.execute(_TABLE_DDL)
        cur.execute(
            """INSERT INTO property_email_intel
                 (org_id, property_key, matched, message_id, from_email,
                  from_name, subject, received_at, status, fields, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, now()), %s,%s,%s)
               ON CONFLICT (org_id, message_id) DO UPDATE
                 SET property_key=EXCLUDED.property_key,
                     matched=EXCLUDED.matched, status=EXCLUDED.status,
                     fields=EXCLUDED.fields, confidence=EXCLUDED.confidence""",
            (org_id, key, matched, str(message_id), msg.get("from_email"),
             msg.get("from_name"), msg.get("subject"), msg.get("received_at"),
             status, json.dumps({k: v for k, v in fields.items()
                                 if v not in (None, "")}), confidence))
        conn.commit()


def load_intel(org_id: str | None, keys: list[str],
               limit: int = 20) -> list[dict]:
    """Intel rows for a property, newest first. ``keys`` carries every
    identity the caller knows for the card (property_id, folder name,
    normalized address) - stored rows may use any of them."""
    keys = [k for k in keys if k]
    if not (org_id and keys) or not pg.is_reachable():
        return []
    try:
        with pg.org_connection(org_id) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM property_email_intel
                    WHERE property_key = ANY(%s)
                    ORDER BY received_at DESC LIMIT %s""", (keys, limit))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
