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


# ------------------------------------------- S4 targeting for principals

class _RecordingTier:
    """A trace tier that records how it was called and returns nothing."""
    name = "recorder"
    tier = 2

    def __init__(self):
        self.calls = []

    def trace_person(self, full_name, address_hint, state):
        self.calls.append({"name": full_name, "addr": address_hint, "state": state})
        return None


class _StubSOS:
    def __init__(self, jurisdiction):
        self._j = jurisdiction

    def resolve_entity(self, entity_name, state):
        return providers.SOSResult(
            entity_name=entity_name, jurisdiction=self._j, filing_id="1",
            officers=["Grant Cardone"], registered_agent="", confidence=0.86,
            vendor="cobalt", query_id="q", cost_usd=1.0)


class _Val:
    def validate_phone(self, *a, **k): raise AssertionError("no phones here")
    def validate_email(self, *a, **k): raise AssertionError("no emails here")


def _reg(rec, sos):
    return providers.ProviderRegistry(
        sos=sos, trace_waterfall=[rec], validation=_Val(),
        status={"sos": "live (cobalt)", "skiptrace": "live (batchdata)",
                "validation": "live (trestle)"})


def test_a_pierced_principal_is_traced_by_name_in_the_entity_jurisdiction():
    """The Grant Cardone bug: searching a fund's principal against the
    PROPERTY address finds nothing. A pierced principal must be traced by
    name in the entity's home state, not pinned to the property address."""
    rec = _RecordingTier()
    prop = dict(property_id="8R-FL-1", owner="Fountain View Circle LLC",
                state="FL", owner_address="123 Naples Blvd, Naples FL")
    pipeline.resolve_contacts("org", prop, registry=_reg(rec, _StubSOS("FL")),
                              persist=False)
    assert rec.calls, "the trace tier was never called"
    call = rec.calls[0]
    assert call["name"] == "Grant Cardone"
    assert call["addr"] is None, "principal must NOT be pinned to the property address"
    assert call["state"] == "FL"


def test_an_individual_owner_is_still_traced_at_the_property_address():
    """The change must not regress the common case: a deed's own owner is
    best found AT the property mailing address."""
    rec = _RecordingTier()
    prop = dict(property_id="8R-FL-2", owner="Robert Cleghorn",
                state="VA", owner_address="900 Colonial Ave, Norfolk VA")

    class _NoPierce:
        def resolve_entity(self, *a): return None
    pipeline.resolve_contacts("org", prop, registry=_reg(rec, _NoPierce()),
                              persist=False)
    call = rec.calls[0]
    assert call["name"] == "Robert Cleghorn"
    assert call["addr"] == "900 Colonial Ave, Norfolk VA"
    assert call["state"] == "VA"


# ------------------------------------------ unpierced-entity labeling

def test_cobalt_reads_a_scalar_principal_field(monkeypatch):
    """Cobalt returns officers as an array on some states/plans and a scalar
    principalName on others - both must resolve."""
    sos = _cobalt(monkeypatch, {"results": [{
        "title": "SCALAR LLC", "sosId": "9", "principalName": "Dana Fox"}]})
    r = sos.resolve_entity("Scalar LLC", "GA")
    assert r.officers == ["Dana Fox"]


def test_an_unpierceable_llc_is_labeled_entity_not_principal():
    """100 PRINCE AVENUE LLC: GA published no member, so the 'principal' was
    just the LLC name repeated. That must read as an entity we could not
    pierce, not a resolved principal with empty contacts."""
    rec = _RecordingTier()      # returns no candidate

    class _NoOfficer:
        def resolve_entity(self, entity_name, state):
            return providers.SOSResult(
                entity_name=entity_name, jurisdiction="GA", filing_id="18097298",
                officers=[], registered_agent="RAM Partners LLC",
                confidence=0.4, vendor="cobalt", query_id="q", cost_usd=1.0)

    prop = dict(property_id="8R-GA-1", owner="100 Prince Avenue LLC",
                state="GA", owner_address="245 E Broad St STE C, Greenville SC")
    res = pipeline.resolve_contacts("org", prop,
                                    registry=_reg(rec, _NoOfficer()),
                                    persist=False)
    owner = res.pocs[0]
    assert owner["role"] == "entity_unpierced"
    assert "no individual" in owner["person"]["unpierced_note"]


def test_a_pierced_human_is_still_a_principal():
    rec = _RecordingTier()
    prop = dict(property_id="8R-GA-2", owner="Real Owner LLC", state="GA",
                owner_address="1 Main St, Atlanta GA")
    res = pipeline.resolve_contacts("org", prop,
                                    registry=_reg(rec, _StubSOS("GA")),
                                    persist=False)
    assert res.pocs[0]["role"] == "principal"     # StubSOS names Grant Cardone


# ------------------------------------------ firmographic enrichment

def _reg_with_firm(rec, sos, firm):
    r = providers.ProviderRegistry(
        sos=sos, trace_waterfall=[rec], validation=_Val(),
        firmographic=firm,
        status={"sos": "live (cobalt)", "skiptrace": "live (batchdata)",
                "validation": "live (trestle)", "firmographic": "mock"})
    return r


def test_an_unpierced_entity_gets_a_business_contact():
    """The realistic contact for an institutional owner: the firm's line."""
    rec = _RecordingTier()

    class _NoOfficer:
        def resolve_entity(self, entity_name, state):
            return providers.SOSResult(
                entity_name=entity_name, jurisdiction="GA", filing_id="1",
                officers=[], registered_agent="RAM Partners LLC",
                confidence=0.4, vendor="cobalt", query_id="q", cost_usd=1.0)

    prop = dict(property_id="8R-GA-9", owner="100 Prince Avenue LLC",
                state="GA", city="Athens", management_company="RAM Partners LLC")
    res = pipeline.resolve_contacts(
        "org", prop,
        registry=_reg_with_firm(rec, _NoOfficer(), providers.MockFirmographic()),
        persist=False)
    owner = next(p for p in res.pocs if p["role"] == "entity_unpierced")
    bc = owner.get("business_contact")
    assert bc and bc["phone"] and bc["email"] and bc["website"]


def test_the_management_company_poc_is_enriched():
    rec = _RecordingTier()

    class _NoPierce:
        def resolve_entity(self, *a): return None
    prop = dict(property_id="8R-1", owner="Jane Roe", state="GA",
                management_company="RAM Partners LLC")
    res = pipeline.resolve_contacts(
        "org", prop,
        registry=_reg_with_firm(rec, _NoPierce(), providers.MockFirmographic()),
        persist=False)
    pm = next(p for p in res.pocs if p["role"] == "pm")
    assert pm.get("business_contact"), "the PM should carry a business contact"


def test_apollo_uses_get_with_query_params(monkeypatch):
    """Apollo org-enrich is a GET with match params in the query string.
    The path prefix is SELF-VERIFYING: docs have shipped both /api/v1
    (current docs.apollo.io) and /v1 (older reference), this repo has been
    flipped between them twice on doc reads alone, and neither was ever
    live-confirmed - so the adapter tries /api/v1, falls back to /v1 on a
    404, and remembers the one that answered (owner 2026-08-11: research
    the method, don't guess-and-grind)."""
    seen = {"urls": [], "auth": None, "params": None}

    def fake_request(method, url, *, headers, json=None, params=None):
        assert method == "GET"
        seen["urls"].append(url)
        seen["params"] = params
        seen["auth"] = headers.get("X-Api-Key")
        if "/api/v1/" in url:
            raise live.ProviderError(f"GET {url} -> HTTP 404: unknown route")
        return {"organization": {
            "name": "RAM Partners LLC", "phone": "(404) 555-1212",
            "website_url": "https://rampartners.com"}}

    monkeypatch.setattr(live, "_request", fake_request)
    apollo = live.ApolloFirmographic("k")
    monkeypatch.setattr(apollo, "_search_org", lambda c: None)  # force enrich
    bc = apollo.enrich_company("RAM Partners LLC")
    assert ["/api/v1/" in u for u in seen["urls"]] == [True, False]
    assert all(u.endswith("/organizations/enrich") for u in seen["urls"])
    assert apollo._prefix == "/v1"                 # remembered for next call
    assert seen["params"] == {"name": "RAM Partners LLC"}
    assert seen["auth"] == "k"
    assert bc.phone == "+14045551212"
    assert bc.website.endswith("rampartners.com")


def test_apollo_prefers_api_v1_when_it_answers(monkeypatch):
    urls = []

    def fake_request(method, url, *, headers, json=None, params=None):
        urls.append(url)
        return {"organizations": [{"name": "Harbor Group",
                                   "phone": "7575550100", "id": "o1"}]}

    monkeypatch.setattr(live, "_request", fake_request)
    apollo = live.ApolloFirmographic("k")
    bc = apollo.enrich_company("Harbor Group")
    assert bc is not None and bc.phone == "+17575550100"
    assert urls == ["https://api.apollo.io/api/v1/mixed_companies/search"]
    assert apollo._prefix == "/api/v1"


def test_apollo_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(live, "_request",
                        lambda *a, **k: {"organization": {}})
    assert live.ApolloFirmographic("k").enrich_company("Nobody LLC") is None


# ------------------------------------------ Apollo search-first + empty note

def test_apollo_searches_by_name_first(monkeypatch):
    """A bare company name ('Nexus Management Company') should hit the
    name-SEARCH endpoint, not just domain-based enrich."""
    seen = {}

    def fake_request(method, url, *, headers, json=None, params=None):
        assert method == "POST"
        seen["url"] = url
        seen["params"] = params
        return {"organizations": [{
            "name": "Nexus Management Company", "phone": "(804) 555-0100",
            "website_url": "https://nexusmgmt.com"}]}

    monkeypatch.setattr(live, "_request", fake_request)
    bc = live.ApolloFirmographic("k").enrich_company("Nexus Management Company")
    assert seen["url"].endswith("/mixed_companies/search")
    assert seen["params"]["q_organization_name"] == "Nexus Management Company"
    assert bc.phone == "+18045550100"
    assert bc.company == "Nexus Management Company"


def test_apollo_falls_back_to_enrich_when_search_is_empty(monkeypatch):
    def fake_request(method, url, *, headers, json=None, params=None):
        if "mixed_companies" in url:
            return {"organizations": []}
        return {"organization": {"name": "RAM Partners LLC",
                                 "website_url": "https://rampartners.com"}}

    monkeypatch.setattr(live, "_request", fake_request)
    bc = live.ApolloFirmographic("k").enrich_company("RAM Partners LLC")
    assert bc and bc.website.endswith("rampartners.com")


def test_apollo_all_empty_is_none(monkeypatch):
    def fake_request(method, url, *, headers, json=None, params=None):
        return ({"organizations": []} if "mixed_companies" in url
                else {"organization": {}})

    monkeypatch.setattr(live, "_request", fake_request)
    assert live.ApolloFirmographic("k").enrich_company("Nobody LLC") is None


def test_a_live_firmographic_miss_leaves_a_legible_note():
    """The 'nothing coming back' ambiguity: a live provider that finds
    nothing must annotate the POC, not silently omit the block."""
    rec = _RecordingTier()

    class _NoOfficer:
        def resolve_entity(self, entity_name, state):
            return providers.SOSResult(
                entity_name=entity_name, jurisdiction="VA", filing_id="1",
                officers=[], registered_agent="", confidence=0.4,
                vendor="cobalt", query_id="q", cost_usd=1.0)

    class _EmptyFirm:
        name = "apollo"
        def enrich_company(self, *a, **k): return None

    prop = dict(property_id="8R-VA-9", owner="Spada III LLC", state="VA",
                management_company="Nexus Management Company")
    res = pipeline.resolve_contacts(
        "org", prop,
        registry=_reg_with_firm(rec, _NoOfficer(), _EmptyFirm()),
        persist=False)
    pm = next(p for p in res.pocs if p["role"] == "pm")
    assert "no business contact found (apollo)" in pm["business_contact_note"]
