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
    for var in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "welcome@eight-rock.com")
    monkeypatch.setenv("SMTP_PASS", "pw")
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []


def test_unconfigured_send_is_a_notice_not_a_crash(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS",
                "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
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


# ------------------------------------------------------- Graph transport

def _configure_graph(monkeypatch):
    monkeypatch.setenv("GRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GRAPH_SENDER", "Brian@eight-rock.com")
    monkeypatch.setenv(
        "MAIL_FROM", "Eight Rock Workbench <welcome@eight-rock.com>")


class _FakeMsalApp:
    token = {"access_token": "tok"}

    def __init__(self, *a, **k):
        pass

    def acquire_token_for_client(self, scopes):
        return _FakeMsalApp.token


def test_graph_is_preferred_over_smtp_and_sends_as_alias(monkeypatch):
    _configure(monkeypatch)          # SMTP also configured...
    _configure_graph(monkeypatch)    # ...but Graph must win
    import msal
    monkeypatch.setattr(msal, "ConfidentialClientApplication", _FakeMsalApp)
    _FakeMsalApp.token = {"access_token": "tok"}
    posted = {}

    class Resp:
        status_code = 202
        text = ""

    def fake_post(url, headers=None, json=None, timeout=None):
        posted["url"], posted["json"] = url, json
        return Resp()

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    ok, reason = mailer.send_signup_email("new@user.com", "Jordan")
    assert (ok, reason) == (True, "sent")
    assert _FakeSMTP.sent == [], "graph configured -> SMTP must not be used"
    # Sends VIA the real mailbox, FROM the alias.
    assert "/users/Brian@eight-rock.com/sendMail" in posted["url"]
    frm = posted["json"]["message"]["from"]["emailAddress"]
    assert frm["address"] == "welcome@eight-rock.com"
    assert posted["json"]["message"]["body"]["contentType"] == "HTML"
    assert "EIGHT ROCK" in posted["json"]["message"]["body"]["content"]


def test_graph_auth_failure_is_a_reason_not_a_crash(monkeypatch):
    _configure_graph(monkeypatch)
    import msal
    monkeypatch.setattr(msal, "ConfidentialClientApplication", _FakeMsalApp)
    _FakeMsalApp.token = {"error_description": "AADSTS7000215 bad secret"}
    ok, reason = mailer.send("a@b.com", "s", "t", ["x"])
    assert ok is False and "AADSTS7000215" in reason


def test_graph_api_error_surfaces_status_and_body(monkeypatch):
    _configure_graph(monkeypatch)
    import msal
    monkeypatch.setattr(msal, "ConfidentialClientApplication", _FakeMsalApp)
    _FakeMsalApp.token = {"access_token": "tok"}

    class Resp:
        status_code = 403
        text = "ErrorAccessDenied: not granted"

    monkeypatch.setattr(mailer.requests, "post",
                        lambda *a, **k: Resp())
    ok, reason = mailer.send("a@b.com", "s", "t", ["x"])
    assert ok is False and "403" in reason and "ErrorAccessDenied" in reason
