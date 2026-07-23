"""Forced-Seller Radar — scores every HR multifamily property on a 0-100
distress likelihood.

Inputs (all already in the workbench's ETL DB):
  - HMDA origination vintage (2020-22 loans facing rate-shock maturities)
  - Recent assessment jumps >20% (proxy for recent sales)
  - Local unemployment trend (BLS LAUS)
  - Current 10Y Treasury vs origination-era rate (FRED)
  - Ownership concentration (multi-property owners more likely to need liquidity)
  - Class C 1970s-80s vintage stock (more deferred-maintenance pressure)

Outputs:
  - Top-N ranked list with the exact reason each property was flagged
  - Score breakdown so Brian can see what drove the rank
  - Per-property "skip trace" columns (owner LLC, address, last sale)

This operationalizes the Matrix Feb 2026 thesis: 2020-22 vintage bridge
loans + 25-50% rate shock on refi = forced sellers. The radar finds them
before brokers do.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# DB paths
_HERE = Path(__file__).resolve()
WORKBENCH_DB = _HERE.parent.parent / "data" / "workbench.db"
ETL_DB = _HERE.parent.parent.parent / "hampton-roads-etl" / "hampton_roads.db"


def _parse_sale_date(value: Any) -> dt.date | None:
    """Parse a sale date from va_multifamily_inventory.

    VA cities use multiple formats:
      - Unix milliseconds as integer or float (Norfolk, Chesapeake)
      - ISO date string "2021-11-03T00:00:00.000" (some cities)
      - ISO date "2021-11-03"
      - None / empty string

    Returns None for any unparseable value (caller treats as "no sale").
    """
    if value is None or value == "":
        return None
    # Try numeric (unix ms) first
    try:
        v = float(value)
        if v > 1e9:    # plausible unix-ms timestamp
            return dt.date.fromtimestamp(v / 1000.0)
    except (TypeError, ValueError, OSError):
        pass
    # ISO date string
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        pass
    return None


@dataclass
class DistressScore:
    """One property's distress assessment."""
    parcel_id: str
    city: str
    address: str
    owner: str
    year_built: int | None
    last_sale_date: str
    last_sale_price: float | None
    assessed_value: float
    units_estimate: int | None = None

    # Score components (each 0-100)
    vintage_loan_score: float = 0.0
    rate_shock_score: float = 0.0
    assessment_jump_score: float = 0.0
    holding_period_score: float = 0.0
    market_softness_score: float = 0.0
    institutional_owner_score: float = 0.0

    # Aggregate
    total_score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "parcel_id": self.parcel_id, "city": self.city,
            "address": self.address, "owner": self.owner,
            "year_built": self.year_built,
            "last_sale_date": self.last_sale_date,
            "last_sale_price": self.last_sale_price,
            "assessed_value": self.assessed_value,
            "units_estimate": self.units_estimate,
            "score": round(self.total_score, 1),
            "reasons": "; ".join(self.reasons),
        }


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _score_last_sale_vintage(last_sale_raw: Any,
                              last_sale_price: float | None) -> tuple[float, str | None]:
    """+30 if last sale was 2020-2022 (bridge-era debt).

    These loans typically have 3-5 year terms with rate adjustments or
    balloon payments coming due 2025-2027. Properties bought 2020-22 face
    the maturity wall NOW.
    """
    if not last_sale_raw or not last_sale_price or last_sale_price < 1_000_000:
        return 0.0, None
    sale_date = _parse_sale_date(last_sale_raw)
    if sale_date is None:
        return 0.0, None
    if dt.date(2020, 1, 1) <= sale_date <= dt.date(2022, 12, 31):
        return 30.0, f"Bought {sale_date.year} (bridge-loan vintage; maturity coming due)"
    if dt.date(2023, 1, 1) <= sale_date <= dt.date(2023, 12, 31):
        # 2023 buyers locked in high rates already; less distress angle
        return 8.0, f"Bought {sale_date.year} (high-rate vintage)"
    return 0.0, None


def _score_assessment_jump(prior_value: float | None,
                            latest_value: float | None) -> tuple[float, str | None]:
    """+15-25 for assessment jumps that signal recent sale at higher price."""
    if not prior_value or not latest_value or prior_value <= 0:
        return 0.0, None
    pct_change = (latest_value - prior_value) / prior_value
    if pct_change >= 0.40:
        return 25.0, f"Assessment +{pct_change*100:.0f}% (likely 2025 sale at premium)"
    if pct_change >= 0.20:
        return 15.0, f"Assessment +{pct_change*100:.0f}% (signal of recent sale/reassessment)"
    return 0.0, None


def _score_holding_period(last_sale_raw: Any) -> tuple[float, str | None]:
    """+10 for institutional holding period reaching the 5-7 yr target hold."""
    if not last_sale_raw:
        return 0.0, None
    sale_date = _parse_sale_date(last_sale_raw)
    if sale_date is None:
        return 0.0, None
    years_held = (dt.date.today() - sale_date).days / 365.25
    if 5 <= years_held <= 8:
        return 10.0, f"In typical institutional disposition window ({years_held:.1f} yr hold)"
    if years_held > 15:
        return 8.0, f"Long-tenure owner ({years_held:.0f} yr hold; sometimes a tax-motivated sale)"
    return 0.0, None


def _score_institutional_owner(owner: str | None) -> tuple[float, str | None]:
    """+10 if institutional/LLC owner (vs mom-and-pop)."""
    if not owner:
        return 0.0, None
    owner_upper = owner.upper()
    institutional_tokens = (
        "LLC", " LP", "L.P.", "FUND", "CAPITAL", "PARTNERS", "INC",
        "CORP", "TRUST", "REIT", "INVESTORS", "ACQUISITION",
    )
    for tok in institutional_tokens:
        if tok in owner_upper:
            return 10.0, f"Institutional owner ({owner.title()[:40]}; structured for liquidity)"
    return 0.0, None


def _score_market_softness(city: str, unemployment_lookup: dict[str, float]) -> tuple[float, str | None]:
    """+5-15 if local unemployment elevated or trending up."""
    rate = unemployment_lookup.get(city)
    if rate is None:
        return 0.0, None
    if rate >= 6.0:
        return 15.0, f"{city} unemployment elevated ({rate:.1f}%)"
    if rate >= 5.0:
        return 8.0, f"{city} unemployment moderate ({rate:.1f}%)"
    return 0.0, None


def _score_rate_shock(
    last_sale_raw: Any,
    current_10y: float | None,
    historical_10y_at_sale: float | None,
) -> tuple[float, str | None]:
    """+15-30 for properties bought when rates were low (refi shock at maturity)."""
    if not last_sale_raw or current_10y is None:
        return 0.0, None
    sale_date = _parse_sale_date(last_sale_raw)
    if sale_date is None:
        return 0.0, None
    # Estimate historical 10Y at sale time
    if historical_10y_at_sale is None:
        if dt.date(2020, 3, 1) <= sale_date <= dt.date(2022, 3, 1):
            historical_10y_at_sale = 1.2  # ZIRP era estimate
        elif dt.date(2022, 4, 1) <= sale_date <= dt.date(2023, 12, 31):
            historical_10y_at_sale = 3.5
        else:
            return 0.0, None
    shock = current_10y - historical_10y_at_sale
    if shock >= 3.0:
        return 30.0, (
            f"Rate shock: bought at ~{historical_10y_at_sale:.1f}% 10Y, "
            f"refi at {current_10y:.1f}% (+{shock*100:.0f} bps)"
        )
    if shock >= 2.0:
        return 20.0, (
            f"Rate shock: bought at ~{historical_10y_at_sale:.1f}% 10Y, "
            f"refi at {current_10y:.1f}% (+{shock*100:.0f} bps)"
        )
    if shock >= 1.0:
        return 10.0, f"Moderate rate shock: +{shock*100:.0f} bps since sale"
    return 0.0, None


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_inventory(min_assessed: float = 2_000_000) -> list[dict]:
    """Pull multifamily properties from va_multifamily_inventory."""
    if not ETL_DB.is_file():
        return []
    with sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT city, parcel_id, gpin, address, owner, year_built,
                   class_description, assessed_value, last_sale_date,
                   last_sale_price, latest_fiscal_year
            FROM va_multifamily_inventory
            WHERE assessed_value >= ?
            ORDER BY assessed_value DESC
        """, (min_assessed,)).fetchall()
    return [dict(r) for r in rows]


def _load_assessment_jumps() -> dict[tuple[str, str], tuple[float, float]]:
    """Map (city, parcel_id) → (prior_assessed, latest_assessed)."""
    if not ETL_DB.is_file():
        return {}
    with sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True) as conn:
        rows = conn.execute("""
            WITH ranked AS (
                SELECT city, parcel_id, fiscal_year, assessed_value,
                       ROW_NUMBER() OVER (PARTITION BY city, parcel_id
                                          ORDER BY fiscal_year DESC) AS rn
                FROM va_assessment_history
                WHERE assessed_value > 0
            )
            SELECT a.city, a.parcel_id, b.assessed_value AS prior, a.assessed_value AS latest
            FROM ranked a
            JOIN ranked b ON a.city = b.city AND a.parcel_id = b.parcel_id
                         AND a.rn = 1 AND b.rn = 2
            WHERE b.assessed_value > 0
        """).fetchall()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def _load_unemployment() -> dict[str, float]:
    """City → latest unemployment rate %."""
    if not ETL_DB.is_file():
        return {}
    with sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True) as conn:
        rows = conn.execute("""
            SELECT county, unemployment_rate_pct
            FROM bls_laus
            WHERE unemployment_rate_pct IS NOT NULL
            ORDER BY year DESC, month DESC
        """).fetchall()
    out: dict[str, float] = {}
    for city, rate in rows:
        if city not in out:
            out[city] = float(rate)
    return out


def _load_current_10y() -> float | None:
    """Latest DGS10 from FRED."""
    if not ETL_DB.is_file():
        return None
    with sqlite3.connect(f"file:{ETL_DB}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT value FROM fred_series WHERE series_id='DGS10' AND value IS NOT NULL "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return float(row[0]) if row else None


def _estimate_units(class_description: str | None, assessed_value: float) -> int | None:
    """Heuristic unit count from assessor class code. Chesapeake/Newport News
    use 4-digit class codes (3352, 3346); Norfolk uses 405/406/407.

    Rough fallback when ALN doesn't have the match.
    """
    if not class_description:
        return None
    s = class_description.strip()
    if s.startswith("405"):
        return 100   # 49+ units typical
    if s.startswith("406"):
        return 30
    if s.startswith("407"):
        return 20
    if s.startswith("404"):
        return 12
    if s.startswith("403"):
        return 10
    if s.startswith("3352"):
        return 80    # Chesapeake apartment large
    if s.startswith("3346"):
        return 30    # smaller multifamily
    return None


# ---------------------------------------------------------------------------
# Main scoring
# ---------------------------------------------------------------------------

def score_all_properties(
    min_assessed_value: float = 2_000_000,
    city_filter: list[str] | None = None,
) -> list[DistressScore]:
    """Compute distress scores for all HR multifamily properties.

    Returns a sorted list (highest score first). Default min_assessed_value
    filters out tiny parcels; bump for institutional-only view.
    """
    properties = _load_inventory(min_assessed_value)
    if city_filter:
        properties = [p for p in properties if p["city"] in city_filter]

    jumps = _load_assessment_jumps()
    unemployment = _load_unemployment()
    current_10y = _load_current_10y()

    scores: list[DistressScore] = []
    for p in properties:
        sale_date_obj = _parse_sale_date(p["last_sale_date"])
        ds = DistressScore(
            parcel_id=p["parcel_id"],
            city=p["city"],
            address=p["address"] or "",
            owner=p["owner"] or "",
            year_built=int(p["year_built"]) if p["year_built"] else None,
            last_sale_date=sale_date_obj.isoformat() if sale_date_obj else "",
            last_sale_price=float(p["last_sale_price"] or 0) or None,
            assessed_value=float(p["assessed_value"] or 0),
            units_estimate=_estimate_units(p["class_description"], p["assessed_value"]),
        )

        components = [
            _score_last_sale_vintage(p["last_sale_date"], p["last_sale_price"]),
            _score_rate_shock(p["last_sale_date"], current_10y, None),
            _score_assessment_jump(*jumps.get((p["city"], p["parcel_id"]), (None, None))),
            _score_holding_period(p["last_sale_date"]),
            _score_institutional_owner(p["owner"]),
            _score_market_softness(p["city"], unemployment),
        ]
        component_names = (
            "vintage_loan_score", "rate_shock_score", "assessment_jump_score",
            "holding_period_score", "institutional_owner_score",
            "market_softness_score",
        )
        for (val, reason), name in zip(components, component_names):
            setattr(ds, name, val)
            if reason:
                ds.reasons.append(reason)

        ds.total_score = sum(c[0] for c in components)
        # Cap at 100
        ds.total_score = min(100.0, ds.total_score)
        scores.append(ds)

    scores.sort(key=lambda s: -s.total_score)
    return scores


def top_n_candidates(n: int = 25, **kwargs) -> list[DistressScore]:
    """Return the top-N highest-scoring properties — Brian's ready-to-mail list."""
    return [s for s in score_all_properties(**kwargs) if s.total_score > 0][:n]
