"""Derive vacancy / unit-mix signal from a listing's available-units table.

The owner ask (2026-08-03): ingest the per-unit board a listing publishes, not
just the rent range. From it we can state things a range can't — "at least 6
units are on the market, all 2br/1ba, two carry a concession, next turn Aug
18". Those are underwriting signals: live vacancy is both a rent-comp input
and a distress/motivation tell.

Deliberately conservative wording. A listing's board shows units the property
CHOSE to advertise, which is a *floor* on vacancy, never the whole rent roll —
so counts are reported as "at least N", never as the vacancy rate.
"""

from __future__ import annotations

import datetime as dt
import json
import re


def _is_now(available: str | None) -> bool:
    return bool(available) and available.strip().lower() in (
        "now", "available now", "immediately", "today")


def _bedbath(bedrooms, bathrooms) -> str | None:
    if bedrooms is None and bathrooms is None:
        return None
    bd = "studio" if bedrooms == 0 else (f"{int(bedrooms)}br"
                                         if bedrooms is not None else "?br")
    if bathrooms is None:
        return bd
    ba = int(bathrooms) if float(bathrooms).is_integer() else bathrooms
    return f"{bd}/{ba}ba"


def summarize_units(units: list) -> dict:
    """Collapse a list of UnitAvailability (or dicts) into row signal.

    Returns the columns persisted on rent_listings plus a JSON snapshot of
    the board itself, so the detail survives for the UI and re-analysis.
    """
    rows = [u if isinstance(u, dict) else _as_dict(u) for u in (units or [])]
    rows = [r for r in rows if any(v is not None and v != "" for v in r.values())]
    if not rows:
        return {"units_available": 0, "units_available_now": 0,
                "next_available": None, "unit_mix": None,
                "unit_rent_min": None, "unit_rent_max": None,
                "units_special_offers": 0, "units_json": None}

    rents = [float(r["base_rent"]) for r in rows
             if r.get("base_rent") not in (None, "")]
    mixes = {}
    for r in rows:
        m = _bedbath(r.get("bedrooms"), r.get("bathrooms"))
        if m:
            mixes[m] = mixes.get(m, 0) + 1
    # "all 2br/1ba" when the board is uniform; otherwise the mix breakdown.
    if len(mixes) == 1:
        mix = f"all {next(iter(mixes))}"
    elif mixes:
        mix = ", ".join(f"{n}x {m}" for m, n in
                        sorted(mixes.items(), key=lambda kv: -kv[1]))
    else:
        mix = None

    return {
        "units_available": len(rows),
        "units_available_now": sum(1 for r in rows if _is_now(r.get("available"))),
        "next_available": _next_available(rows),
        "unit_mix": mix,
        "unit_rent_min": min(rents) if rents else None,
        "unit_rent_max": max(rents) if rents else None,
        "units_special_offers": sum(1 for r in rows if r.get("special_offer")),
        "units_json": json.dumps(rows, default=str),
    }


def _next_available(rows: list[dict]) -> str | None:
    """Soonest availability across the board. 'Now' wins; then the earliest
    parseable date; else whatever the first row states verbatim."""
    if any(_is_now(r.get("available")) for r in rows):
        return "Now"
    dated = []
    for r in rows:
        d = _parse_date(r.get("available"))
        if d:
            dated.append((d, r["available"]))
    if dated:
        return min(dated, key=lambda t: t[0])[1]
    for r in rows:
        if r.get("available"):
            return r["available"]
    return None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_date(s: str | None) -> dt.date | None:
    """'Aug 18' / 'Sep 18, 2026' / '8/18' -> a date, best effort. No wall
    clock is read (phase0 discipline) - a bare month/day assumes a nominal
    year so ordering works; comparison is relative within one board."""
    if not s:
        return None
    t = s.strip().lower()
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?", t)
    if m and m.group(1) in _MONTHS:
        try:
            return dt.date(int(m.group(3) or 2000), _MONTHS[m.group(1)],
                           int(m.group(2)))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:
        try:
            yr = m.group(3)
            yr = int(yr) + 2000 if yr and len(yr) == 2 else int(yr or 2000)
            return dt.date(yr, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _as_dict(u) -> dict:
    return {"unit": getattr(u, "unit", None),
            "bedrooms": getattr(u, "bedrooms", None),
            "bathrooms": getattr(u, "bathrooms", None),
            "sqft": getattr(u, "sqft", None),
            "available": getattr(u, "available", None),
            "base_rent": getattr(u, "base_rent", None),
            "special_offer": bool(getattr(u, "special_offer", False))}


def headline(signal: dict) -> str | None:
    """One-line human summary for the card, or None when the board was empty.
    'At least 6 units available (2 now) · all 2br/1ba · $1,050–$1,199 · 2
    concessions · next Aug 18'."""
    n = signal.get("units_available") or 0
    if not n:
        return None
    bits = [f"at least {n} unit{'s' if n != 1 else ''} available"]
    now = signal.get("units_available_now") or 0
    if now:
        bits[0] += f" ({now} now)"
    if signal.get("unit_mix"):
        bits.append(signal["unit_mix"])
    lo, hi = signal.get("unit_rent_min"), signal.get("unit_rent_max")
    if lo is not None and hi is not None:
        bits.append(f"${lo:,.0f}" + (f"–${hi:,.0f}" if hi != lo else ""))
    sp = signal.get("units_special_offers") or 0
    if sp:
        bits.append(f"{sp} concession{'s' if sp != 1 else ''}")
    if signal.get("next_available") and signal["next_available"] != "Now":
        bits.append(f"next {signal['next_available']}")
    return " · ".join(bits)
