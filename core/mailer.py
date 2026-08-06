"""Outbound transactional email (owner ask 2026-08-07: branded signup message).

One capability, kept deliberately small: send a branded HTML email over SMTP.
Configuration lives in ``.env`` (loaded HERE, not assumed from app startup —
the lesson of 2026-07-31: any module a headless step imports must load .env
itself):

    SMTP_HOST=smtp.office365.com      # or any provider
    SMTP_PORT=587                     # STARTTLS
    SMTP_USER=welcome@eight-rock.com
    SMTP_PASS=<app password>          # never committed; .env is gitignored
    MAIL_FROM=Eight Rock Workbench <welcome@eight-rock.com>   # optional

Unconfigured is a NOTICE, never a crash (repo rule): ``send()`` returns False
with a reason and the calling flow (signup/approval) continues — losing a
welcome email must never lose a signup. Failures are the same: caught,
reported in the return, swallowed by the caller's flow.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:                      # pragma: no cover - dotenv optional
    pass

import config

_GOLD = config.COLORS.get("ac", "#C8900A")
_DARK = "#1E1E24"


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASS"))


def _from_addr() -> str:
    return os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER", "")


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
    not sent ('smtp not configured' vs the actual error), never a bare skip."""
    if not to:
        return False, "no recipient address"
    if not is_configured():
        return False, "smtp not configured (set SMTP_HOST/USER/PASS in .env)"
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
