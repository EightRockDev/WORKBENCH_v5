"""Inbox -> property-data ingestion (owner 2026-08-11, corrected same day:
"Just want you to ingest data. Not pull individual emails and display in
workbench.")

Gate-clearing mail extractions are ingested as DATA, exactly like any other
feed: one ``muni_records`` row (kind='assessor-email') keyed to the matched
backbone parcel's apn. The spine build already consumes every
kind LIKE 'assessor%' source and merges per-parcel with COALESCE - so an
email's unit count fills a gap the assessor left NULL and can never
override assessor data. No UI of its own: the facts surface as ordinary
property details wherever the backbone renders.

Matching is by normalized address; a city, when extracted, must agree
(localities share street names). No backbone match -> no ingest: a
parcel-less row cannot join the spine, and guessing a parcel would poison
it.

Idempotent per message (source_url carries the message id; re-ingest
replaces). §6.2 gate + privacy unchanged: only auto-applied or
human-confirmed messages reach this, and what lands is the extract, not
the mail. Degradation: any failure is a silent no-op - ingest enrichment
must never break the inbox pipeline.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

# What an email extraction may contribute to the parcel record. Keys are
# phase0-normalizable spellings; asking_price/cap_rate ride along verbatim
# for future consumers (the spine ignores what it doesn't map).
_INGEST_FIELDS = ("units", "asking_price", "cap_rate", "name")


def match_property(fields: dict[str, Any]) -> dict[str, Any] | None:
    """The backbone property this extraction is about, or None."""
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
            sql = ("SELECT property_id, apn, address, city, state, units "
                   "FROM properties_8r WHERE address IS NOT NULL")
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
                 extraction, status: str = "applied") -> str | None:
    """Ingest one gate-clearing extraction into muni_records. Returns the
    matched property_id, or None. Never raises into ingest."""
    try:
        fields = dict(getattr(extraction, "fields", None) or {})
        if not any(fields.get(k) for k in _INGEST_FIELDS):
            return None
        prop = match_property(fields)
        if prop is None or not prop.get("apn"):
            return None          # no parcel identity - nothing to join on
        _ingest_row(prop, message_id, fields,
                    float(getattr(extraction, "confidence", 0.0) or 0.0))
        return str(prop["property_id"])
    except Exception:
        return None              # additive enrichment - never block ingest


def _ingest_row(prop: dict, message_id: str, fields: dict,
                confidence: float) -> None:
    from data import db
    record = {
        "apn": prop["apn"],
        "address": prop.get("address"),
        "city": prop.get("city"),
        # Email-claimed facts, phase0-normalizable spellings first.
        **{k: fields[k] for k in _INGEST_FIELDS if fields.get(k) not in
           (None, "")},
        "_source": "inbox-module-d",
        "_confidence": round(confidence, 3),
    }
    tag = f"inbox:{message_id}"
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    with db.get_connection(db.DB_PATH) as conn:
        # One row per message: re-ingest replaces (same semantics as the
        # sales pullers' per-source refresh).
        conn.execute("DELETE FROM muni_records WHERE source_url = ?", (tag,))
        conn.execute(
            "INSERT INTO muni_records (market, state, county, kind, "
            "source_url, pulled_at, record) VALUES (?,?,?,?,?,?,?)",
            (prop.get("city"), prop.get("state") or "VA", prop.get("city"),
             "assessor-email", tag, now_iso, json.dumps(record)))
        conn.commit()
