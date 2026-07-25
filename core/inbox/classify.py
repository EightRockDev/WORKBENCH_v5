"""Module D — inbound email classification (spec §6.2), deterministic.

Classifies broker / lender / attorney / LP / other from sender domain, subject,
body signals and attachment types, returning a category **and a confidence** —
the confidence is what drives the §6.2 gate: high-confidence extractions apply
automatically, low-confidence ones queue for one-click human confirm.

Section 11: this is the deterministic path and it is complete on its own. An
optional AI classifier may refine the category, but it can never lower the bar —
see ``core/inbox/engine.py`` for how the two combine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORIES = ("broker", "lender", "attorney", "lp", "other")

# Signal weights. Domain evidence is strongest, then subject, then body.
_DOMAIN_HINTS = {
    "broker": ("marcusmillichap", "cbre", "jll", "cushwake", "cushmanwakefield",
               "colliers", "berkadia", "newmark", "kwcommercial", "loopnet",
               "crexi", "realtor", "commercial"),
    "lender": ("bank", "capital", "lending", "loans", "mortgage", "fanniemae",
               "freddiemac", "hud", "creditunion", "fcu", "walkerdunlop",
               "greystone", "arbor"),
    "attorney": ("law", "legal", "attorney", "counsel", "llp", "esq", "title"),
    "lp": ("invest", "family", "wealth", "capitalpartners"),
}
_SUBJECT_HINTS = {
    "broker": ("om", "offering memorandum", "listing", "for sale", "new to market",
               "price reduction", "call for offers", "bov", "opinion of value",
               "rent roll", "t-12", "t12", "flyer", "investment opportunity"),
    "lender": ("term sheet", "quote", "loan", "financing", "rate", "ltv", "dscr",
               "pre-approval", "commitment letter", "underwriting"),
    "attorney": ("psa", "purchase and sale", "contract", "closing", "title",
                 "escrow", "estoppel", "amendment", "addendum", "diligence"),
    "lp": ("distribution", "capital call", "k-1", "investor update", "subscription"),
}
_BODY_HINTS = {
    "broker": ("asking price", "cap rate", "units", "noi", "offering",
               "seller is", "please find attached the om"),
    "lender": ("interest rate", "amortization", "interest-only", "proceeds",
               "loan amount", "term sheet", "spread over"),
    "attorney": ("executed", "counterparty", "closing date", "escrow agent",
                 "please review the attached agreement"),
    "lp": ("your capital account", "distribution", "quarterly update"),
}
_ATTACH_HINTS = {
    "broker": (".pdf", ".xlsx", ".xls"),
    "attorney": (".docx", ".doc", ".pdf"),
}


@dataclass
class Classification:
    category: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    classifier: str = "deterministic"

    def as_dict(self) -> dict:
        return {"category": self.category, "confidence": round(self.confidence, 3),
                "signals": self.signals, "classifier": self.classifier}


def _domain(email: str | None) -> str:
    return (email or "").split("@")[-1].lower()


def classify(*, from_email: str | None, subject: str | None, body: str | None,
             attachments: list[dict] | None = None) -> Classification:
    """Score each category from independent signals; return the best with a
    calibrated confidence in [0,1]."""
    subject_l = (subject or "").lower()
    body_l = (body or "")[:4000].lower()
    dom = _domain(from_email)
    names = [str(a.get("filename", "")).lower() for a in (attachments or [])]

    scores = dict.fromkeys(CATEGORIES, 0.0)
    signals: dict[str, list[str]] = {c: [] for c in CATEGORIES}

    for cat, hints in _DOMAIN_HINTS.items():
        for h in hints:
            if h in dom:
                scores[cat] += 0.45
                signals[cat].append(f"sender domain contains '{h}'")
                break
    for cat, hints in _SUBJECT_HINTS.items():
        hit = [h for h in hints if h in subject_l]
        if hit:
            scores[cat] += min(0.40, 0.22 * len(hit))
            signals[cat].append(f"subject mentions {hit[:2]}")
    for cat, hints in _BODY_HINTS.items():
        hit = [h for h in hints if h in body_l]
        if hit:
            scores[cat] += min(0.30, 0.12 * len(hit))
            signals[cat].append(f"body mentions {hit[:2]}")
    for cat, exts in _ATTACH_HINTS.items():
        if any(n.endswith(e) for n in names for e in exts):
            scores[cat] += 0.10
            signals[cat].append("relevant attachment type")

    best = max(scores, key=lambda c: scores[c])
    raw = scores[best]
    if raw <= 0:
        return Classification("other", 0.0, ["no broker/lender/attorney/LP signals"])

    # Ambiguity penalty: a close runner-up lowers confidence, which is exactly
    # what should push a borderline message into the human-confirm queue.
    runner = sorted(scores.values(), reverse=True)[1]
    conf = max(0.0, min(0.99, raw - 0.5 * runner))
    return Classification(best, conf, signals[best])


def is_deal_relevant(c: Classification) -> bool:
    """Broker/lender/attorney mail drives pipeline records; LP/other does not."""
    return c.category in ("broker", "lender", "attorney")
