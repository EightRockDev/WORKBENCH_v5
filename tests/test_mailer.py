"""Branded transactional email (core/mailer.py) — configured send goes over
SMTP with the Eight Rock frame; unconfigured is a notice, never a crash."""

from __future__ import annotations

import core.mailer as mailer


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.creds = (user, password)

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def _configure(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "welcome@eight-rock.com")
    monkeypatch.setenv("SMTP_PASS", "pw")
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []


def test_unconfigured_send_is_a_notice_not_a_crash(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(var, raising=False)
    ok, reason = mailer.send_signup_email("a@b.com", "Ann")
    assert ok is False and "not configured" in reason


def test_signup_email_is_branded_and_multipart(monkeypatch):
    _configure(monkeypatch)
    ok, reason = mailer.send_signup_email("new@user.com", "Jordan")
    assert ok is True and reason == "sent"
    assert len(_FakeSMTP.sent) == 1
    msg = _FakeSMTP.sent[0]
    assert msg["To"] == "new@user.com"
    assert "Welcome" in msg["Subject"]
    html = msg.get_body(("html",)).get_content()
    assert "EIGHT ROCK" in html and "awaiting approval" in html
    # Plain-text fallback exists for text-only clients.
    assert "thanks for signing up" in msg.get_body(("plain",)).get_content()


def test_approval_email_names_the_signin_url(monkeypatch):
    _configure(monkeypatch)
    ok, _ = mailer.send_approved_email("new@user.com", "Jordan")
    assert ok is True
    html = _FakeSMTP.sent[0].get_body(("html",)).get_content()
    assert "workbench.eight-rock.com" in html


def test_smtp_failure_returns_reason_never_raises(monkeypatch):
    _configure(monkeypatch)

    class _Boom(_FakeSMTP):
        def login(self, user, password):
            raise RuntimeError("bad credentials")

    monkeypatch.setattr(mailer.smtplib, "SMTP", _Boom)
    ok, reason = mailer.send("a@b.com", "s", "t", ["body"])
    assert ok is False and "bad credentials" in reason
