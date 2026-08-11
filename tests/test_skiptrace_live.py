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
    assert reg.status == {"sos": "mock", "skiptrace": "mock",
                          "validation": "mock", "firmographic": "mock"}


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


# ---------------------------------------------------------------------------
# 2026-08-11 "it never has worked" fixes: evidence trace, entity-name guard,
# unpierced fallback role, Apollo /api prefix, Cobalt retryId, Trestle
# Real Contact name-match parsing.
# ---------------------------------------------------------------------------

from core.skiptrace import trace as _trace  # noqa: E402


class _NoneSOS:
    name = "cobalt"

    def resolve_entity(self, entity_name, state):
        return None                     # live SOS erred / found nothing


class _BoomTier:
    name = "batchdata"
    tier = 2
    calls = 0

    def trace_person(self, full_name, address_hint, state):
        type(self).calls += 1
        raise AssertionError("person trace must not run for an entity name")


def _reg_unpierced():
    return prov.ProviderRegistry(
        sos=_NoneSOS(), trace_waterfall=[_BoomTier()],
        validation=prov.MockTrestle(), firmographic=prov.MockFirmographic(),
        status={"sos": "live (cobalt)", "skiptrace": "live (batchdata)",
                "validation": "live (trestle)", "firmographic": "live (apollo)"})


def test_failed_pierce_is_unpierced_role_not_owner_and_skips_person_trace():
    """The screenshot bug: Cobalt returned None -> the LLC rendered as role
    'owner' with empty phone/email lines, BatchData was asked to trace
    'Gd Richmond Two Llc' as a person, and Apollo never ran."""
    _BoomTier.calls = 0
    res = pipeline.resolve_contacts(
        "org", {"property_id": "P1", "owner": "Gd Richmond Two Llc",
                "state": "VA", "owner_address": "1 Main, Richmond VA"},
        registry=_reg_unpierced(), persist=False)
    card = res.pocs[0]
    assert card["role"] == "entity_unpierced"
    assert _BoomTier.calls == 0                       # no garbage person trace
    assert "registry returned no record" in card["person"]["unpierced_note"]
    assert card["business_contact"]["phone"]          # firmographic fallback ran
    skips = [t for t in res.provider_trace if t["outcome"] == "skip"]
    assert skips and "entity name" in skips[0]["detail"]


def test_provider_trace_captures_error_lines(monkeypatch):
    _trace.reset()

    def boom(*a, **k):
        raise live.ProviderError("POST https://x -> HTTP 401: bad key")

    monkeypatch.setattr(live, "_post", boom)
    assert live.BatchDataSkipTrace("key").trace_person("Jane Doe", None, "VA") is None
    lines = _trace.snapshot()
    assert lines and lines[0]["outcome"] == "error"
    assert "HTTP 401" in lines[0]["detail"]


def test_apollo_tries_api_v1_first(monkeypatch):
    urls = []

    def fake_request(method, url, **k):
        urls.append(url)
        return {"organizations": [{"name": "Harbor Group", "phone": "7575550100",
                                   "id": "o1"}]}

    monkeypatch.setattr(live, "_request", fake_request)
    bc = live.ApolloFirmographic("key").enrich_company("Harbor Group")
    assert bc is not None and bc.phone == "+17575550100"
    assert urls == ["https://api.apollo.io/api/v1/mixed_companies/search"]


def test_cobalt_polls_retry_id(monkeypatch):
    seq = [{"retryId": "r-1"},
           {"results": [{"title": "Ghent Holdings LLC", "sosId": "S1",
                         "officers": [{"name": "Alice Ghent"}]}]}]
    calls = []

    def fake_get(url, **k):
        calls.append(k.get("params"))
        return seq[len(calls) - 1]

    monkeypatch.setattr(live, "_get", fake_get)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    r = live.CobaltSOS("key").resolve_entity("Ghent Holdings LLC", "VA")
    assert r is not None and r.officers == ["Alice Ghent"]
    assert calls[1] == {"retryId": "r-1"}             # polled, not re-searched


def test_trestle_real_contact_boolean_name_match(monkeypatch):
    payload = {"request_id": "rc-1",
               "phone": {"is_valid": True, "line_type": "Mobile",
                         "name_match": True, "contact_grade": "A"}}
    urls = []

    def fake_get(url, **k):
        urls.append(url)
        return payload

    monkeypatch.setattr(live, "_get", fake_get)
    v = live.TrestleValidation("key").validate_phone("+17575550142", "Jane Doe")
    assert urls[0].endswith("/1.1/real_contact")
    assert v.name_match == 1.0 and v.line_type == "mobile"   # grade A reachable


def test_validation_outage_keeps_phone_but_never_callable(monkeypatch):
    class _OkTier:
        name = "batchdata"
        tier = 2

        def trace_person(self, full_name, address_hint, state):
            return prov.TraceCandidate(
                full_name=full_name, phones=["+17575550142"], emails=[],
                addresses=[], relatives=[], age_band=None, deceased=False,
                vendor=self.name, query_id="q", cost_usd=0.12)

    class _DownValidation:
        name = "trestle"

        def validate_phone(self, e164, expected_name):
            raise live.ProviderError("GET https://x -> HTTP 500: down")

        def validate_email(self, addr):
            raise live.ProviderError("down")

    reg = prov.ProviderRegistry(
        sos=prov.MockSOS(), trace_waterfall=[_OkTier()],
        validation=_DownValidation(), firmographic=None,
        status={"sos": "mock", "skiptrace": "live", "validation": "live"})
    res = pipeline.resolve_contacts(
        "org", {"property_id": "P2", "owner": "Jane Doe", "state": "VA"},
        registry=reg, persist=False)
    phones = res.pocs[0]["phones"]
    assert phones and phones[0]["e164"] == "+17575550142"
    assert phones[0]["callable"] is False
    assert "validation unavailable" in phones[0]["reason"]
