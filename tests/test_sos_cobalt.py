"""Cobalt SOS adapter, the SOS waterfall, and the mock-pierce safeguard.

Cobalt is the self-serve, all-states entity-piercing vendor (spec §4.2 S3).
Two correctness properties matter most and get real tests:

  * a registered AGENT that is a commercial service (CT Corporation, a law
    firm) is not the beneficial owner and must never be handed to skip trace;
  * an LLC pierced by a MOCK SOS produces a fabricated principal, so its
    phones - real or not - must never be callable. This is the common
    half-live state (BatchData live, SOS still mock) and the trap the owner
    would otherwise walk into: real numbers for a guessed person.
"""

from __future__ import annotations

import datetime as dt

from core.skiptrace import live, pipeline, providers


# ------------------------------------------------------- Cobalt parse

class _FakeGet:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, url, *, headers, params):
        self.calls.append((url, params))
        return self.payload


def _cobalt(monkeypatch, payload):
    monkeypatch.setattr(live, "_get", _FakeGet(payload))
    return live.CobaltSOS("k")


def test_cobalt_extracts_the_officer_as_principal(monkeypatch):
    sos = _cobalt(monkeypatch, {"results": [{
        "title": "BRG LITTLE CREEK LLC", "sosId": "VA-5355481",
        "officers": [{"name": "Linda Brg"}],
        "registeredAgent": {"name": "Linda Brg"}}]})
    r = sos.resolve_entity("Brg Little Creek LLC", "VA")
    assert r is not None
    assert r.officers == ["Linda Brg"]
    assert r.jurisdiction == "VA" and r.vendor == "cobalt"
    assert r.cost_usd > 0


def test_cobalt_requires_a_state(monkeypatch):
    sos = _cobalt(monkeypatch, {"results": []})
    assert sos.resolve_entity("Anything LLC", "") is None


def test_a_commercial_agent_is_not_used_as_principal(monkeypatch):
    """No officers, agent is CT Corporation -> no principal handed onward."""
    sos = _cobalt(monkeypatch, {"results": [{
        "title": "SHELL CO LLC", "sosId": "1",
        "registeredAgent": {"name": "CT Corporation System"}}]})
    r = sos.resolve_entity("Shell Co LLC", "VA")
    assert r is not None and r.officers == []       # agent rejected
    assert r.confidence < 0.5


def test_a_human_agent_is_an_acceptable_fallback_principal(monkeypatch):
    sos = _cobalt(monkeypatch, {"results": [{
        "title": "FAMILY HOLDINGS LLC", "sosId": "2",
        "registeredAgent": {"name": "Robert Cleghorn"}}]})
    r = sos.resolve_entity("Family Holdings LLC", "VA")
    assert r.officers == ["Robert Cleghorn"]


def test_commercial_agent_detector():
    assert live._is_commercial_agent("CT Corporation System")
    assert live._is_commercial_agent("Registered Agents Inc.")
    assert live._is_commercial_agent("Smith & Jones, LLP")
    assert not live._is_commercial_agent("Linda Brg")


# ------------------------------------------------------- SOS waterfall

class _SOS:
    def __init__(self, name, result):
        self.name, self._r = name, result

    def resolve_entity(self, entity_name, state):
        return self._r


def _res(officers):
    return providers.SOSResult(
        entity_name="X LLC", jurisdiction="VA", filing_id="1",
        officers=officers, registered_agent="", confidence=0.9,
        vendor="t", query_id="q", cost_usd=0.0)


def test_waterfall_prefers_the_first_provider_that_names_an_officer():
    wf = providers._SOSWaterfall([_SOS("va", _res(["From VA"])),
                                  _SOS("cobalt", _res(["From Cobalt"]))])
    assert wf.resolve_entity("X LLC", "VA").officers == ["From VA"]


def test_waterfall_falls_through_when_the_first_finds_no_officer():
    wf = providers._SOSWaterfall([_SOS("va", _res([])),          # VA blank
                                  _SOS("cobalt", _res(["Found"]))])
    assert wf.resolve_entity("X LLC", "TX").officers == ["Found"]


def test_waterfall_survives_a_provider_that_raises():
    class _Boom:
        def resolve_entity(self, *a):
            raise RuntimeError("vendor down")
    wf = providers._SOSWaterfall([_Boom(), _SOS("cobalt", _res(["Found"]))])
    assert wf.resolve_entity("X LLC", "VA").officers == ["Found"]


# ------------------------------------------------- mock-pierce safeguard

def _prop(**kw):
    base = dict(property_id="8R-DEMO-9", owner="Ghent Holdings LLC",
                state="VA", owner_address="900 Colonial Ave, Norfolk VA")
    base.update(kw)
    return base


def test_llc_pierced_by_mock_sos_is_never_callable():
    """The trap: BatchData live, SOS mock -> real phones on a guessed
    principal. None may be callable."""
    res = pipeline.resolve_contacts("org", _prop(), persist=False)  # all-mock
    owner = res.pocs[0]
    assert owner.get("entity_chain"), "this owner is an LLC"
    assert owner["phones"], "mock still returns phones to grade"
    assert all(not p["callable"] for p in owner["phones"])
    assert any("principal unverified" in p["reason"] for p in owner["phones"])


def test_an_individual_owner_is_unaffected_by_the_safeguard():
    """No entity chain -> the name is the deed's, not a guess -> the mock-SOS
    gate must not touch it (it would wrongly kill every normal result)."""
    res = pipeline.resolve_contacts("org", _prop(owner="Robert Cleghorn"),
                                    persist=False)
    owner = res.pocs[0]
    assert not owner.get("entity_chain")
    # at least one phone stays callable on the mock individual path
    assert any(p["callable"] for p in owner["phones"])
