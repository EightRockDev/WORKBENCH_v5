"""Module D — deterministic fact extraction from inbound mail (spec §6.2).

Pulls the deal facts a pipeline record needs (property name, address, units,
asking price, cap rate) and lender term-sheet terms (rate, LTV, amortization,
IO, term, proceeds) using explicit patterns — **no LLM** (Section 11).

Each field carries its own confidence, and the record's confidence is the mean
of what was found, weighted by how load-bearing the field is. That number drives
the §6.2 confidence gate: below threshold, the extraction queues for one-click
human confirm instead of silently writing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Field importance for the composite confidence — a price with no address is
# much weaker evidence of a real deal than an address with units.
_FIELD_WEIGHT = {
    "address": 1.0, "units": 0.9, "asking_price": 0.8, "city": 0.6,
    "state": 0.4, "cap_rate": 0.5, "name": 0.7,
}

_MONEY = r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|mm|m|k)?\b"
_STATES = ("VA", "NC", "SC", "GA", "TN", "MD", "DC", "FL", "TX")


@dataclass
class Extraction:
    fields: dict = field(default_factory=dict)
    confidences: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Weighted mean confidence over the fields we actually found."""
        if not self.confidences:
            return 0.0
        num = sum(c * _FIELD_WEIGHT.get(k, 0.5) for k, c in self.confidences.items())
        den = sum(_FIELD_WEIGHT.get(k, 0.5) for k in self.confidences)
        return round(num / den, 3) if den else 0.0

    def as_dict(self) -> dict:
        return {"fields": self.fields, "confidences": self.confidences,
                "evidence": self.evidence, "confidence": self.confidence}


def _money(m: re.Match) -> float:
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit in ("million", "mm", "m"):
        val *= 1_000_000
    elif unit == "k":
        val *= 1_000
    return val


def extract_deal(*, subject: str | None, body: str | None,
                 attachments: list[dict] | None = None) -> Extraction:
    """Extract pipeline-record facts from a broker email."""
    text = f"{subject or ''}\n{body or ''}"
    low = text.lower()
    ex = Extraction()

    # --- units -----------------------------------------------------------
    m = re.search(r"\b(\d{1,4})\s*[- ]?\s*units?\b", low)
    if m:
        u = int(m.group(1))
        if 1 <= u <= 5000:
            ex.fields["units"] = u
            ex.confidences["units"] = 0.9
            ex.evidence.append(f"'{m.group(0).strip()}' in text")

    # --- asking price ----------------------------------------------------
    pm = re.search(r"(?:asking|price|offered at|list(?:ed)? at)\D{0,20}" + _MONEY, low)
    if pm:
        ex.fields["asking_price"] = _money(pm)
        ex.confidences["asking_price"] = 0.85
        ex.evidence.append(f"asking price near '{pm.group(0)[:40].strip()}'")
    else:
        any_money = re.search(_MONEY, low)
        if any_money and _money(any_money) >= 100_000:
            ex.fields["asking_price"] = _money(any_money)
            ex.confidences["asking_price"] = 0.45     # unlabeled -> low confidence
            ex.evidence.append("unlabeled dollar figure - low confidence")

    # --- cap rate --------------------------------------------------------
    cm = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(?:cap|cap rate)|cap(?:\s*rate)?\D{0,10}"
                   r"(\d{1,2}(?:\.\d{1,2})?)\s*%", low)
    if cm:
        raw = cm.group(1) or cm.group(2)
        cap = float(raw) / 100.0
        if 0.02 <= cap <= 0.20:
            ex.fields["cap_rate"] = round(cap, 4)
            ex.confidences["cap_rate"] = 0.8
            ex.evidence.append(f"cap rate {raw}%")

    # --- street address ---------------------------------------------------
    am = re.search(r"\b(\d{1,6}\s+[A-Z][A-Za-z0-9.'-]*(?:\s+[A-Z][A-Za-z0-9.'-]*){0,4}\s+"
                   r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|"
                   r"Way|Ct|Court|Pkwy|Parkway|Cir|Circle|Ter|Terrace|Pl|Place))\b", text)
    if am:
        ex.fields["address"] = am.group(1).strip()
        ex.confidences["address"] = 0.85
        ex.evidence.append(f"street address '{am.group(1).strip()}'")

    # --- city / state -----------------------------------------------------
    sm = re.search(r",\s*([A-Z][a-zA-Z ]{2,20}),?\s+(" + "|".join(_STATES) + r")\b", text)
    if sm:
        ex.fields["city"] = sm.group(1).strip()
        ex.fields["state"] = sm.group(2)
        ex.confidences["city"] = 0.8
        ex.confidences["state"] = 0.9
        ex.evidence.append(f"city/state '{sm.group(1).strip()}, {sm.group(2)}'")
    else:
        st = re.search(r"\b(" + "|".join(_STATES) + r")\b\s+\d{5}\b", text)
        if st:
            ex.fields["state"] = st.group(1)
            ex.confidences["state"] = 0.7

    # --- deal name --------------------------------------------------------
    nm = re.search(r"\b([A-Z][A-Za-z'&.-]+(?:\s+[A-Z][A-Za-z'&.-]+){0,3}\s+"
                   r"(?:Apartments|Townhomes|Townhouses|Commons|Court|Village|Place|"
                   r"Manor|Landing|Pointe|Point|Park|Estates|Lofts|Flats|Gardens))\b", text)
    if nm:
        ex.fields["name"] = nm.group(1).strip()
        ex.confidences["name"] = 0.75
        ex.evidence.append(f"property name '{nm.group(1).strip()}'")
    elif ex.fields.get("address"):
        ex.fields["name"] = ex.fields["address"]
        ex.confidences["name"] = 0.5

    # Attachments are corroboration, not facts.
    om = [a for a in (attachments or [])
          if re.search(r"\b(om|offering|memorandum|rent.?roll|t-?12)\b",
                       str(a.get("filename", "")), re.I)]
    if om:
        ex.evidence.append(f"deal attachment: {om[0].get('filename')}")
        for k in ex.confidences:
            ex.confidences[k] = min(0.99, ex.confidences[k] + 0.05)

    return ex


def extract_terms(*, subject: str | None, body: str | None) -> Extraction:
    """Extract lender term-sheet terms (§6.2 term-sheet history)."""
    text = f"{subject or ''}\n{body or ''}"
    low = text.lower()
    ex = Extraction()

    r = re.search(r"(?:rate|coupon|pricing)\D{0,15}(\d{1,2}\.\d{1,3})\s*%", low)
    if r:
        ex.fields["rate"] = round(float(r.group(1)) / 100, 5)
        ex.confidences["rate"] = 0.9
        ex.evidence.append(f"rate {r.group(1)}%")

    l = re.search(r"(\d{2,3}(?:\.\d)?)\s*%\s*ltv|ltv\D{0,10}(\d{2,3}(?:\.\d)?)\s*%", low)
    if l:
        raw = float(l.group(1) or l.group(2))
        if 10 <= raw <= 95:
            ex.fields["ltv"] = round(raw / 100, 4)
            ex.confidences["ltv"] = 0.9
            ex.evidence.append(f"LTV {raw}%")

    a = re.search(r"(\d{2})\s*[- ]?\s*(?:year|yr)\s*(?:amortization|amort)", low)
    if a:
        ex.fields["amort_years"] = int(a.group(1))
        ex.confidences["amort_years"] = 0.85

    io = re.search(r"(\d{1,2})\s*(?:year|yr)s?\s*(?:of\s*)?(?:interest[- ]only|io)\b", low)
    if io:
        ex.fields["io_years"] = int(io.group(1))
        ex.confidences["io_years"] = 0.8

    t = re.search(r"(\d{1,2})\s*[- ]?\s*(?:year|yr)\s*term\b", low)
    if t:
        ex.fields["term_years"] = int(t.group(1))
        ex.confidences["term_years"] = 0.85

    p = re.search(r"(?:proceeds|loan amount)\D{0,20}" + _MONEY, low)
    if p:
        ex.fields["proceeds"] = _money(p)
        ex.confidences["proceeds"] = 0.85
        ex.evidence.append("loan proceeds found")

    lender = re.search(r"\b([A-Z][A-Za-z&.-]+(?:\s+[A-Z][A-Za-z&.-]+){0,3}\s+"
                       r"(?:Bank|Capital|Lending|Mortgage|Financial|Partners))\b", text)
    if lender:
        ex.fields["lender"] = lender.group(1).strip()
        ex.confidences["lender"] = 0.7

    return ex
