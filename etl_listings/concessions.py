"""Concession parser + effective-rent math.

Apartments.com / Zillow / Rent.com all surface concessions as unstructured
text in a banner or "Special Offers" callout. We parse these via regex
first (fast, no API cost); fall back to Claude API for unparseable copy.

Effective rent math (Eight Rock convention):

  effective_monthly_rent =
      asking_monthly_rent × (lease_term_months − months_free) / lease_term_months

So $1,500 asking + 1 month free on 12-month lease →
  $1,500 × (12 − 1) / 12 = $1,375 effective.

The convention amortizes the concession over the full lease — this is the
right way to compute cap rate, NOI, and verdict. ALN's avg_rent is asking;
effective rent is what hits the underwriter's pro forma.
"""

from __future__ import annotations

import dataclasses
import os
import re

# Regex patterns ordered most-specific to least-specific. Each capture group
# pulls out the relevant value; the surrounding text is logged for review.

_PATTERNS = [
    # "1 month free", "2 months free", "1.5 months free"
    (
        r"(\d+(?:\.\d+)?)\s+months?\s+free",
        lambda m: {"months_free": float(m.group(1)), "lease_term": 12},
    ),
    # "1 month rent free", "two months free rent"
    (
        r"(\d+|one|two|three|four|five)\s+months?\s+(?:of\s+)?(?:rent\s+)?free",
        lambda m: {
            "months_free": _word_to_float(m.group(1)),
            "lease_term": 12,
        },
    ),
    # "6 weeks free", "4 weeks free"
    (
        r"(\d+)\s+weeks?\s+free",
        lambda m: {
            "months_free": int(m.group(1)) / 4.33,
            "lease_term": 12,
        },
    ),
    # "$500 off first month", "$1,000 off first month"
    (
        r"\$\s?(\d[\d,]*)\s+off(?:\s+(?:your|the))?\s+(?:first|1st)?\s*month",
        lambda m: {
            "dollar_off": float(m.group(1).replace(",", "")),
            "lease_term": 12,
        },
    ),
    # "Look + lease — $500 off" (variant)
    (
        r"look\s*[+&]\s*lease.*?\$\s?(\d[\d,]*)",
        lambda m: {
            "dollar_off": float(m.group(1).replace(",", "")),
            "lease_term": 12,
        },
    ),
    # "Move-in special: $X" — flat-dollar incentive
    (
        r"move[-\s]in\s+(?:special|bonus|incentive)[^$]*\$\s?(\d[\d,]*)",
        lambda m: {
            "dollar_off": float(m.group(1).replace(",", "")),
            "lease_term": 12,
        },
    ),
    # Implicit "free rent" mentions without explicit number — assume 1 mo
    (
        r"\bfree\s+rent\b",
        lambda m: {"months_free": 1.0, "lease_term": 12},
    ),
]


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _word_to_float(s: str) -> float:
    s = s.strip().lower()
    if s in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[s])
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ParsedConcession:
    """Structured result of parsing concession text."""
    months_free: float = 0.0          # equivalent months of free rent
    dollar_off: float = 0.0           # flat-dollar concession (alternative)
    lease_term_months: int = 12       # assumed unless text says otherwise
    raw_text: str = ""
    confidence: str = "none"          # "none" | "regex" | "ai" | "manual"

    @property
    def has_concession(self) -> bool:
        return self.months_free > 0 or self.dollar_off > 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_concession_text(
    text: str | None,
    *,
    use_ai_fallback: bool = True,
) -> ParsedConcession:
    """Parse a concession banner string into structured form.

    Tries regex first (fast, $0). Falls back to Claude API only if regex
    finds nothing AND the text has any hint of a concession ("free", "off",
    "$", "weeks", "months", "special"). Set ``use_ai_fallback=False`` to
    disable AI calls (useful for tests + when running offline).
    """
    if not text:
        return ParsedConcession(raw_text="", confidence="none")

    cleaned = text.lower()

    for pattern, builder in _PATTERNS:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            data = builder(m)
            return ParsedConcession(
                months_free=data.get("months_free", 0.0),
                dollar_off=data.get("dollar_off", 0.0),
                lease_term_months=data.get("lease_term", 12),
                raw_text=text,
                confidence="regex",
            )

    # No regex match — does the text smell like a concession?
    smells_concessiony = any(
        kw in cleaned for kw in
        ("free", " off", "$", " weeks", " month", "special", "bonus", "discount")
    )
    if smells_concessiony and use_ai_fallback:
        ai_result = _ai_parse(text)
        if ai_result is not None:
            ai_result.raw_text = text
            ai_result.confidence = "ai"
            return ai_result

    return ParsedConcession(raw_text=text, confidence="none")


def compute_effective_rent(
    asking_rent: float,
    concession: ParsedConcession,
) -> float:
    """Annual-amortized effective monthly rent given asking + concession.

    Handles both months-free and dollar-off concessions. Returns asking
    unchanged when there's no concession.
    """
    if asking_rent <= 0 or not concession.has_concession:
        return asking_rent

    lease = concession.lease_term_months or 12
    annual_asking = asking_rent * lease

    if concession.months_free > 0:
        annual_effective = asking_rent * (lease - concession.months_free)
    elif concession.dollar_off > 0:
        annual_effective = annual_asking - concession.dollar_off
    else:
        return asking_rent

    return max(0.0, annual_effective / lease)


# ---------------------------------------------------------------------------
# Claude API fallback
# ---------------------------------------------------------------------------

def _ai_parse(text: str) -> ParsedConcession | None:
    """Use Claude to parse concession text the regex missed.

    Returns None on any error (missing API key, network, parse failure) — the
    caller treats this as "no concession detected" and moves on.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "You are parsing rental concession copy from an apartment listing. "
        "Extract the concession value as structured JSON. Return ONLY JSON "
        "with this schema, no other text:\n\n"
        '  {"months_free": <float>, "dollar_off": <float>, '
        '"lease_term_months": <int>}\n\n'
        "Rules:\n"
        "- If the concession is N weeks free, convert: months_free = N / 4.33\n"
        "- If 'first 2 months $X', that's an effective dollar_off of 2*X\n"
        "- If no actual concession, return all zeros + lease_term_months=12\n"
        "- Default lease_term_months to 12 unless the copy says otherwise\n\n"
        f'Concession copy: """{text}"""'
    )
    try:
        msg = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        content = msg.content[0].text if msg.content else ""
        import json
        data = json.loads(content.strip())
        return ParsedConcession(
            months_free=float(data.get("months_free") or 0),
            dollar_off=float(data.get("dollar_off") or 0),
            lease_term_months=int(data.get("lease_term_months") or 12),
        )
    except Exception:
        return None
