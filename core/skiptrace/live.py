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
import re
import time

import requests

from core.skiptrace import trace
from core.skiptrace.providers import (
    EmailValidation, PhoneValidation, SOSResult, TraceCandidate,
)

_TIMEOUT = 25  # seconds per vendor call


class ProviderError(RuntimeError):
    """A live vendor call failed (network, auth, or unexpected payload).

    Carries the HTTP status and the first bytes of the response body —
    vendors put the actionable message there ("invalid api key", "unknown
    endpoint"), and hiding it is why live mode failed silently for weeks.
    """


def _err_detail(r: requests.Response) -> str:
    body = (r.text or "")[:200].replace("\n", " ")
    return f"HTTP {r.status_code}: {body}"


def _request(method: str, url: str, *, headers: dict,
             json: dict | None = None, params: dict | None = None) -> dict:
    try:
        r = requests.request(method, url, headers=headers, json=json,
                             params=params, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise ProviderError(f"{method} {url} failed: {e}") from e
    if r.status_code >= 400:
        raise ProviderError(f"{method} {url} -> {_err_detail(r)}")
    try:
        return r.json()
    except ValueError as e:
        raise ProviderError(
            f"{method} {url} -> HTTP {r.status_code} non-JSON body: "
            f"{(r.text or '')[:120]}") from e


def _post(url: str, *, headers: dict, json: dict | None = None,
          params: dict | None = None) -> dict:
    return _request("POST", url, headers=headers, json=json, params=params)


def _get(url: str, *, headers: dict, params: dict) -> dict:
    return _request("GET", url, headers=headers, params=params)


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
        try:
            data = _post(f"{self.BASE}{self.ENDPOINT}", headers=headers, json=req)
        except ProviderError as e:
            trace.record(self.name, f"skip-trace {full_name!r}", "error", str(e))
            return None

        # Response shape is defensive-parsed; BatchData nests results under
        # results.persons[] with phoneNumbers[]/emails[] and DNC flags.
        persons = (_first(data, "results.persons", "persons", "data.persons", default=[]) or [])
        if not persons:
            trace.record(self.name, f"skip-trace {full_name!r}", "miss",
                         f"no persons in response (keys: {list(data)[:6]})")
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
            trace.record(self.name, f"skip-trace {full_name!r}", "miss",
                         "person matched but carried no phone/email")
            return None
        trace.record(self.name, f"skip-trace {full_name!r}", "hit",
                     f"{len(phones)} phone(s), {len(emails)} email(s)")

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
    # Real Contact, not Phone Intel: phone_intel returns NO name-match field,
    # so name_match parsed to 0.0 on every call and grade A (>= 0.8) was
    # mathematically unreachable — every live-validated phone graded B at
    # best. Real Contact takes phone+name and returns the match + grade
    # inputs this grading actually needs (docs.trestleiq.com Real Contact).
    PHONE_ENDPOINT = "/1.1/real_contact"
    COST_PHONE = float(os.environ.get("TRESTLE_PHONE_COST", "0.035"))
    COST_EMAIL = float(os.environ.get("TRESTLE_EMAIL_COST", "0.005"))

    def __init__(self, api_key: str):
        self._key = api_key

    def validate_phone(self, e164: str, expected_name: str) -> PhoneValidation:
        headers = {"x-api-key": self._key, "Accept": "application/json"}
        params = {"phone": e164, "name": expected_name}
        data = _get(f"{self.BASE}{self.PHONE_ENDPOINT}", headers=headers, params=params)

        line_type = str(_first(data, "phone.line_type", "line_type",
                               "phone_type", default="unknown")).lower()
        active = bool(_first(data, "phone.is_valid", "is_valid", "active",
                             default=True))
        # Real Contact: phone.name_match is "true"/"false"/null; the graded
        # score lives in phone.contact_grade (A-F) / activity_score. Take the
        # strongest signal available, tolerating the 3.1 phone_intel shape too.
        raw_match = _first(data, "phone.name_match", "name_match",
                           "name_match_score", "contact_grade_score",
                           default=None)
        if isinstance(raw_match, bool):
            name_match = 1.0 if raw_match else 0.0
        elif isinstance(raw_match, str):
            name_match = {"true": 1.0, "match": 1.0, "false": 0.0,
                          "no_match": 0.0}.get(raw_match.lower(), 0.0)
        else:
            try:
                name_match = float(raw_match or 0.0)
            except (TypeError, ValueError):
                name_match = 0.0
        if name_match > 1:            # some APIs return 0–100
            name_match = name_match / 100.0
        grade = str(_first(data, "phone.contact_grade", default="")).upper()
        if name_match == 0.0 and grade in ("A", "B"):
            # Vendor says the contact grades well but gave no numeric match -
            # trust the grade rather than flooring to 0 (the old bug's shape).
            name_match = 0.9 if grade == "A" else 0.7
        out = PhoneValidation(
            e164=e164,
            line_type=("mobile" if "mobile" in line_type or "cell" in line_type
                       else "landline" if "land" in line_type or "fixed" in line_type
                       else "voip" if "voip" in line_type else "unknown"),
            active=active,
            name_match=round(name_match, 2),
            litigator=bool(_first(data, "litigator_checks.is_litigator",
                                  "is_litigator", "litigator", default=False)),
            dnc_federal=bool(_first(data, "phone.is_dnc", "is_dnc",
                                    "dnc.federal", "do_not_call", default=False)),
            dnc_states=list(_first(data, "dnc.state", "dnc_states", default=[]) or []),
            vendor=self.name,
            query_id=str(_first(data, "request_id", default="trestle")),
            cost_usd=self.COST_PHONE,
        )
        trace.record(self.name, f"validate {e164}", "hit",
                     f"line={out.line_type} active={out.active} "
                     f"name_match={out.name_match}")
        return out

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
        except ProviderError as e:
            trace.record(self.name, f"pierce {entity_name!r}", "error", str(e))
            return None
        hits = _first(search, "items", "results", "businesses", default=[]) or []
        if not hits:
            trace.record(self.name, f"pierce {entity_name!r}", "miss",
                         "no matching entity on VA SCC")
            return None
        trace.record(self.name, f"pierce {entity_name!r}", "hit")
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


# ---------------------------------------------------------------------------
# Cobalt Intelligence — entity → officers, all 50 states (§4.2 S3).
# The self-serve SOS API for the metro rollout: VA SCC covers only Virginia
# and has no clean token, so Cobalt is the general-purpose piercing vendor.
# Keyed by COBALT_API_KEY. Best-effort parse; returns None when unavailable
# so the registry falls back to mock rather than fabricating a principal.
# Docs: https://cobaltintelligence.com  (Secretary of State API)
# ---------------------------------------------------------------------------

class CobaltSOS:
    name = "cobalt"
    BASE = os.environ.get("COBALT_BASE",
                          "https://apigateway.cobaltintelligence.com/v1")
    COST_PER_LOOKUP = float(os.environ.get("COBALT_COST", "1.00"))

    def __init__(self, api_key: str):
        self._key = api_key

    # Async states hand back a retryId instead of results; poll it. Docs:
    # cobaltintelligence.stoplight.io — slow states (OR up to ~5 min); we
    # bound the wait so one slow state can't hang a resolve run.
    RETRY_WAIT_S = float(os.environ.get("COBALT_RETRY_WAIT_S", "6"))
    RETRY_MAX = int(os.environ.get("COBALT_RETRY_MAX", "15"))

    def resolve_entity(self, entity_name: str, state: str) -> SOSResult | None:
        st = (state or "").strip().upper()[:2]   # Cobalt keys results by state
        if not st:
            return None
        headers = {"x-api-key": self._key, "Accept": "application/json"}
        try:
            data = _get(f"{self.BASE}/search", headers=headers,
                        params={"searchQuery": entity_name, "state": st})
            retry_id = _first(data, "retryId", "retry_id", default=None)
            polls = 0
            while retry_id and polls < self.RETRY_MAX:
                time.sleep(self.RETRY_WAIT_S)
                polls += 1
                data = _get(f"{self.BASE}/search", headers=headers,
                            params={"retryId": retry_id})
                retry_id = _first(data, "retryId", "retry_id", default=None)
            if retry_id:
                trace.record(self.name, f"pierce {entity_name!r} ({st})",
                             "error", f"still pending after {polls} polls "
                             f"(retryId {retry_id})")
                return None
        except ProviderError as e:
            trace.record(self.name, f"pierce {entity_name!r} ({st})",
                         "error", str(e))
            return None
        # Cobalt returns {results:[...]} or a bare object; tolerate both.
        hits = _first(data, "results", "data", default=None)
        biz = (hits[0] if isinstance(hits, list) and hits
               else (data if isinstance(data, dict) and
                     _first(data, "title", "entityName", "sosId") else None))
        if not isinstance(biz, dict):
            trace.record(self.name, f"pierce {entity_name!r} ({st})", "miss",
                         f"no entity in response (keys: {list(data)[:6]})")
            return None
        officers: list[str] = []
        # Array fields (officers/members/...) AND scalar principal fields -
        # Cobalt's shape varies by state and plan, so cast a wide net.
        for group in ("officers", "principals", "members", "managers",
                      "governors", "organizers", "contacts", "people"):
            val = biz.get(group)
            for o in (val if isinstance(val, list) else []):
                nm = (o if isinstance(o, str)
                      else _first(o, "name", "fullName", "fullNameNormalized",
                                  "officerName", "personName", default=None))
                if nm and nm not in officers:
                    officers.append(nm)
        for scalar in ("principalName", "officerName", "memberName",
                       "managerName", "governorName", "contactName"):
            nm = _first(biz, scalar, default=None)
            if nm and nm not in officers:
                officers.append(nm)
        agent = _first(biz, "registeredAgent.name", "registeredAgent",
                       "agentName", default="")
        # A registered agent is a fallback principal ONLY when it's a person -
        # a commercial agent (CT Corporation, a law firm) is not the
        # beneficial owner and must never be handed to skip trace.
        if not officers and agent and not _is_commercial_agent(agent):
            officers = [agent]
        trace.record(self.name, f"pierce {entity_name!r} ({st})", "hit",
                     f"{len(officers)} officer(s), agent={agent or '-'}")
        return SOSResult(
            entity_name=_first(biz, "title", "entityName", "name",
                               default=entity_name),
            jurisdiction=st,
            filing_id=str(_first(biz, "sosId", "entityId", "id", default="")),
            officers=officers,
            registered_agent=agent or "",
            confidence=0.86 if officers else 0.4,
            vendor=self.name,
            query_id=str(_first(biz, "sosId", "entityId", default="cobalt")),
            cost_usd=self.COST_PER_LOOKUP,
        )


# ---------------------------------------------------------------------------
# SEC EDGAR full-text search — FREE institutional-LLC piercing (§4.2 S3).
# Single-purpose apartment LLCs ("GD Richmond Two LLC") register with a
# commercial agent and NO members on the state record, so registry piercing
# dead-ends. But institutional sponsors raise under Reg D, and the Form D
# names the issuer's RELATED PERSONS (executives of the sponsor) with the
# sponsor's own address. No auth, no key - SEC fair-access just wants a real
# UA with contact info. (Owner 2026-08-11: "use alternative approaches".)
# ---------------------------------------------------------------------------

class EdgarSOS:
    name = "edgar"
    SEARCH = os.environ.get(
        "EDGAR_FTS_BASE", "https://efts.sec.gov/LATEST/search-index")
    ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
    SUBMISSIONS = "https://data.sec.gov/submissions"
    UA = {"User-Agent": os.environ.get(
        "EDGAR_UA", "EightRock Workbench bmccune@gmail.com"),
        "Accept": "application/json"}

    def resolve_entity(self, entity_name: str, state: str) -> SOSResult | None:
        if not entity_name:
            return None
        try:
            hits = self._search(entity_name)
            if not hits:
                trace.record(self.name, f"pierce {entity_name!r}", "miss",
                             "no SEC filings mention this entity")
                return None
            cik, matched_name = hits[0]
            officers, agent = self._related_persons(cik)
        except ProviderError as e:
            trace.record(self.name, f"pierce {entity_name!r}", "error", str(e))
            return None
        if not officers:
            trace.record(self.name, f"pierce {entity_name!r}", "miss",
                         f"CIK {cik} found but no related persons parsed")
            return None
        trace.record(self.name, f"pierce {entity_name!r}", "hit",
                     f"CIK {cik}: {len(officers)} related person(s)")
        return SOSResult(
            entity_name=matched_name or entity_name,
            jurisdiction=(state or "").strip().upper()[:2] or "US",
            filing_id=f"CIK-{cik}",
            officers=officers,
            registered_agent=agent,
            confidence=0.9,          # SEC-sworn related persons
            vendor=self.name, query_id=f"edgar-{cik}", cost_usd=0.0)

    def _search(self, entity_name: str) -> list[tuple[str, str]]:
        """Full-text search, exact-phrase, Form D first. Returns
        [(cik, display_name)]."""
        data = _get(self.SEARCH, headers=self.UA,
                    params={"q": f'"{entity_name}"', "forms": "D"})
        out = []
        for h in ((data.get("hits") or {}).get("hits") or []):
            src = h.get("_source") or {}
            for dn in (src.get("display_names") or []):
                # "GD RICHMOND TWO LLC  (CIK 0001234567)"
                m = re.search(r"\(CIK\s+(\d+)\)", dn)
                if m and entity_name.split()[0].lower() in dn.lower():
                    out.append((m.group(1).lstrip("0"), dn.split("(")[0].strip()))
        return out

    def _related_persons(self, cik: str) -> tuple[list[str], str]:
        """Names from the newest Form D's relatedPersonsList."""
        subs = _get(f"{self.SUBMISSIONS}/CIK{int(cik):010d}.json",
                    headers=self.UA, params={})
        recent = (subs.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accs = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        acc = doc = None
        for i, f in enumerate(forms):
            if f in ("D", "D/A") and i < len(accs):
                acc = accs[i].replace("-", "")
                doc = docs[i] if i < len(docs) else "primary_doc.xml"
                break
        if not acc:
            return [], ""
        url = f"{self.ARCHIVES}/{int(cik)}/{acc}/{doc or 'primary_doc.xml'}"
        try:
            r = requests.get(url, headers={**self.UA, "Accept": "*/*"},
                             timeout=_TIMEOUT)
            if r.status_code >= 400:
                raise ProviderError(f"GET {url} -> {_err_detail(r)}")
            xml_text = r.text
        except requests.RequestException as e:
            raise ProviderError(f"GET {url} failed: {e}") from e
        officers = []
        for m in re.finditer(
                r"<relatedPersonInfo>.*?<firstName>([^<]*)</firstName>"
                r".*?<lastName>([^<]*)</lastName>", xml_text, re.DOTALL):
            nm = f"{m.group(1).strip()} {m.group(2).strip()}".strip()
            if nm and nm not in officers:
                officers.append(nm)
        return officers, ""


_COMMERCIAL_AGENTS = (
    "ct corporation", "registered agent", "corporation service",
    "cogency", "incorp", "national registered", "legalzoom", "northwest",
    "harbor compliance", "csc ", "capitol services", " law", "llp", "pllc",
)


def _is_commercial_agent(name: str) -> bool:
    low = (name or "").lower()
    return any(tok in low for tok in _COMMERCIAL_AGENTS)


# ---------------------------------------------------------------------------
# Apollo.io — firmographic / business-contact enrichment (§4 enrichment).
# The realistic contact for an institutional owner whose LLC names no member:
# the management company / sponsor's main line, website, and a named
# acquisitions/asset-management contact. NOT skip trace, NOT auto-dialer
# compliant (a business main line is a manual call). Keyed by APOLLO_API_KEY.
# Docs: https://apolloio.github.io/apollo-api-docs/
# ---------------------------------------------------------------------------

class ApolloFirmographic:
    name = "apollo"
    BASE = os.environ.get("APOLLO_BASE", "https://api.apollo.io")
    COST_PER_LOOKUP = float(os.environ.get("APOLLO_COST", "0.10"))

    def __init__(self, api_key: str):
        self._key = api_key
        self._prefix: str | None = None   # learned working path prefix

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "Accept": "application/json",
                "Cache-Control": "no-cache", "X-Api-Key": self._key}

    def _call(self, method: str, suffix: str, *, params: dict) -> dict:
        """Apollo's docs have shipped BOTH api.apollo.io/api/v1/... (current
        docs.apollo.io) and api.apollo.io/v1/... (older reference) - and this
        codebase has been flip-flopped between them twice on doc reads alone
        (V5.18.2 pinned /v1 as "the fix"; neither was ever live-verified and
        the panel still returned nothing). Stop guessing: try /api/v1 first,
        fall back to /v1 on a 404, remember what worked, and put the winning
        path in the trace so the host run settles it with evidence."""
        prefixes = ([self._prefix] if self._prefix
                    else ["/api/v1", "/v1"])
        last: ProviderError | None = None
        for pfx in prefixes:
            url = f"{self.BASE}{pfx}{suffix}"
            try:
                data = _request(method, url, headers=self._headers(),
                                params=params)
            except ProviderError as e:
                last = e
                if "HTTP 404" in str(e) and self._prefix is None:
                    continue          # wrong prefix generation - try the other
                raise
            if self._prefix is None:
                self._prefix = pfx
                trace.record(self.name, f"endpoint prefix {pfx}", "hit",
                             f"{method} {url} answered")
            return data
        raise last if last else ProviderError(f"{suffix}: no prefix answered")

    def _search_org(self, company: str) -> dict | None:
        """Name-based org SEARCH — the right call for a bare company name.
        Org ENRICH matches best by domain, which we don't have, so a name
        like "Nexus Management Company" often enriches to nothing. Search
        does fuzzy name matching, POST with the term in the query string.
        Path prefix is self-verifying via _call (docs have shipped both
        /api/v1 and /v1 generations)."""
        try:
            data = self._call("POST", "/mixed_companies/search",
                              params={"q_organization_name": company,
                                      "per_page": 1})
        except ProviderError as e:
            trace.record(self.name, f"org-search {company!r}", "error", str(e))
            return None
        orgs = _first(data, "organizations", "accounts", default=[]) or []
        return orgs[0] if isinstance(orgs, list) and orgs else None

    def _enrich_org(self, company: str) -> dict | None:
        """Org ENRICH by name — GET, match params in the query string
        (docs.apollo.io/reference/organization-enrichment)."""
        try:
            data = self._call("GET", "/organizations/enrich",
                              params={"name": company})
        except ProviderError as e:
            trace.record(self.name, f"org-enrich {company!r}", "error", str(e))
            return None
        org = _first(data, "organization", "organizations", default=None)
        if isinstance(org, list):
            org = org[0] if org else None
        return org if isinstance(org, dict) else None

    def enrich_company(self, company: str, city=None, state=None):
        from core.skiptrace.providers import BusinessContact
        if not company:
            return None
        # Search first (name is what we have), enrich as fallback.
        org = self._search_org(company) or self._enrich_org(company)
        if not isinstance(org, dict):
            trace.record(self.name, f"enrich {company!r}", "miss",
                         "no org matched by search or enrich")
            return None
        phone = _first(org, "phone", "primary_phone.number", "sanitized_phone",
                       default="") or ""
        website = _first(org, "website_url", "website", default="") or ""
        # Best-available named contact when the plan returns leadership.
        contact_name = contact_title = ""
        people = _first(org, "people", "contacts", "leadership", default=[]) or []
        for p in (people if isinstance(people, list) else []):
            nm = _first(p, "name", "full_name", default=None)
            if nm:
                contact_name = nm
                contact_title = _first(p, "title", "headline", default="") or ""
                break
        email = _first(org, "email", "primary_email", default="") or ""
        if not (phone or website or email or contact_name):
            trace.record(self.name, f"enrich {company!r}", "miss",
                         "org matched but had no phone/site/email/contact")
            return None
        trace.record(self.name, f"enrich {company!r}", "hit",
                     f"phone={'y' if phone else '-'} site={'y' if website else '-'}")
        return BusinessContact(
            company=_first(org, "name", default=company),
            phone=_e164(phone) if phone else "",
            email=email, website=website,
            contact_name=contact_name, contact_title=contact_title,
            vendor=self.name,
            query_id=str(_first(org, "id", default="apollo")),
            cost_usd=self.COST_PER_LOOKUP)


def _e164(num: str) -> str:
    digits = "".join(c for c in str(num) if c.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return "+" + digits if digits else str(num)
