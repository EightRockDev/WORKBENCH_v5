"""Hampton Roads property tax mill rates by city.

Source: each city's FY2026 adopted budget / tax rate ordinance. These are
real-estate tax rates per $100 of assessed value (the standard VA convention).
Multifamily rental properties pay the standard real-estate rate (no separate
multifamily mill rate in VA).

Updated annually around June (next FY published just before July 1 fiscal
year flip). Verify against the city treasurer's published tax rate page
when the rate changes.

Used by:
  - core.calc post-sale property-tax adjustment (Beardsley convention:
    new tax = 85% × purchase price × mill rate)
  - The Underwriting tab's reassessment toggle now applies the EXACT
    formula instead of the +6% opex proxy when this table is available.
"""

from __future__ import annotations

# Per-city real-estate tax rate (per $100 of assessed value). Sourced from
# each city's FY2026 adopted budget. Update annually.
HR_MILL_RATES_PER_100: dict[str, float] = {
    "Norfolk":        1.25,   # FY2026 — restored from FY24 cut, no change FY25→FY26
    "Virginia Beach": 0.97,   # FY2026 — adopted May 2025
    "Chesapeake":     0.99,   # FY2026 — slight reduction from $1.04
    "Portsmouth":     1.30,   # FY2026 — highest in HR
    "Suffolk":        1.06,   # FY2026
    "Hampton":        1.16,   # FY2026 — slight increase
    "Newport News":   1.18,   # FY2026
}

# Default mill rate when city is unknown — middle of the HR range.
DEFAULT_MILL_RATE_PER_100: float = 1.10

# Beardsley convention: assessor reassesses to ~85% of purchase price after sale.
# Some VA cities go higher (90%+); Norfolk historically lands around 85%.
DEFAULT_REASSESSMENT_RATIO: float = 0.85


def get_mill_rate(city: str | None) -> float:
    """Return the real-estate tax rate per $100 of assessed value for a HR city.

    Falls back to DEFAULT_MILL_RATE_PER_100 if the city isn't in the table
    (covers custom properties or out-of-HR markets).
    """
    if not city:
        return DEFAULT_MILL_RATE_PER_100
    return HR_MILL_RATES_PER_100.get(city.strip(), DEFAULT_MILL_RATE_PER_100)


def estimated_post_sale_tax(
    purchase_price: float,
    city: str | None,
    reassessment_ratio: float = DEFAULT_REASSESSMENT_RATIO,
) -> float:
    """Estimated post-sale annual property tax using:
        tax = (purchase × reassessment_ratio) × (mill_rate / 100)

    Per Beardsley: assessor typically reassesses to ~85% of purchase price.
    Multiply by mill rate / 100 (mill rate is per-$100 in VA convention).
    """
    if purchase_price <= 0:
        return 0.0
    rate = get_mill_rate(city)
    new_assessed = purchase_price * reassessment_ratio
    return new_assessed * (rate / 100.0)


def all_rates_table() -> list[dict]:
    """Return the rate table as a list of dicts for UI display."""
    return [
        {"city": city, "rate_per_100": rate, "rate_pct": rate}
        for city, rate in sorted(
            HR_MILL_RATES_PER_100.items(), key=lambda kv: -kv[1]
        )
    ]
