"""Module D — Inbox -> Deal Engine (spec §6.2).

Connect Outlook/Gmail, classify inbound broker/lender/attorney mail, extract the
deal facts, and auto-create/update pipeline records with zero manual entry —
confidence-gated so a low-confidence extraction queues for one-click human
confirm instead of silently writing.
"""

from core.inbox.engine import (  # noqa: F401
    IngestResult, confirm_message, dismiss_message, ingest_message,
    list_deals, list_messages, list_queue, list_term_sheets,
)
from core.inbox.providers import get_provider, provider_status  # noqa: F401


def sync_inbox(org_id: str, limit: int = 50) -> list:
    """Poll the configured mailbox and ingest everything new (idempotent)."""
    provider = get_provider()
    return [ingest_message(org_id, m) for m in provider.fetch(limit=limit)]
