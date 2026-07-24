"""ADVERSARIAL compliance-gate tests (spec §4.4 C1-C7, §13 verification rule).

The spec requires "an adversarial test suite that attempts to dial unstamped
numbers and must fail". These tests actively try to get a non-compliant touch
through the gate; every attempt must be refused AND logged.

Also covers AC-B2: 100% of touches logged with channel, timestamp, rule trace
and outcome, audit-exportable.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.compliance import ledger, rules
from core.outreach import engine
from data import pg

pytestmark = pytest.mark.skipif(not pg.is_configured(), reason="Postgres not configured")

CLEAN_PHONE = {
    "e164": "+17575550100", "line_type": "mobile", "grade": "A", "litigator": False,
    "callable": True,
    "dnc": {"federal": False, "state": [], "scrubbed_at": "2026-07-24T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00"},
}
NOON_ET = dt.datetime(2026, 7, 24, 16, 0, tzinfo=dt.timezone.utc)   # 12:00 in UTC-4/-5


def _phone(**over):
    p = {**CLEAN_PHONE, "dnc": dict(CLEAN_PHONE["dnc"])}
    dnc = over.pop("dnc", None)
    p.update(over)
    if dnc:
        p["dnc"].update(dnc)
    return p


@pytest.fixture()
def org():
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO organizations (name) VALUES ('T') RETURNING id")
        oid = str(cur.fetchone()["id"])
        conn.commit()
    yield oid
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
        conn.commit()


def _ev(org_id, **kw):
    kw.setdefault("channel", "call")
    kw.setdefault("e164", CLEAN_PHONE["e164"])
    kw.setdefault("state", "VA")
    kw.setdefault("now_utc", NOON_ET)
    kw.setdefault("phone_record", CLEAN_PHONE)
    return rules.evaluate(org_id, **kw)


# ---------------------------------------------------------------------------
# Baseline: a clean, stamped, in-window manual dial IS allowed
# ---------------------------------------------------------------------------

def test_clean_manual_dial_allowed(org):
    d = _ev(org)
    assert d.allowed, d.reason
    assert {r.rule for r in d.trace} >= {
        "C1-INTERNAL-DNC", "C1-SCRUB-FRESH", "C1-FEDERAL-DNC", "C1-STATE-DNC",
        "C2-LITIGATOR", "C3-CHANNEL", "C4-QUIET-HOURS", "C4-FREQUENCY",
        "C5-REVOCATION", "C6-FCRA", "C7-LICENSING"}


# ---------------------------------------------------------------------------
# ADVERSARIAL — each of these MUST be refused
# ---------------------------------------------------------------------------

def test_unstamped_number_is_refused(org):
    """The headline adversarial case: no valid scrub -> must not be dialable."""
    d = _ev(org, phone_record={"e164": CLEAN_PHONE["e164"], "line_type": "mobile"})
    assert not d.allowed
    assert any(r.rule == "C1-SCRUB-FRESH" and not r.passed for r in d.trace)


def test_expired_stamp_is_refused(org):
    d = _ev(org, phone_record=_phone(dnc={"expires_at": "2020-01-01T00:00:00+00:00"}))
    assert not d.allowed
    assert any(r.rule == "C1-SCRUB-FRESH" and not r.passed for r in d.trace)


def test_federal_dnc_refused(org):
    d = _ev(org, phone_record=_phone(dnc={"federal": True}))
    assert not d.allowed and any(r.rule == "C1-FEDERAL-DNC" and not r.passed for r in d.trace)


def test_state_dnc_refused(org):
    d = _ev(org, phone_record=_phone(dnc={"state": ["TX"]}))
    assert not d.allowed and any(r.rule == "C1-STATE-DNC" and not r.passed for r in d.trace)


def test_litigator_refused(org):
    d = _ev(org, phone_record=_phone(litigator=True))
    assert not d.allowed and any(r.rule == "C2-LITIGATOR" and not r.passed for r in d.trace)


def test_internal_dnc_refused(org):
    ledger.add_internal_dnc(org, CLEAN_PHONE["e164"], "prior request")
    d = _ev(org)
    assert not d.allowed and any(r.rule == "C1-INTERNAL-DNC" and not r.passed for r in d.trace)


def test_prerecorded_to_cell_without_consent_hard_blocked(org):
    """C3: the Terrakotta-style voice-clone drop must be impossible by default."""
    for sub in ("prerecorded", "ai_voice", "voice_clone", "rvm", "ringless"):
        d = _ev(org, channel="voicemail", subtype=sub)
        assert not d.allowed, f"{sub} should be blocked"
        assert any(r.rule == "C3-CHANNEL" and not r.passed for r in d.trace)


def test_prerecorded_allowed_only_with_express_written_consent(org):
    ledger.record_consent(org, CLEAN_PHONE["e164"], "voice", evidence="signed web form")
    d = _ev(org, channel="voicemail", subtype="prerecorded")
    assert d.allowed, d.reason


def test_sms_requires_consent(org):
    d = _ev(org, channel="sms", subtype="sms")
    assert not d.allowed and any(r.rule == "C3-CHANNEL" and not r.passed for r in d.trace)


def test_quiet_hours_refused(org):
    # 03:00 UTC == 22:00 ET previous day -> outside 8-21 local
    d = _ev(org, now_utc=dt.datetime(2026, 7, 25, 3, 0, tzinfo=dt.timezone.utc))
    assert not d.allowed and any(r.rule == "C4-QUIET-HOURS" and not r.passed for r in d.trace)


def test_unknown_geography_uses_conservative_window(org):
    """No area-code/state match -> must satisfy the window in EVERY US zone."""
    # 14:00 UTC = 09:00 ET but 06:00 PT -> refused under the conservative rule.
    d = _ev(org, e164="+19995550000", state=None,
            phone_record=_phone(e164="+19995550000"),
            now_utc=dt.datetime(2026, 7, 24, 13, 0, tzinfo=dt.timezone.utc))
    assert not d.allowed and any(r.rule == "C4-QUIET-HOURS" and not r.passed for r in d.trace)


def test_revocation_blocks_every_channel(org):
    ledger.record_revocation(org, e164=CLEAN_PHONE["e164"], source="inbound_call")
    for ch, sub in (("call", "manual_dial"), ("sms", "sms"), ("mail", "letter")):
        d = _ev(org, channel=ch, subtype=sub)
        assert not d.allowed, f"{ch} should be blocked after opt-out"
        assert any(r.rule == "C5-REVOCATION" and not r.passed for r in d.trace)


def test_revocation_propagates_to_internal_dnc(org):
    ledger.record_revocation(org, e164=CLEAN_PHONE["e164"], source="sms_stop")
    assert ledger.on_internal_dnc(org, CLEAN_PHONE["e164"])


def test_fcra_firewall_refuses_screening_purpose(org):
    for bad in ("tenant_screening", "credit", "employment"):
        d = _ev(org, purpose=bad)
        assert not d.allowed
        assert any(r.rule == "C6-FCRA" and not r.passed for r in d.trace)


def test_managed_service_refused(org):
    d = _ev(org, managed_service=True)
    assert not d.allowed and any(r.rule == "C7-LICENSING" and not r.passed for r in d.trace)


def test_frequency_cap_enforced(org):
    for _ in range(rules.DEFAULT_DAILY_CAP):
        r = engine.attempt_touch(org, channel="call", e164=CLEAN_PHONE["e164"],
                                 state="VA", phone_record=CLEAN_PHONE,
                                 person_name="X", dispatcher=lambda: "connected")
        assert r.allowed
    blocked = engine.attempt_touch(org, channel="call", e164=CLEAN_PHONE["e164"],
                                   state="VA", phone_record=CLEAN_PHONE, person_name="X")
    assert not blocked.allowed
    assert any(r["rule"] == "C4-FREQUENCY" and not r["passed"]
               for r in blocked.decision.trace_json())


# ---------------------------------------------------------------------------
# AC-B2 — every touch logged with a rule trace; blocked ones too
# ---------------------------------------------------------------------------

def test_blocked_touch_is_still_logged_and_not_dispatched(org):
    dispatched = []
    res = engine.attempt_touch(
        org, channel="voicemail", subtype="prerecorded", e164=CLEAN_PHONE["e164"],
        state="VA", phone_record=CLEAN_PHONE, person_name="Owner",
        dispatcher=lambda: dispatched.append(1) or "sent")
    assert not res.allowed
    assert dispatched == []            # the dispatcher was never invoked
    rows = engine.export_touches(org)
    assert len(rows) == 1 and rows[0]["allowed"] is False
    assert rows[0]["rule_trace"], "blocked touch must carry its rule trace"


def test_audit_export_has_trace_columns(org):
    engine.attempt_touch(org, channel="call", e164=CLEAN_PHONE["e164"], state="VA",
                         phone_record=CLEAN_PHONE, person_name="Owner",
                         dispatcher=lambda: "connected")
    csv_text = engine.export_touches_csv(org)
    assert "rules_passed" in csv_text and "C1-FEDERAL-DNC" in csv_text


def test_touch_log_is_append_only(org):
    engine.attempt_touch(org, channel="call", e164=CLEAN_PHONE["e164"], state="VA",
                         phone_record=CLEAN_PHONE, person_name="Owner",
                         dispatcher=lambda: "connected")
    import psycopg
    with pg.org_connection(org) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE outreach_touches SET outcome='tampered'")


# ---------------------------------------------------------------------------
# B1 — dial list contains only callable numbers
# ---------------------------------------------------------------------------

def test_dial_list_excludes_non_callable():
    pocs = [{
        "id": None, "property_id": "P1", "role": "owner",
        "person": {"full_name": "Jane Owner"},
        "phones": [
            {"e164": "+17575550001", "grade": "A", "callable": True, "line_type": "mobile"},
            {"e164": "+17575550002", "grade": "F", "callable": False, "line_type": "mobile"},
            {"e164": "+17575550003", "grade": "B", "callable": True, "line_type": "landline"},
        ]}]
    targets = engine.callable_targets(pocs)
    assert [t["e164"] for t in targets] == ["+17575550001", "+17575550003"]
    assert all(t["phone_record"]["callable"] for t in targets)
