"""Outbound transactional email (owner ask 2026-08-07: branded signup message).

Two transports, tried in this order:

1. **Microsoft Graph** (preferred — the tenant hard-blocks Basic SMTP auth;
   two fresh app passwords 535'd on 2026-08-07, and Microsoft is retiring
   Basic SMTP through 2026 anyway). Entra app registration with the
   application permission ``Mail.Send`` + admin consent. `.env`:

       GRAPH_TENANT_ID=<Directory (tenant) ID>
       GRAPH_CLIENT_ID=<Application (client) ID>
       GRAPH_CLIENT_SECRET=<client secret VALUE>
       GRAPH_SENDER=welcome@eight-rock.com   # a REAL mailbox (the free
                 # SHARED mailbox created 2026-08-08). Graph refuses to send
                 # from a mere alias (ErrorSendAsDenied) even with
                 # SendFromAliasEnabled — the sender must be a real mailbox.
       MAIL_FROM=Eight Rock Workbench <welcome@eight-rock.com>

2. **SMTP** fallback (kept for non-M365 providers):
   SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS.

Config lives in ``.env``, loaded HERE (2026-07-31 lesson: any module a
headless step imports must load .env itself). Unconfigured is a NOTICE,
never a crash: ``send()`` returns (False, reason) and the calling flow
(signup/approval) continues — losing a welcome email must never lose a
signup.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:                      # pragma: no cover - dotenv optional
    pass

import config

_GOLD = config.COLORS.get("ac", "#C8900A")
_DARK = "#1E1E24"

_GRAPH_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


def graph_configured() -> bool:
    return bool(os.environ.get("GRAPH_TENANT_ID")
                and os.environ.get("GRAPH_CLIENT_ID")
                and os.environ.get("GRAPH_CLIENT_SECRET"))


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASS"))


def is_configured() -> bool:
    return graph_configured() or smtp_configured()


def transport() -> str:
    if graph_configured():
        return "graph"
    if smtp_configured():
        return "smtp"
    return "none"


def _from_addr() -> str:
    return (os.environ.get("MAIL_FROM") or os.environ.get("GRAPH_SENDER")
            or os.environ.get("SMTP_USER", ""))


def _graph_sender_mailbox() -> str:
    """The REAL mailbox Graph sends via (/users/{mailbox}/sendMail). The
    alias in MAIL_FROM rides in the message's from field, not the URL."""
    return (os.environ.get("GRAPH_SENDER") or os.environ.get("SMTP_USER")
            or parseaddr(_from_addr())[1])


def _graph_send(to: str, subject: str, html: str) -> tuple[bool, str]:
    import msal
    tenant = os.environ["GRAPH_TENANT_ID"]
    app = msal.ConfidentialClientApplication(
        os.environ["GRAPH_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=os.environ["GRAPH_CLIENT_SECRET"])
    tok = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in tok:
        return False, ("graph auth failed: "
                       + str(tok.get("error_description", tok))[:300])
    name, addr = parseaddr(_from_addr())
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": to}}],
    }
    if addr:
        message["from"] = {"emailAddress": {"name": name or None,
                                            "address": addr}}
    resp = requests.post(
        _GRAPH_URL.format(sender=_graph_sender_mailbox()),
        headers={"Authorization": f"Bearer {tok['access_token']}"},
        json={"message": message, "saveToSentItems": False},
        timeout=30)
    if resp.status_code == 202:
        return True, "sent"
    return False, f"graph sendMail {resp.status_code}: {resp.text[:300]}"


def _branded_html(title: str, body_lines: list[str]) -> str:
    """Shared Eight Rock frame: dark header bar, gold rule, plain readable
    body. Inline styles only — email clients ignore stylesheets."""
    paras = "".join(
        f"<p style='margin:0 0 14px 0;font-size:15px;line-height:1.55;"
        f"color:#333'>{line}</p>" for line in body_lines)
    return f"""\
<div style="max-width:560px;margin:0 auto;font-family:Segoe UI,Arial,sans-serif;
            border:1px solid #e3e3e3;border-radius:8px;overflow:hidden">
  <div style="background:{_DARK};padding:22px 28px">
    <span style="color:{_GOLD};font-size:20px;font-weight:700;
                 letter-spacing:0.5px">EIGHT ROCK</span>
    <span style="color:#fff;font-size:20px;font-weight:300"> WORKBENCH</span>
  </div>
  <div style="height:3px;background:{_GOLD}"></div>
  <div style="padding:26px 28px">
    <h2 style="margin:0 0 16px 0;font-size:18px;color:{_DARK}">{title}</h2>
    {paras}
  </div>
  <div style="padding:14px 28px;background:#f7f7f7;border-top:1px solid #e3e3e3">
    <p style="margin:0;font-size:12px;color:#888">Eight Rock Capital Partners
    &middot; This message was sent by the Workbench platform.</p>
  </div>
</div>"""


def send(to: str, subject: str, title: str,
         body_lines: list[str]) -> tuple[bool, str]:
    """Send one branded email. Returns (sent, reason) — reason says WHY when
    not sent ('not configured' vs the actual error), never a bare skip."""
    if not to:
        return False, "no recipient address"
    if not is_configured():
        return False, ("mail not configured (set GRAPH_TENANT_ID/CLIENT_ID/"
                       "CLIENT_SECRET or SMTP_HOST/USER/PASS in .env)")
    if graph_configured():
        try:
            return _graph_send(to, subject,
                               _branded_html(title, body_lines))
        except Exception as exc:                  # NOTICE, never a crash
            return False, f"graph error: {exc}"
    msg = EmailMessage()
    msg["From"] = _from_addr()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("\n\n".join(body_lines))          # plain-text fallback
    msg.add_alternative(_branded_html(title, body_lines), subtype="html")
    try:
        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
        return True, "sent"
    except Exception as exc:                          # NOTICE, never a crash
        return False, f"smtp error: {exc}"


# ------------------------------------------------------- signup lifecycle

def send_signup_email(to: str, display_name: str | None = None) -> tuple[bool, str]:
    """New signup landed in the pending queue — welcome them and set the
    expectation that an admin approves access (FR-9.4: safe-by-default)."""
    name = (display_name or "").strip() or "there"
    return send(
        to,
        subject="Welcome to Eight Rock Workbench",
        title=f"Welcome, {name}",
        body_lines=[
            f"Hi {name} — thanks for signing up for the Eight Rock Workbench.",
            "Your account has been created and is awaiting approval by an "
            "administrator. You'll be able to sign in as soon as you're "
            "approved — no further action is needed from you.",
            "If you weren't expecting this email, you can safely ignore it.",
        ])


def send_approved_email(to: str, display_name: str | None = None) -> tuple[bool, str]:
    """Admin approved the account — tell the user they're in."""
    name = (display_name or "").strip() or "there"
    return send(
        to,
        subject="You're in — Eight Rock Workbench access approved",
        title="Access approved",
        body_lines=[
            f"Hi {name} — an administrator has approved your Eight Rock "
            "Workbench account.",
            "Sign in with the same account you signed up with at "
            "<a href='https://workbench.eight-rock.com' "
            f"style='color:{_GOLD}'>workbench.eight-rock.com</a>.",
        ])
