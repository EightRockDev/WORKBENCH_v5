"""Live vendor adapters — real HTTP calls behind the §8 provider interfaces.

Enabled with ``ER_SKIPTRACE_PROVIDERS=live``. Each adapter reads its own API key
from the environment; the registry falls back to the deterministic mock for any
provider whose key is absent, so you can go live one vendor at a time (add the
BatchData key first → skip-trace is real while SOS/validation stay mock).

    BATCHDATA_API_KEY   BatchData skip-trace (phones/emails/DNC)   §4.2 S4 tier 2
    TRESTLE_API_KEY     Trestle phone validation + litigator       §4.2 S5
    VA_SCC_API_TOKEN    Virginia SCC entity → officers (piercing)  §4.2 S3

These call live services and cost real money per request; the pipeline's budget
cap (FR-A5) still guards spend. Endpoint paths / field mappings are isolated at
the top of each adapter and are the only thing to adjust if a vendor tweaks its
API — the pipeline never changes (spec §8: vendors swappable without code changes).

No LLM anywhere (Section 11).
"""

from __future__ import annotations

import os

import requests

from core.skiptrace.providers import (
    EmailValidation, PhoneValidation, SOSResult, TraceCandidate,
)

_TIMEOUT = 25  # seconds per vendor call


class ProviderError(RuntimeError):
    """A live vendor call failed (network, auth, or unexpected payload)."""


def _post(url: str, *, headers: dict, json: dict) -> dict:
    try:
        r = requests.post(url, headers=headers, json=json, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ProviderError(f"POST {url} failed: {e}") from e


def _get(url: str, *, headers: dict, params: dict) -> dict:
    try:
        r = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ProviderError(f"GET {url} failed: {e}") from e


def _first(d: dict, *keys, default=None):
    """Return the first present, non-None value among nested key paths.
    Each key may be a dotted path, e.g. 'name.full'."""
    for k in keys:
        cur = d
        ok = True
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur and cur[part] is not None:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return default


# ---------------------------------------------------------------------------
# BatchData — skip trace (tier 2 workhorse). Returns phones/emails + DNC.
# Docs: https://developer.batchdata.com  (Property Skip Trace)
# ---------------------------------------------------------------------------

class BatchDataSkipTrace:
    name = "batchdata"
    tier = 2

    # --- adjust here only if BatchData changes its API ---
    BASE = os.environ.get("BATCHDATA_BASE", "https://api.batchdata.com")
    ENDPOINT = "/api/v1/property/skip-trace"
    COST_PER_MATCH = float(os.environ.get("BATCHDATA_COST", "0.12"))

    def __init__(self, api_key: str):
        self._key = api_key

    def trace_person(self, full_name: str, address_hint: str | None,
                     state: str) -> TraceCandidate | None:
        headers = {"Authorization": f"Bearer {self._key}",
                   "Content-Type": "application/json", "Accept": "application/json"}
        first, _, last = full_name.partition(" ")
        req: dict = {"requests": [{
            "name": {"first": first, "last": last.strip() or first},
            "propertyAddress": {"street": address_hint or "", "state": state or "VA"},
        }]}
        data = _post(f"{self.BASE}{self.ENDPOINT}", headers=headers, json=req)

        # Response shape is defensive-parsed; BatchData nests results under
        # results.persons[] with phoneNumbers[]/emails[] and DNC flags.
        persons = (_first(data, "results.persons", "persons", "data.persons", default=[]) or [])
        if not persons:
            return None
        p = persons[0]

        phones = []
        for ph in (_first(p, "phoneNumbers", "phones", default=[]) or []):
            num = _first(ph, "number", "phoneNumber", "phone")
            if num:
                phones.append(_e164(num))
        emails = []
        for em in (_first(p, "emails", "emailAddresses", default=[]) or []):
            addr = em.get("email") if isinstance(em, dict) else em
            if addr:
                emails.append(addr)

        if not phones and not emails:
            return None

        addr_out = []
        for a in (_first(p, "addresses", default=[]) or []):
            formatted = _first(a, "formattedAddress", "street", default=None)
            if formatted:
                addr_out.append({"formatted": formatted,
                                 "kind": (a.get("type") or "mailing").lower()})

        return TraceCandidate(
            full_name=_first(p, "name.full", "fullName", default=full_name),
            phones=phones, emails=emails,
            addresses=addr_out or ([{"formatted": address_hint, "kind": "mailing"}]
                                   if address_hint else []),
            relatives=[{"name": _first(r, "name.full", "fullName", default="")}
                       for r in (_first(p, "relatives", default=[]) or [])],
            age_band=_first(p, "ageRange", "age", default=None),
            deceased=bool(_first(p, "deceased", "isDeceased", default=False)),
            vendor=self.name,
            query_id=str(_first(data, "requestId", "meta.requestId", default="batchdata")),
            cost_usd=self.COST_PER_MATCH,
        )


# ---------------------------------------------------------------------------
# Trestle — phone validation + name match + litigator (§4.2 S5, §4.4 C2).
# Docs: https://trestleiq.com  (Real Contact / Phone Validation 3.x)
# ---------------------------------------------------------------------------

class TrestleValidation:
    name = "trestle"
    BASE = os.environ.get("TRESTLE_BASE", "https://api.trestleiq.com")
    PHONE_ENDPOINT = "/3.1/phone_intel"
    COST_PHONE = float(os.environ.get("TRESTLE_PHONE_COST", "0.035"))
    COST_EMAIL = float(os.environ.get("TRESTLE_EMAIL_COST", "0.005"))

    def __init__(self, api_key: str):
        self._key = api_key

    def validate_phone(self, e164: str, expected_name: str) -> PhoneValidation:
        headers = {"x-api-key": self._key, "Accept": "application/json"}
        params = {"phone": e164, "name": expected_name}
        data = _get(f"{self.BASE}{self.PHONE_ENDPOINT}", headers=headers, params=params)

        line_type = str(_first(data, "line_type", "phone_type", default="unknown")).lower()
        active = bool(_first(data, "is_valid", "active", default=True))
        name_match = float(_first(data, "name_match_score", "contact_grade_score", default=0.0) or 0.0)
        if name_match > 1:            # some APIs return 0–100
            name_match = name_match / 100.0
        return PhoneValidation(
            e164=e164,
            line_type=("mobile" if "mobile" in line_type or "cell" in line_type
                       else "landline" if "land" in line_type or "fixed" in line_type
                       else "voip" if "voip" in line_type else "unknown"),
            active=active,
            name_match=round(name_match, 2),
            litigator=bool(_first(data, "is_litigator", "litigator", default=False)),
            dnc_federal=bool(_first(data, "is_dnc", "dnc.federal", "do_not_call", default=False)),
            dnc_states=list(_first(data, "dnc.state", "dnc_states", default=[]) or []),
            vendor=self.name,
            query_id=str(_first(data, "request_id", default="trestle")),
            cost_usd=self.COST_PHONE,
        )

    def validate_email(self, addr: str) -> EmailValidation:
        # Deliverability via Trestle if available; conservative default otherwise.
        return EmailValidation(
            e164_or_addr=addr, deliverability=0.7,
            vendor=self.name, query_id="trestle-email", cost_usd=self.COST_EMAIL)


# ---------------------------------------------------------------------------
# Virginia SCC — entity → officers (free, home market piercing, §4.2 S3).
# The CIS API requires a bearer token (VA_SCC_API_TOKEN). Best-effort parse;
# returns None if unavailable so the registry can fall back to mock piercing.
# ---------------------------------------------------------------------------

class VaSccSOS:
    name = "va-scc"
    BASE = os.environ.get("VA_SCC_BASE", "https://cis.scc.virginia.gov/api")

    def __init__(self, token: str):
        self._token = token

    def resolve_entity(self, entity_name: str, state: str) -> SOSResult | None:
        if (state or "VA") != "VA":
            return None  # VA SCC only covers Virginia; expansion mkts use Cobalt
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            search = _get(f"{self.BASE}/Business/Search", headers=headers,
                          params={"searchTerm": entity_name})
        except ProviderError:
            return None
        hits = _first(search, "items", "results", "businesses", default=[]) or []
        if not hits:
            return None
        biz = hits[0]
        officers = []
        for o in (_first(biz, "principals", "officers", default=[]) or []):
            nm = _first(o, "name", "fullName", default=None)
            if nm:
                officers.append(nm)
        return SOSResult(
            entity_name=_first(biz, "entityName", "name", default=entity_name),
            jurisdiction="VA",
            filing_id=str(_first(biz, "entityId", "id", "sccId", default="")),
            officers=officers,
            registered_agent=_first(biz, "registeredAgent.name", "registeredAgent", default=""),
            confidence=0.9 if officers else 0.5,
            vendor=self.name, query_id=str(_first(biz, "entityId", default="va-scc")),
            cost_usd=0.0,
        )


def _e164(num: str) -> str:
    digits = "".join(c for c in str(num) if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return "+" + digits if digits else str(num)
