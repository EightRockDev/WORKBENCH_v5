"""B2 — personalized outreach artifacts, grounded in Workbench data (spec §5).

The differentiator: artifacts cite facts no dialer can see — the owner's own deed
chain ("you bought in 2014 at $1.1M"), loan maturity from GRANITE ("your HUD loan
matures March 2027"), assessed-value trend, and portfolio context.

Section 11 compliance: generation is **deterministic by default**. Every artifact
has a template path that runs with the AI layer switched off; the optional AI
polish only rewrites prose and can never introduce a number — the grounded facts
are rendered by the template and the numeric sanity check re-verifies them.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass, field


@dataclass
class Grounding:
    """Facts pulled from the Workbench for one owner/property."""

    owner_name: str
    property_name: str | None = None
    property_address: str | None = None
    units: int | None = None
    city: str | None = None
    last_sale_year: int | None = None
    last_sale_amount: float | None = None
    loan_maturity: dt.date | None = None
    loan_type: str | None = None
    assessed_value: float | None = None
    assessed_trend_pct: float | None = None
    portfolio_count: int | None = None
    sender_name: str = "Eight Rock Capital Partners"
    sender_phone: str = ""
    sender_email: str = ""

    def facts(self) -> list[str]:
        """Grounded, checkable sentences. Only facts we actually hold."""
        out: list[str] = []
        if self.last_sale_year and self.last_sale_amount:
            out.append(f"you acquired it in {self.last_sale_year} for "
                       f"${self.last_sale_amount:,.0f}")
        if self.loan_maturity:
            lt = f"{self.loan_type} " if self.loan_type else ""
            out.append(f"your {lt}loan matures {self.loan_maturity:%B %Y}")
        if self.assessed_value:
            trend = ""
            if self.assessed_trend_pct is not None:
                direction = "up" if self.assessed_trend_pct >= 0 else "down"
                trend = f", {direction} {abs(self.assessed_trend_pct):.0f}% over three years"
            out.append(f"the current assessment is ${self.assessed_value:,.0f}{trend}")
        if self.portfolio_count and self.portfolio_count > 1:
            out.append(f"our records show {self.portfolio_count} properties under "
                       "the same ownership")
        return out


LETTER_TEMPLATE = """{date}

{owner_name}
{mailing_address}

Re: {property_label}

Dear {salutation},

I am writing directly about {property_label}{units_clause}. We are a local
buyer focused on {market} and we are not a broker - there is no listing
agreement and no commission involved.

{facts_paragraph}

If you have ever considered selling, I would welcome a short conversation. We
can move at your pace, close on your timeline, and there is no obligation in
talking. If the timing is not right, simply let me know and I will not follow
up further.

You can reach me directly at {sender_phone}{email_clause}.

Sincerely,

{sender_name}
"""

TALKING_POINTS_TEMPLATE = """Call prep - {owner_name} ({property_label})
------------------------------------------------------------------
Open:   "Hi {salutation}, this is {sender_name}. I'm a local buyer, not a
        broker - do you have two minutes?"
Ground: {facts_bullets}
Ask:    "Have you given any thought to what you'd do with {property_label}
        over the next year or two?"
Close:  Offer a no-obligation valuation; confirm best contact method.
Notes:  Do not discuss price on the first call. Log outcome in the workbench.
"""


def _salutation(owner_name: str) -> str:
    parts = [p for p in (owner_name or "").split() if p]
    return f"Mr./Ms. {parts[-1]}" if parts else "Property Owner"


def _facts_paragraph(g: Grounding) -> str:
    facts = g.facts()
    if not facts:
        return ("I follow the smaller multifamily properties in this market "
                "closely, and yours is one I would like to learn more about.")
    if len(facts) == 1:
        body = facts[0]
    else:
        body = ", ".join(facts[:-1]) + f", and {facts[-1]}"
    return (f"From the public record I can see that {body}. I mention this only "
            "so you know I have done my homework and am not sending a form letter.")


def render_letter(g: Grounding, mailing_address: str,
                  today: dt.date | None = None) -> str:
    """Deterministic direct-mail letter (B2). No LLM required."""
    today = today or dt.date.today()
    label = g.property_name or g.property_address or "your property"
    units = f", the {g.units}-unit property" if g.units else ""
    return LETTER_TEMPLATE.format(
        date=today.strftime("%B %d, %Y"),
        owner_name=g.owner_name,
        mailing_address=mailing_address or "",
        property_label=label,
        units_clause=units,
        market=g.city or "Hampton Roads",
        facts_paragraph=_facts_paragraph(g),
        salutation=_salutation(g.owner_name),
        sender_phone=g.sender_phone or "(757) 000-0000",
        email_clause=f" or {g.sender_email}" if g.sender_email else "",
        sender_name=g.sender_name,
    )


def render_talking_points(g: Grounding) -> str:
    """Live-call talking points (B2), deterministic."""
    facts = g.facts()
    # Upper-case only the first letter: .capitalize() would destroy proper nouns
    # and acronyms in the grounded facts ("HUD" -> "hud", "March" -> "march").
    bullets = "\n        ".join(
        f"- {f[0].upper() + f[1:]}" for f in facts) or "- (no public-record facts on file)"
    return TALKING_POINTS_TEMPLATE.format(
        owner_name=g.owner_name,
        property_label=g.property_name or g.property_address or "the property",
        salutation=_salutation(g.owner_name),
        sender_name=g.sender_name,
        facts_bullets=bullets,
    )


# ---------------------------------------------------------------------------
# Numeric sanity check — no AI polish may alter a grounded number (§11)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def numbers_in(text: str) -> set[str]:
    return {m.group(0).rstrip(".").replace(",", "") for m in _NUM_RE.finditer(text)}


def validate_polish(original: str, polished: str) -> tuple[bool, str]:
    """Reject AI-polished prose that introduces or changes a number (§11 AC-11.3)."""
    before, after = numbers_in(original), numbers_in(polished)
    added = after - before
    if added:
        return False, f"AI polish introduced numbers not in the grounded letter: {sorted(added)}"
    dropped = before - after
    if dropped:
        return False, f"AI polish dropped grounded numbers: {sorted(dropped)}"
    return True, "numbers match the grounded template"


# ---------------------------------------------------------------------------
# AC-B3 — direct-mail batch: generate, DEDUPLICATE, export-ready
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    letters: list[dict] = field(default_factory=list)
    duplicates_removed: int = 0
    skipped_no_address: int = 0

    @property
    def count(self) -> int:
        return len(self.letters)


def _norm_addr(a: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (a or "").lower())


def build_letter_batch(recipients: list[dict], *, today: dt.date | None = None,
                       sender_name: str = "Eight Rock Capital Partners",
                       sender_phone: str = "", sender_email: str = "") -> BatchResult:
    """Render a deduplicated letter batch (AC-B3).

    ``recipients`` are dicts with at least ``owner_name`` and ``mailing_address``
    plus any Grounding fields. Duplicate (owner, address) pairs collapse to one
    piece so a portfolio owner is not mailed five times.
    """
    res = BatchResult()
    seen: set[tuple[str, str]] = set()
    for r in recipients:
        addr = r.get("mailing_address")
        if not addr:
            res.skipped_no_address += 1
            continue
        key = (_norm_addr(r.get("owner_name")), _norm_addr(addr))
        if key in seen:
            res.duplicates_removed += 1
            continue
        seen.add(key)
        g = Grounding(
            owner_name=r.get("owner_name") or "Property Owner",
            property_name=r.get("property_name"), property_address=r.get("property_address"),
            units=r.get("units"), city=r.get("city"),
            last_sale_year=r.get("last_sale_year"), last_sale_amount=r.get("last_sale_amount"),
            loan_maturity=r.get("loan_maturity"), loan_type=r.get("loan_type"),
            assessed_value=r.get("assessed_value"),
            assessed_trend_pct=r.get("assessed_trend_pct"),
            portfolio_count=r.get("portfolio_count"),
            sender_name=sender_name, sender_phone=sender_phone, sender_email=sender_email)
        res.letters.append({
            "owner_name": g.owner_name,
            "mailing_address": addr,
            "property_id": r.get("property_id"),
            "body": render_letter(g, addr, today),
        })
    return res


def batch_to_html(batch: BatchResult) -> str:
    """One print-ready HTML doc, one letter per page (export/lob handoff)."""
    pages = []
    for ltr in batch.letters:
        body = html.escape(ltr["body"]).replace("\n", "<br/>")
        pages.append(f'<div class="pg">{body}</div>')
    return ("<html><head><meta charset='utf-8'><style>"
            "@page{size:letter;margin:1in}"
            ".pg{page-break-after:always;font-family:Georgia,serif;font-size:11pt;"
            "line-height:1.5;white-space:normal}"
            "</style></head><body>" + "".join(pages) + "</body></html>")


def batch_to_csv(batch: BatchResult) -> str:
    """Mail-merge CSV (for a mail house or a lob-style API handoff)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["owner_name", "mailing_address", "property_id", "body"])
    for ltr in batch.letters:
        w.writerow([ltr["owner_name"], ltr["mailing_address"],
                    ltr.get("property_id"), ltr["body"]])
    return buf.getvalue()
