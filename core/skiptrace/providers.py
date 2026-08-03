"""Vendor provider interfaces + deterministic mock adapters (spec §8, §4.2).

Architecture rule (spec §8 "Vendor abstraction"): every external data call goes
through a provider interface — ``SOSProvider``, ``SkipTraceProvider``,
``ValidationProvider`` — with per-vendor adapters, per-call cost accounting, and
a waterfall configured in data, so vendors are swappable without code changes
when pricing or quality shifts.

Until real API keys are provisioned (BatchData, Trestle, Cobalt, VA SCC), the
registry serves **deterministic mock adapters**: results are derived from a
stable hash of the input, so the pipeline is fully exercisable, testable, and
repeatable with zero vendor spend. Swap to live adapters via
``ER_SKIPTRACE_PROVIDERS=live`` once adapters + keys exist — the pipeline code
does not change.

Every provider result carries ``cost_usd`` and ``query_id`` so S7 can persist
provenance (FR-A7) and the spend ledger stays accurate to the cent (AC-A4).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------------------
# Result shapes (plain dataclasses; persisted as JSONB per the §4.5 contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SOSResult:
    """One state-registry resolution hop (§4.2 S3)."""

    entity_name: str
    jurisdiction: str
    filing_id: str
    officers: list[str]
    registered_agent: str
    confidence: float
    vendor: str
    query_id: str
    cost_usd: float


@dataclass(frozen=True)
class TraceCandidate:
    """A person-trace hit from one waterfall tier (§4.2 S4), pre-validation."""

    full_name: str
    phones: list[str]
    emails: list[str]
    addresses: list[dict]
    relatives: list[dict]
    age_band: str | None
    deceased: bool
    vendor: str
    query_id: str
    cost_usd: float


@dataclass(frozen=True)
class PhoneValidation:
    """Trestle-style phone validation + grading inputs (§4.2 S5)."""

    e164: str
    line_type: str          # 'mobile' | 'landline' | 'voip' | 'unknown'
    active: bool
    name_match: float       # 0..1
    litigator: bool
    dnc_federal: bool
    dnc_states: list[str]
    vendor: str
    query_id: str
    cost_usd: float


@dataclass(frozen=True)
class EmailValidation:
    e164_or_addr: str
    deliverability: float   # 0..1
    vendor: str
    query_id: str
    cost_usd: float


@dataclass(frozen=True)
class BusinessContact:
    """Firmographic contact for a COMPANY (§4 enrichment), not an individual.

    The realistic contact for an institutional owner whose LLC names no
    member: the management company / sponsor's main line, website, and a
    best-available named contact (acquisitions/asset-management). This is a
    directory/firmographic lookup, not skip trace, and it is NOT compliance-
    stamped for auto-dialing - a business main line is a manual call.
    """
    company: str
    phone: str
    email: str
    website: str
    contact_name: str
    contact_title: str
    vendor: str
    query_id: str
    cost_usd: float


# ---------------------------------------------------------------------------
# Interfaces (Protocol = structural typing; adapters need no base class)
# ---------------------------------------------------------------------------


class SOSProvider(Protocol):
    name: str

    def resolve_entity(self, entity_name: str, state: str) -> SOSResult | None: ...


class SkipTraceProvider(Protocol):
    name: str
    tier: int               # waterfall position: 1 cheapest first (§4.2 S4)

    def trace_person(self, full_name: str, address_hint: str | None,
                     state: str) -> TraceCandidate | None: ...


class ValidationProvider(Protocol):
    name: str

    def validate_phone(self, e164: str, expected_name: str) -> PhoneValidation: ...
    def validate_email(self, addr: str) -> EmailValidation: ...


# ---------------------------------------------------------------------------
# Deterministic mock adapters
# ---------------------------------------------------------------------------

def _h(seed: str) -> int:
    """Stable integer from a string — same input, same 'vendor response'."""
    return int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16)


def _pick(seed: str, options: list):
    return options[_h(seed) % len(options)]


_FIRST = ["Robert", "Maria", "James", "Linda", "David", "Susan", "Michael", "Karen"]
_LAST_SUFFIXES = ["", " Jr.", ""]


def _principal_name_for(entity: str) -> str:
    """Derive a stable principal name from an entity name — e.g.
    'Cleghorn Capital LLC' -> 'Robert Cleghorn'."""
    words = [w for w in entity.replace(",", " ").split()
             if w.lower() not in {"llc", "lp", "llp", "inc", "inc.", "corp", "corp.",
                                  "capital", "holdings", "partners", "investors",
                                  "properties", "group", "trust", "the", "co", "co."}]
    surname = (words[0] if words else "Owner").strip(".").title()
    first = _pick(entity + "|first", _FIRST)
    return f"{first} {surname}{_pick(entity + '|sfx', _LAST_SUFFIXES)}"


class MockSOS:
    """Mock Virginia SCC / Cobalt SOS adapter (§4.2 S3). VA SCC is free."""

    name = "mock-va-scc"

    def resolve_entity(self, entity_name: str, state: str) -> SOSResult | None:
        if not entity_name:
            return None
        principal = _principal_name_for(entity_name)
        qid = f"sos-{_h(entity_name):x}"
        return SOSResult(
            entity_name=entity_name,
            jurisdiction=state or "VA",
            filing_id=f"{state or 'VA'}-{_h(entity_name) % 10_000_000:07d}",
            officers=[principal],
            registered_agent=_pick(entity_name + "|agent",
                                   [principal, "Registered Agents Inc.", "CT Corporation"]),
            confidence=0.85 + (_h(entity_name) % 10) / 100.0,
            vendor=self.name,
            query_id=qid,
            # VA SCC free in home market; expansion-market SOS costs $0.03-$2.00
            cost_usd=0.0 if (state or "VA") == "VA" else 0.50,
        )


class MockFirmographic:
    """Mock business-contact enrichment (deterministic, $0). Real vendor is
    Apollo/PDL, keyed in get_registry()."""

    name = "mock-firmographic"

    def enrich_company(self, company: str, city: str | None = None,
                       state: str | None = None) -> BusinessContact | None:
        if not company:
            return None
        base = _h("firm" + company)
        slug = "".join(c for c in company.lower() if c.isalnum())[:18] or "firm"
        contact = _principal_name_for(company + "|amgr")
        return BusinessContact(
            company=company,
            phone=f"+1{200 + base % 700:03d}{base % 900 + 100:03d}"
                  f"{base % 9000 + 1000:04d}",
            email=f"acquisitions@{slug}.com",
            website=f"https://{slug}.com",
            contact_name=contact,
            contact_title=_pick(company + "|title",
                                ["Acquisitions", "Asset Management",
                                 "Managing Principal", "Director of Investments"]),
            vendor=self.name, query_id=f"firm-{base:x}", cost_usd=0.0)


class _SOSWaterfall:
    """Try SOS providers in order, first non-None wins.

    Lets VA SCC (free, Virginia) run ahead of Cobalt (paid, all states):
    a Virginia entity resolves for free, and everything VA SCC can't answer -
    including every out-of-state entity as the metros expand - falls through
    to Cobalt. A provider that raises is skipped, never fatal.
    """

    name = "sos-waterfall"

    def __init__(self, providers):
        self._providers = list(providers)

    def resolve_entity(self, entity_name: str, state: str):
        for p in self._providers:
            try:
                r = p.resolve_entity(entity_name, state)
            except Exception:      # noqa: BLE001 - one vendor down never blocks
                continue
            if r is not None and r.officers:
                return r
        return None


class MockTier1Append:
    """Mock Datazapp bulk append — cheap, low hit-rate tier (§4.3: $0.01–$0.03)."""

    name = "mock-datazapp"
    tier = 1

    def trace_person(self, full_name, address_hint, state):
        # ~40% hit rate, deterministic per name
        if _h(self.name + full_name) % 10 >= 4:
            return None
        return _mk_candidate(full_name, address_hint, state, self.name, n_phones=1,
                             n_emails=0, cost=0.02)


class MockTier2BatchData:
    """Mock BatchData skip trace — the workhorse tier (§4.3: $0.07–$0.18/match)."""

    name = "mock-batchdata"
    tier = 2

    def trace_person(self, full_name, address_hint, state):
        # ~80% hit rate (spec: 75–85% phone hit rate)
        if _h(self.name + full_name) % 10 >= 8:
            return None
        return _mk_candidate(full_name, address_hint, state, self.name, n_phones=2,
                             n_emails=1, cost=0.12)


class MockTier3Enformion:
    """Mock Enformion/Endato deep trace — fallback tier (§4.3: $0.01–$0.25)."""

    name = "mock-enformion"
    tier = 3

    def trace_person(self, full_name, address_hint, state):
        return _mk_candidate(full_name, address_hint, state, self.name, n_phones=2,
                             n_emails=1, cost=0.25, with_relatives=True)


def _mk_candidate(full_name, address_hint, state, vendor, *, n_phones, n_emails,
                  cost, with_relatives=False) -> TraceCandidate:
    base = _h(vendor + full_name)
    phones = [f"+1757{(base + i * 7919) % 10_000_000:07d}" for i in range(n_phones)]
    handle = full_name.lower().replace(" ", ".").replace("..", ".")
    emails = [f"{handle}@example-mail.com"][:n_emails]
    relatives = ([{"name": _pick(full_name + "|rel", _FIRST) + " " + full_name.split()[-1],
                   "relation": _pick(full_name + "|relkind", ["spouse", "sibling", "child"])}]
                 if with_relatives else [])
    return TraceCandidate(
        full_name=full_name,
        phones=phones,
        emails=emails,
        addresses=[{"formatted": address_hint or f"PO Box {base % 9000 + 100}, {state or 'VA'}",
                    "kind": "mailing"}],
        relatives=relatives,
        age_band=_pick(full_name + "|age", ["35-44", "45-54", "55-64", "65-74"]),
        deceased=(_h(full_name + "|dead") % 50 == 0),   # ~2%
        vendor=vendor,
        query_id=f"{vendor}-{base:x}",
        cost_usd=cost,
    )


class MockTrestle:
    """Mock Trestle validation (§4.2 S5): line type, activity, name match,
    litigator flag (+$0.005), DNC flags. §4.3: $0.02–$0.04 per contact."""

    name = "mock-trestle"

    def validate_phone(self, e164: str, expected_name: str) -> PhoneValidation:
        base = _h(e164 + expected_name)
        return PhoneValidation(
            e164=e164,
            line_type=_pick(e164 + "|lt", ["mobile", "mobile", "mobile", "landline", "voip"]),
            active=(base % 10) < 8,                       # 80% active
            name_match=0.55 + (base % 45) / 100.0,        # 0.55–0.99
            litigator=(base % 40 == 0),                   # ~2.5%
            dnc_federal=(base % 5 == 0),                  # ~20% on federal DNC
            dnc_states=(["TX"] if base % 17 == 0 else []),
            vendor=self.name,
            query_id=f"trestle-{base:x}",
            cost_usd=0.035,                               # validation + litigator add-on
        )

    def validate_email(self, addr: str) -> EmailValidation:
        base = _h(addr)
        return EmailValidation(
            e164_or_addr=addr,
            deliverability=0.5 + (base % 50) / 100.0,
            vendor=self.name,
            query_id=f"trestle-em-{base:x}",
            cost_usd=0.005,
        )


# ---------------------------------------------------------------------------
# Registry — waterfall config lives in data, not code (§8)
# ---------------------------------------------------------------------------


@dataclass
class ProviderRegistry:
    sos: SOSProvider
    trace_waterfall: list[SkipTraceProvider] = field(default_factory=list)
    validation: ValidationProvider = None  # type: ignore[assignment]
    firmographic: object = None            # BusinessContact enrichment (opt.)
    status: dict = field(default_factory=dict)  # {'sos': 'live'|'mock', ...}


def _mock_registry() -> ProviderRegistry:
    return ProviderRegistry(
        sos=MockSOS(),
        trace_waterfall=sorted(
            [MockTier1Append(), MockTier2BatchData(), MockTier3Enformion()],
            key=lambda p: p.tier),
        validation=MockTrestle(),
        firmographic=MockFirmographic(),
        status={"sos": "mock", "skiptrace": "mock", "validation": "mock",
                "firmographic": "mock"},
    )


def get_registry() -> ProviderRegistry:
    """Resolve the active provider set.

    ``ER_SKIPTRACE_PROVIDERS=live`` uses real vendor adapters where a key is
    present and falls back to the deterministic mock **per provider** — so you
    can go live one vendor at a time. Default (unset/``mock``) is all-mock.
    """
    mode = os.environ.get("ER_SKIPTRACE_PROVIDERS", "mock").lower()
    if mode != "live":
        return _mock_registry()

    from core.skiptrace import live  # local import: requests only needed live

    status: dict = {}

    # SOS (entity piercing). Cobalt is the general-purpose, all-states vendor
    # (self-serve key); VA SCC is a free Virginia-only supplement. With both
    # keyed, try VA SCC first for a VA entity and fall back to Cobalt - keeps
    # Virginia free without losing the other 49 states. Neither keyed => mock,
    # and the pipeline marks a mock-pierced contact non-callable (AC-A3).
    va_token = os.environ.get("VA_SCC_API_TOKEN")
    cobalt_key = os.environ.get("COBALT_API_KEY")
    if cobalt_key and va_token:
        sos = _SOSWaterfall([live.VaSccSOS(va_token), live.CobaltSOS(cobalt_key)])
        status["sos"] = "live (va-scc + cobalt)"
    elif cobalt_key:
        sos, status["sos"] = live.CobaltSOS(cobalt_key), "live (cobalt)"
    elif va_token:
        sos, status["sos"] = live.VaSccSOS(va_token), "live (va-scc)"
    else:
        sos, status["sos"] = MockSOS(), "mock"

    # Skip trace waterfall: BatchData live if keyed, else the mock waterfall.
    bd_key = os.environ.get("BATCHDATA_API_KEY")
    if bd_key:
        waterfall = [live.BatchDataSkipTrace(bd_key)]
        status["skiptrace"] = "live (batchdata)"
    else:
        waterfall = sorted([MockTier1Append(), MockTier2BatchData(), MockTier3Enformion()],
                           key=lambda p: p.tier)
        status["skiptrace"] = "mock"

    # Validation/grading: Trestle live if keyed, else mock.
    tr_key = os.environ.get("TRESTLE_API_KEY")
    if tr_key:
        validation, status["validation"] = live.TrestleValidation(tr_key), "live (trestle)"
    else:
        validation, status["validation"] = MockTrestle(), "mock"

    # Firmographic (business contact for a company). Apollo if keyed, else
    # mock. This is the realistic path to an institutional owner whose LLC
    # names no member - the manager/sponsor's main line, not skip trace.
    apollo_key = os.environ.get("APOLLO_API_KEY")
    if apollo_key:
        firmographic, status["firmographic"] = (
            live.ApolloFirmographic(apollo_key), "live (apollo)")
    else:
        firmographic, status["firmographic"] = MockFirmographic(), "mock"

    return ProviderRegistry(sos=sos, trace_waterfall=waterfall,
                            validation=validation, firmographic=firmographic,
                            status=status)
