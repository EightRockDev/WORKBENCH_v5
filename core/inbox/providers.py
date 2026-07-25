"""Module D — mail source abstraction (spec §6.2, §8 vendor abstraction).

The spec's identity layer is Microsoft-first, so Outlook/Graph is the primary
adapter with Gmail as the alternate. Same pattern as the skip-trace providers:
a ``MailProvider`` interface, a deterministic **mock** for development/testing,
and live adapters selected by environment.

    ER_INBOX_PROVIDER=graph   MS_GRAPH_TOKEN=...      (Outlook / Microsoft 365)
    ER_INBOX_PROVIDER=gmail   GMAIL_TOKEN=...
    (unset)                   -> deterministic mock fixtures, no network

Live adapters read a mailbox and normalize to the same message dict the engine
consumes, so `core/inbox/engine.py` never changes when the source changes.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Protocol

_TIMEOUT = 30


class MailProvider(Protocol):
    name: str

    def fetch(self, since: dt.datetime | None = None, limit: int = 50) -> list[dict]: ...


# ---------------------------------------------------------------------------
# Deterministic mock — realistic broker / lender / attorney / noise traffic
# ---------------------------------------------------------------------------

_FIXTURES: list[dict] = [
    {
        "external_id": "mock-001",
        "from_email": "jsmith@marcusmillichap.com", "from_name": "Jim Smith",
        "subject": "New to Market: Crossroads Townhomes - 26 Units, Norfolk VA",
        "body": ("Team,\n\nPlease find attached the OM for Crossroads Townhomes, "
                 "1200 Ballentine Blvd, Norfolk, VA 23504. 26 units, asking "
                 "$2,850,000 at a 7.25% cap. Seller is motivated - call for offers "
                 "closes in two weeks.\n\nBest,\nJim"),
        "attachments": [{"filename": "Crossroads-Townhomes-OM.pdf", "size": 4210221}],
    },
    {
        "external_id": "mock-002",
        "from_email": "lending@walkerdunlop.com", "from_name": "Dana Reyes",
        "subject": "Term Sheet - Crossroads Townhomes",
        "body": ("Attached is our term sheet. Indicative pricing: rate 5.85%, "
                 "70% LTV, 30 year amortization, 3 years interest-only, 10 year "
                 "term. Loan amount $1,995,000. Walker Dunlop Capital is pleased "
                 "to quote this.\n\nDana"),
        "attachments": [{"filename": "term-sheet.pdf", "size": 220110}],
    },
    {
        "external_id": "mock-003",
        "from_email": "closing@harborlawllp.com", "from_name": "Ann Boyd",
        "subject": "PSA draft for review - closing timeline",
        "body": ("Counsel,\n\nAttached please find the draft purchase and sale "
                 "agreement. Escrow agent is confirmed; closing date proposed "
                 "60 days from execution.\n\nAnn Boyd, Esq."),
        "attachments": [{"filename": "PSA-draft.docx", "size": 88120}],
    },
    {
        "external_id": "mock-004",
        "from_email": "newsletter@retailweekly.com", "from_name": "Retail Weekly",
        "subject": "This week in retail: 5 trends to watch",
        "body": "Your weekly roundup of retail news and commentary.",
        "attachments": [],
    },
    {
        "external_id": "mock-005",
        "from_email": "broker@crexi.com", "from_name": "Pat Lang",
        "subject": "Investment opportunity - multifamily",
        "body": ("Hi - wanted to flag an off-market multifamily opportunity in "
                 "Hampton Roads. Happy to share details if there's interest."),
        "attachments": [],
    },
]


class MockMailProvider:
    """Deterministic fixtures: two clean deals, one attorney thread, one noise
    message, and one deliberately vague broker note that must land in the
    human-confirm queue rather than auto-writing a deal."""

    name = "mock"

    def fetch(self, since: dt.datetime | None = None, limit: int = 50) -> list[dict]:
        base = dt.datetime(2026, 7, 20, 14, 0, tzinfo=dt.timezone.utc)
        out = []
        for i, f in enumerate(_FIXTURES[:limit]):
            m = dict(f)
            m["provider"] = self.name
            m["received_at"] = base + dt.timedelta(hours=i)
            out.append(m)
        return out


# ---------------------------------------------------------------------------
# Live adapters
# ---------------------------------------------------------------------------

class GraphMailProvider:
    """Microsoft 365 / Outlook via Graph. Reuses the Microsoft-first identity
    layer the spec already assumes (§6.2)."""

    name = "graph"
    BASE = os.environ.get("MS_GRAPH_BASE", "https://graph.microsoft.com/v1.0")

    def __init__(self, token: str, mailbox: str = "me"):
        self._token, self._mailbox = token, mailbox

    def fetch(self, since: dt.datetime | None = None, limit: int = 50) -> list[dict]:
        import requests

        url = f"{self.BASE}/{self._mailbox}/messages"
        params = {"$top": min(limit, 100),
                  "$select": "id,subject,from,receivedDateTime,bodyPreview,body,hasAttachments",
                  "$orderby": "receivedDateTime desc"}
        if since:
            params["$filter"] = f"receivedDateTime ge {since:%Y-%m-%dT%H:%M:%SZ}"
        r = requests.get(url, headers={"Authorization": f"Bearer {self._token}",
                                       "Accept": "application/json"},
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        out = []
        for m in r.json().get("value", []):
            frm = ((m.get("from") or {}).get("emailAddress") or {})
            body = (m.get("body") or {}).get("content") or m.get("bodyPreview") or ""
            out.append({
                "provider": self.name, "external_id": m.get("id"),
                "from_email": frm.get("address"), "from_name": frm.get("name"),
                "subject": m.get("subject"), "body": _strip_html(body),
                "received_at": m.get("receivedDateTime"),
                "attachments": ([{"filename": "(attachment)"}]
                                if m.get("hasAttachments") else []),
            })
        return out


class GmailMailProvider:
    name = "gmail"
    BASE = os.environ.get("GMAIL_BASE", "https://gmail.googleapis.com/gmail/v1")

    def __init__(self, token: str, user_id: str = "me"):
        self._token, self._user = token, user_id

    def fetch(self, since: dt.datetime | None = None, limit: int = 50) -> list[dict]:
        import base64

        import requests

        h = {"Authorization": f"Bearer {self._token}"}
        q = f"after:{since:%Y/%m/%d}" if since else ""
        lst = requests.get(f"{self.BASE}/users/{self._user}/messages", headers=h,
                           params={"maxResults": min(limit, 100), "q": q},
                           timeout=_TIMEOUT)
        lst.raise_for_status()
        out = []
        for ref in lst.json().get("messages", [])[:limit]:
            d = requests.get(f"{self.BASE}/users/{self._user}/messages/{ref['id']}",
                             headers=h, params={"format": "full"}, timeout=_TIMEOUT).json()
            headers = {x["name"].lower(): x["value"]
                       for x in (d.get("payload", {}).get("headers") or [])}
            frm = headers.get("from", "")
            email = frm.split("<")[-1].strip(">") if "<" in frm else frm
            body = ""
            for part in (d.get("payload", {}).get("parts") or []):
                if part.get("mimeType") == "text/plain":
                    data = (part.get("body") or {}).get("data")
                    if data:
                        body = base64.urlsafe_b64decode(data + "==").decode("utf-8", "ignore")
                        break
            out.append({
                "provider": self.name, "external_id": d.get("id"),
                "from_email": email, "from_name": frm.split("<")[0].strip().strip('"'),
                "subject": headers.get("subject"), "body": body or d.get("snippet", ""),
                "received_at": None,
                "attachments": [{"filename": p.get("filename")}
                                for p in (d.get("payload", {}).get("parts") or [])
                                if p.get("filename")],
            })
        return out


def _strip_html(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def get_provider() -> MailProvider:
    """Select the mail source from the environment; mock when unconfigured."""
    kind = os.environ.get("ER_INBOX_PROVIDER", "mock").lower()
    if kind == "graph":
        tok = os.environ.get("MS_GRAPH_TOKEN")
        if tok:
            return GraphMailProvider(tok, os.environ.get("ER_INBOX_MAILBOX", "me"))
    elif kind == "gmail":
        tok = os.environ.get("GMAIL_TOKEN")
        if tok:
            return GmailMailProvider(tok)
    return MockMailProvider()


def provider_status() -> str:
    p = get_provider()
    return f"live ({p.name})" if p.name != "mock" else "mock"
