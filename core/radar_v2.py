"""Module C — Forced-Seller Radar v2 (spec §6.1).

A single **0-100 distress score** per property, fusing six signals:

  1. loan-maturity proximity   (GRANITE HUD feed + FHFA PUDB)      - shipped feed
  2. tax delinquency           (municipal portals)
  3. permit decay              (Census BPS + city permit feeds)
  4. ownership tenure          (deed chain)                        - shipped
  5. listing appearance/removal(scraper)
  6. POC signals               (**new in v5.0**, from Module A):
        deceased owner flag, out-of-state mailing-address change,
        entity dissolution filing

Every score carries an **evidence panel** — the component contributions and the
human-readable facts behind them, so a number is never unexplained.

Deterministic and LLM-free (Section 11): pure arithmetic over public + resolved
data, reproducible for the same inputs.

Acceptance (§6.1): backtested against the 7-city deed chains, the top decile of
trailing score must capture >=3x the base rate of subsequent sales. See
:func:`backtest` and tests/test_radar_v2.py.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# Component weights — sum to 1.0. Tuned so no single signal can alone push a
# property into the top decile; distress is a fusion judgement (§6.1).
WEIGHTS = {
    "loan_maturity": 0.28,
    "tax_delinquency": 0.20,
    "poc_signals": 0.18,      # new in v5.0 — the Module A edge
    "tenure": 0.14,
    "permit_decay": 0.10,
    "listing": 0.10,
}

BAND_GO = 70        # >=70 -> "act now"
BAND_WATCH = 45     # 45-69 -> "watch"


@dataclass
class Component:
    key: str
    score: float                 # 0-100 before weighting
    weight: float
    evidence: list[str] = field(default_factory=list)
    known: bool = True           # False = the underlying data is ABSENT

    @property
    def contribution(self) -> float:
        # An unknown component contributes nothing. Absence of data is not
        # evidence of distress (owner report 2026-08-04: "no permit history on
        # record" was scoring 75, "no deed record" 30 — fabricated points).
        if not self.known:
            return 0.0
        return round(self.score * self.weight, 2)


def _unknown(key: str, note: str) -> "Component":
    """A component whose data is not on file: score 0, contributes nothing,
    and says so plainly rather than asserting a fact we never checked."""
    return Component(key, 0.0, WEIGHTS[key], [note], known=False)


@dataclass
class RadarScore:
    property_id: str
    score: float
    components: list[Component] = field(default_factory=list)

    @property
    def known_count(self) -> int:
        return sum(1 for c in self.components if c.known)

    @property
    def band(self) -> str:
        # With no real data on any signal, the score is 0 by construction — but
        # that is "we don't know", not "healthy". Say so, so a 0 is never read
        # as an all-clear.
        if self.known_count == 0:
            return "NO DATA"
        return "ACT" if self.score >= BAND_GO else ("WATCH" if self.score >= BAND_WATCH else "MONITOR")

    @property
    def coverage_note(self) -> str:
        n = self.known_count
        total = len(self.components)
        if n == 0:
            return ("No distress signals on file yet — connect the loan / tax / "
                    "permit / deed feeds or enter them to score this property.")
        return f"Scored on {n} of {total} signals with data; the rest are not on file."

    @property
    def evidence(self) -> list[str]:
        # Known signals first (ranked by contribution), then the not-on-file
        # ones, so real facts lead and gaps are visible but don't masquerade.
        out: list[str] = [self.coverage_note]
        known = sorted((c for c in self.components if c.known), key=lambda c: -c.contribution)
        unknown = [c for c in self.components if not c.known]
        for c in [*known, *unknown]:
            out.extend(c.evidence)
        return out

    def as_dict(self) -> dict:
        return {
            "property_id": self.property_id, "score": self.score, "band": self.band,
            "known_count": self.known_count, "coverage_note": self.coverage_note,
            "components": [{"key": c.key, "score": c.score, "weight": c.weight,
                            "contribution": c.contribution, "evidence": c.evidence,
                            "known": c.known}
                           for c in self.components],
        }


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


# ---------------------------------------------------------------------------
# Component scorers — each returns 0-100 plus its evidence
# ---------------------------------------------------------------------------

def score_loan_maturity(maturity: dt.date | None, *, today: dt.date,
                        loan_type: str | None = None) -> Component:
    """Closer maturity = more pressure. Peaks inside 12 months; decays past 36."""
    ev: list[str] = []
    if not maturity:
        return _unknown("loan_maturity",
                        "No loan data on file — connect the GRANITE HUD feed")
    months = (maturity.year - today.year) * 12 + (maturity.month - today.month)
    lt = f"{loan_type} " if loan_type else ""
    if months < -6:
        s = 40.0
        ev.append(f"{lt}loan matured {abs(months)} months ago - likely already refinanced or in workout")
    elif months <= 0:
        s = 100.0
        ev.append(f"{lt}loan matured {abs(months)} month(s) ago - acute refinance pressure")
    elif months <= 12:
        s = 100.0 - (months / 12.0) * 20.0        # 100 -> 80
        ev.append(f"{lt}loan matures in {months} month(s) ({maturity:%b %Y}) - inside the refinance window")
    elif months <= 36:
        s = 80.0 - ((months - 12) / 24.0) * 55.0  # 80 -> 25
        ev.append(f"{lt}loan matures {maturity:%b %Y} ({months} months out)")
    else:
        s = 10.0
        ev.append(f"{lt}loan matures {maturity:%b %Y} - well beyond the pressure window")
    return Component("loan_maturity", _clamp(s), WEIGHTS["loan_maturity"], ev)


def score_tax_delinquency(years_delinquent: float | None = 0.0, amount: float | None = None,
                          *, units: int | None = None) -> Component:
    ev: list[str] = []
    if years_delinquent is None:
        return _unknown("tax_delinquency",
                        "No tax-status data — connect the municipal portal")
    if years_delinquent <= 0:
        return Component("tax_delinquency", 0.0, WEIGHTS["tax_delinquency"],
                         ["Property taxes current"])
    s = min(100.0, 45.0 * years_delinquent)
    per_unit = f" (${amount / units:,.0f}/unit)" if amount and units else ""
    amt = f" totaling ${amount:,.0f}{per_unit}" if amount else ""
    ev.append(f"Taxes delinquent {years_delinquent:.1f} year(s){amt}")
    if years_delinquent >= 2:
        ev.append("Multi-year delinquency - tax-sale exposure")
    return Component("tax_delinquency", _clamp(s), WEIGHTS["tax_delinquency"], ev)


def score_permit_decay(permits_last_5y: int = 0, last_permit_year: int | None = None,
                       *, today_year: int) -> Component:
    """No capital going in = deferred maintenance = seller fatigue."""
    ev: list[str] = []
    if permits_last_5y == 0 and last_permit_year is None:
        # No permits AND no permit history at all = we have no permit data, not
        # observed inactivity. Absence is not distress (owner report 2026-08-04).
        return _unknown("permit_decay",
                        "No permit history on record — connect the city permit feed")
    if permits_last_5y == 0:
        gap = today_year - last_permit_year
        s = 75.0 if gap >= 10 else 60.0
        ev.append(f"No permits pulled in 5 years; last permit "
                  f"{last_permit_year} ({gap}y ago)")
    elif permits_last_5y <= 2:
        s = 40.0
        ev.append(f"Only {permits_last_5y} permit(s) in 5 years - light reinvestment")
    else:
        s = 10.0
        ev.append(f"{permits_last_5y} permits in 5 years - actively maintained")
    return Component("permit_decay", _clamp(s), WEIGHTS["permit_decay"], ev)


def score_tenure(last_sale_year: int | None, *, today_year: int) -> Component:
    """Hold-period fatigue: 10-25 years is the classic sell window."""
    ev: list[str] = []
    if not last_sale_year:
        return _unknown("tenure", "No deed record on file — connect the deed feed")
    held = today_year - last_sale_year
    if held < 3:
        s = 5.0
        ev.append(f"Bought {last_sale_year} ({held}y ago) - recent buyer, unlikely seller")
    elif held < 10:
        s = 35.0
        ev.append(f"Held {held} years (since {last_sale_year})")
    elif held <= 25:
        s = 85.0
        ev.append(f"Held {held} years (since {last_sale_year}) - inside the typical sell window")
    else:
        s = 70.0
        ev.append(f"Held {held} years (since {last_sale_year}) - long-term hold, "
                  "often estate/legacy driven")
    return Component("tenure", _clamp(s), WEIGHTS["tenure"], ev)


def score_listing(listed_now: bool = False, delisted_within_days: int | None = None,
                  *, price_cuts: int = 0) -> Component:
    ev: list[str] = []
    if listed_now:
        s = 55.0
        ev.append("Currently listed - motivated but competitive")
        if price_cuts:
            s += min(25.0, 10.0 * price_cuts)
            ev.append(f"{price_cuts} price cut(s) since listing")
    elif delisted_within_days is not None and delisted_within_days <= 365:
        s = 90.0
        ev.append(f"Listed and withdrawn {delisted_within_days} days ago - "
                  "failed sale, owner still wants out")
    else:
        # No positive listing signal and no scraper feed wired = unknown, not a
        # confirmed "not listed". Absence is not a low-distress fact.
        return _unknown("listing",
                        "No listing data — connect the listing scraper")
    return Component("listing", _clamp(s), WEIGHTS["listing"], ev)


def score_poc_signals(pocs: list[dict] | None = None, *,
                      property_state: str | None = None,
                      entity_dissolved: bool = False) -> Component:
    """NEW in v5.0 (§6.1): distress signals only Module A can see."""
    ev: list[str] = []
    s = 0.0
    pocs = pocs or []

    for p in pocs:
        person = p.get("person") or {}
        if person.get("deceased"):
            s = max(s, 95.0)
            ev.append(f"Owner {person.get('full_name','(unnamed)')} flagged deceased "
                      "- estate/probate disposition likely")
        # Out-of-state mailing address = absentee owner, a classic sell signal.
        if property_state:
            for a in (p.get("addresses") or []):
                formatted = (a.get("formatted") or "").upper()
                if a.get("kind") in ("mailing", "current") and formatted:
                    if f" {property_state.upper()}" not in formatted:
                        s = max(s, 60.0)
                        ev.append("Owner mailing address is out of state - absentee owner")
                        break
        if len(p.get("other_properties") or []) >= 5:
            s = max(s, 45.0)
            ev.append(f"Portfolio owner ({len(p['other_properties'])+1} parcels) - "
                      "may trade assets to rebalance")

    if entity_dissolved:
        s = max(s, 90.0)
        ev.append("Owning entity shows a dissolution filing - wind-down in progress")

    if not ev:
        # No adverse signals. Distinguish "we checked resolved contacts and
        # found nothing" (known, low) from "no contacts resolved yet" (unknown)
        # — the latter must not read as an all-clear (owner report 2026-08-04).
        if not pocs:
            return _unknown("poc_signals",
                            "No owner contacts resolved yet — run Resolve Contacts")
        ev.append("No adverse signals among the resolved contacts")
    return Component("poc_signals", _clamp(s), WEIGHTS["poc_signals"], ev)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def score_property(prop: dict, *, pocs: list[dict] | None = None,
                   today: dt.date | None = None, signals: dict | None = None) -> RadarScore:
    """Fuse all six components into one explainable 0-100 score.

    ``signals`` carries data not on the property row yet (tax delinquency, permits,
    listing, loan) so the scorer stays usable before the Phase 0 spine lands.
    """
    today = today or dt.date.today()
    sig = signals or {}
    # Absent signals are passed through as-is (None / 0-with-no-history) so the
    # scorers can tell "not on file" from a real observation. Nothing here
    # invents a default value — that was the source of the fabricated score
    # (owner report 2026-08-04). `years_delinquent`/`permits_last_5y` default to
    # None so an unwired feed reads as unknown, not "current"/"no reinvestment".
    comps = [
        score_loan_maturity(sig.get("loan_maturity"), today=today,
                            loan_type=sig.get("loan_type")),
        score_tax_delinquency(sig.get("years_delinquent"), sig.get("tax_amount"),
                              units=prop.get("units")),
        score_poc_signals(pocs, property_state=prop.get("state"),
                          entity_dissolved=sig.get("entity_dissolved", False)),
        score_tenure(prop.get("last_sold_year") or sig.get("last_sale_year"),
                     today_year=today.year),
        score_permit_decay(sig.get("permits_last_5y", 0), sig.get("last_permit_year"),
                           today_year=today.year),
        score_listing(sig.get("listed_now", False), sig.get("delisted_within_days"),
                      price_cuts=sig.get("price_cuts", 0)),
    ]
    total = round(sum(c.contribution for c in comps), 1)
    return RadarScore(str(prop.get("property_id")), _clamp(total), comps)


# ---------------------------------------------------------------------------
# §6.1 acceptance — backtest lift
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    n: int
    base_rate: float
    top_decile_rate: float
    lift: float
    threshold: float

    @property
    def passes(self) -> bool:
        """Spec target: top-decile score captures >=3x the base rate of sales."""
        return self.lift >= 3.0


def backtest(scored: list[tuple[float, bool]]) -> BacktestResult:
    """``scored`` = [(trailing_score, did_it_trade_within_24_months)].

    Returns the lift of the top-decile score band over the population base rate.
    """
    if not scored:
        return BacktestResult(0, 0.0, 0.0, 0.0, 0.0)
    n = len(scored)
    traded = sum(1 for _, t in scored if t)
    base = traded / n
    ranked = sorted(scored, key=lambda x: -x[0])
    k = max(1, n // 10)
    top = ranked[:k]
    top_rate = sum(1 for _, t in top if t) / len(top)
    lift = (top_rate / base) if base > 0 else 0.0
    return BacktestResult(n, round(base, 4), round(top_rate, 4), round(lift, 2),
                          round(top[-1][0], 1))
