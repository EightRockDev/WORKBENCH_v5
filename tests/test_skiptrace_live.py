"""Live-adapter parsing tests — HTTP mocked, no real vendor calls (spec §8).

Verifies that the BatchData / Trestle / VA-SCC adapters map real-shaped vendor
payloads onto the provider result contracts, that the registry mixes live+mock
per provider by env keys, and that a full pipeline run works end-to-end with a
live-mocked BatchData while grading/piercing stay on mock.
"""

from __future__ import annotations

import os

import pytest

from core.skiptrace import live, pipeline
from core.skiptrace import providers as prov


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in ("ER_SKIPTRACE_PROVIDERS", "BATCHDATA_API_KEY", "TRESTLE_API_KEY",
              "VA_SCC_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# BatchData parsing
# ---------------------------------------------------------------------------

def test_batchdata_maps_payload(monkeypatch):
    payload = {"requestId": "req-1", "results": {"persons": [{
        "name": {"full": "Michael Cleghorn"},
        "phoneNumbers": [{"number": "(757) 555-0142"}, {"number": "757.555.9987"}],
        "emails": [{"email": "mike@example.com"}],
        "addresses": [{"formattedAddress": "12 Main St, Norfolk VA", "type": "Mailing"}],
        "ageRange": "45-54", "deceased": False,
    }]}}
    monkeypatch.setattr(live, "_post", lambda *a, **k: payload)
    cand = live.BatchDataSkipTrace("key").trace_person("Michael Cleghorn", "12 Main St", "VA")
    assert cand is not None
    assert cand.phones == ["+17575550142", "+17575559987"]      # normalized E.164
    assert cand.emails == ["mike@example.com"]
    assert cand.vendor == "batchdata" and cand.cost_usd > 0
    assert cand.addresses[0]["kind"] == "mailing"


def test_batchdata_no_hit_returns_none(monkeypatch):
    monkeypatch.setattr(live, "_post", lambda *a, **k: {"results": {"persons": []}})
    assert live.BatchDataSkipTrace("key").trace_person("No One", None, "VA") is None


def test_e164_normalization():
    assert live._e164("(757) 555-0142") == "+17575550142"
    assert live._e164("1-757-555-0142") == "+17575550142"


# ---------------------------------------------------------------------------
# Trestle parsing → grading inputs
# ---------------------------------------------------------------------------

def test_trestle_maps_validation(monkeypatch):
    monkeypatch.setattr(live, "_get", lambda *a, **k: {
        "line_type": "Mobile", "is_valid": True, "name_match_score": 92,
        "is_litigator": False, "is_dnc": True, "request_id": "t-1"})
    v = live.TrestleValidation("key").validate_phone("+17575550142", "Michael Cleghorn")
    assert v.line_type == "mobile" and v.active is True
    assert v.name_match == 0.92          # 92 → 0.92
    assert v.dnc_federal is True and v.litigator is False
    # feed the grader: DNC federal must render non-callable regardless of grade
    import datetime as dt
    stamped = pipeline._stamp_phone(v, dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc))
    assert stamped["callable"] is False and "DNC" in stamped["reason"]


# ---------------------------------------------------------------------------
# Registry mixing by env
# ---------------------------------------------------------------------------

def test_registry_all_mock_by_default():
    reg = prov.get_registry()
    assert reg.status == {"sos": "mock", "skiptrace": "mock", "validation": "mock"}


def test_registry_goes_live_per_key(monkeypatch):
    monkeypatch.setenv("ER_SKIPTRACE_PROVIDERS", "live")
    monkeypatch.setenv("BATCHDATA_API_KEY", "bd")
    reg = prov.get_registry()
    assert "live" in reg.status["skiptrace"]        # BatchData live
    assert reg.status["sos"] == "mock"              # no VA token → mock piercing
    assert reg.status["validation"] == "mock"       # no Trestle key → mock
    assert isinstance(reg.trace_waterfall[0], live.BatchDataSkipTrace)


def test_pipeline_runs_with_live_batchdata(monkeypatch):
    monkeypatch.setenv("ER_SKIPTRACE_PROVIDERS", "live")
    monkeypatch.setenv("BATCHDATA_API_KEY", "bd")
    monkeypatch.setattr(live, "_post", lambda *a, **k: {"requestId": "r", "results": {"persons": [{
        "name": {"full": "Robert Cleghorn"},
        "phoneNumbers": [{"number": "7575551234"}], "emails": [],
    }]}})
    prop = {"property_id": "P1", "owner": "Robert Cleghorn", "state": "VA",
            "owner_address": "1 A St", "management_company": ""}
    res = pipeline.resolve_contacts("org", prop, persist=False)
    assert res.pocs and res.pocs[0]["phones"]
    assert res.pocs[0]["phones"][0]["e164"] == "+17575551234"
    assert any(s["vendor"] == "batchdata" for s in res.spend_lines)
