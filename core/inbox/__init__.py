"""Module D — Inbox -> Deal Engine (spec §6.2).

Connect Outlook/Gmail per user, classify inbound broker/lender/attorney mail,
extract the deal facts, and auto-create/update pipeline records with zero manual
entry — confidence-gated so a low-confidence extraction queues for one-click
human confirm instead of silently writing.

**Privacy model: private mailbox, shared pipeline.** A connected mailbox and its
raw messages belong to one user and are unreadable by anyone else — enforced by
row-level security at the database layer, not by hiding UI. The deals extracted
from that mail ARE org-visible, because the pipeline is shared work.
"""

from core.inbox.engine import (  # noqa: F401
    IngestResult, confirm_message, dismiss_message, ingest_message,
    list_deals, list_messages, list_queue, list_term_sheets,
)
from core.inbox.providers import (  # noqa: F401
    GraphMailProvider, MockMailProvider, get_provider, provider_status,
)


def sync_inbox(org_id: str, user_id: str, limit: int = 50) -> list:
    """Poll THIS user's mailbox and ingest everything new (idempotent).

    Uses the user's own OAuth token when their mailbox is connected; falls back
    to the deterministic mock fixtures otherwise, so the module stays testable
    before any mailbox is linked.
    """
    from core.inbox import oauth

    provider = None
    try:
        conn = oauth.get_connection(org_id, user_id)
        if conn and conn.get("status") == "connected":
            token = oauth.refresh_if_needed(org_id, user_id)
            if token:
                provider = GraphMailProvider(token)
    except Exception:
        provider = None      # never block a sync on the token layer

    if provider is None:
        provider = get_provider()

    results = [ingest_message(org_id, m, owner_user_id=user_id)
               for m in provider.fetch(limit=limit)]
    try:
        oauth.touch_sync(org_id, user_id)
    except Exception:
        pass
    return results


def mailbox_status(org_id: str, user_id: str) -> dict:
    """What the UI needs to describe THIS user's mailbox connection."""
    from core.inbox import oauth

    try:
        conn = oauth.get_connection(org_id, user_id)
    except Exception:
        conn = None
    if conn:
        return {"connected": True, "provider": conn["provider"],
                "account_email": conn.get("account_email"),
                "status": conn.get("status"), "last_sync_at": conn.get("last_sync_at")}
    return {"connected": False, "provider": None, "account_email": None,
            "status": "not_connected", "last_sync_at": None}
