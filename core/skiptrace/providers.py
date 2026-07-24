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
    status: dict = field(default_factory=dict)  # {'sos': 'live'|'mock', ...}


def _mock_registry() -> ProviderRegistry:
    return ProviderRegistry(
        sos=MockSOS(),
        trace_waterfall=sorted(
            [MockTier1Append(), MockTier2BatchData(), MockTier3Enformion()],
            key=lambda p: p.tier),
        validation=MockTrestle(),
        status={"sos": "mock", "skiptrace": "mock", "validation": "mock"},
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

    # SOS (entity piercing): VA SCC if a token is set, else mock.
    va_token = os.environ.get("VA_SCC_API_TOKEN")
    if va_token:
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

    return ProviderRegistry(sos=sos, trace_waterfall=waterfall,
                            validation=validation, status=status)
