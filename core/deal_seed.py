"""Asset-aware seeding of the FIRST numbers on an untouched property.

Owner ask 2026-08-13, after reading what an untouched property showed:
purchase price was `units x $130,000` - a fixed mid-range Class-C
Hampton Roads $/unit that ignored assessed value, sale history and city
alike (and assumed 100 units, so a $13M seed, whenever the roll carried
no unit count). NOI came off a flat $1,500 rent whenever the backbone had
no estimate. The numbers looked like underwriting and were constants.

This module picks the best available ANCHOR for price, in priority order,
and reports which one it used so the UI can say so inline:

  1. recent_sale   - the property's own arm's-length transfer, trended to
                     today. It actually traded; nothing beats that. Taken
                     from the county deed index, or from the last-sold
                     columns on the property's own record.
  2. assessed      - assessor value / assessment ratio. Assessors target
                     market value at a published ratio, so this is a real
                     per-asset number, not a market constant. The curated
                     records carry it PER UNIT, the backbone carries a
                     total; both are read.
  3. ppu           - the legacy units x $/unit fallback, for rows carrying
                     neither. Explicitly labelled as a market placeholder.

Correction 2026-08-27 (owner: "if I've favorited a property, I should not
see this message"). The seed only ever looked at the county-sourced keys -
`assessed_value` and the deed index - so a property in the owner's OWN
records was told "no sale or assessed value on this parcel" while its row
carried `last_sold_amount`, `last_sold_year` and `assessed_value_per_unit`,
and its real `avg_rent` was captioned "market placeholder - no rent
estimate for this asset". Both statements were false about data already on
the screen. A seed that ignores the record it claims to be seeded from is
worse than no seed: it is a market constant wearing the record's clothes.

Every result is a SEED, never an underwrite: the basis string is rendered
next to the field so the number is never mistaken for analysis.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

# Mid-range Class C Hampton Roads $/unit - the pre-2026-08-13 sole basis,
# now the last resort.
FALLBACK_PPU = 130_000

# Units assumed when the roll carries no count. The old code used 100,
# which seeded $13M on an unknown-size parcel - the single most misleading
# number on the page. A small-property assumption fails visibly instead.
ASSUMED_UNITS = 24

# Assessors target market value at a published ratio (same constant the
# post-sale tax reassessment uses).
ASSESSMENT_RATIO = 0.85

# Sales older than this are ignored as an anchor - too stale to trend.
MAX_SALE_AGE_Y = 7

# Annual appreciation applied when trending an older sale to today.
# Deliberately conservative: a seed that runs hot is worse than one that
# runs cold, because it flatters the going-in cap.
TREND_PER_YEAR = 0.03

# Fallback rent when the backbone has no estimate for the asset.
FALLBACK_RENT = 1_500


@dataclass
class DealSeed:
    """Seeded first numbers plus the evidence behind them."""
    purchase_price: float
    noi: float
    units: int
    units_assumed: bool = False
    price_basis: str = "ppu"          # recent_sale | assessed | ppu
    price_note: str = ""              # human-readable, rendered inline
    rent_basis: str = "fallback"      # listings | hud_fmr | record | fallback
    rent_note: str = ""
    evidence: list = field(default_factory=list)

    @property
    def is_anchored(self) -> bool:
        """True when price came from THIS asset, not a market constant."""
        return self.price_basis in ("recent_sale", "assessed")


def _year_of(date_str) -> int | None:
    if not date_str:
        return None
    s = str(date_str)[:4]
    return int(s) if s.isdigit() else None


def _price_from_sale(prop: dict, db_path: Path | None) -> tuple | None:
    """Most recent credible arm's-length sale, trended to today."""
    try:
        from core.sale_history import sale_history_for
        sales = sale_history_for(prop, db_path=db_path) or []
    except Exception:
        return None
    this_year = dt.date.today().year
    for s in sales:                       # newest-first
        try:
            price = float(s.get("price") or 0)
        except (TypeError, ValueError):
            continue
        year = _year_of(s.get("date"))
        if price < 50_000 or year is None:
            continue                      # $1/$10 deed transfers, gifts
        age = this_year - year
        if age < 0 or age > MAX_SALE_AGE_Y:
            continue
        trended = price * ((1 + TREND_PER_YEAR) ** age)
        note = (f"last sale ${price:,.0f} ({year})"
                + (f", trended +{TREND_PER_YEAR:.0%}/yr to today"
                   if age else ""))
        return trended, note, {"kind": "sale", "price": price, "year": year}
    return None


def _price_from_record_sale(prop: dict) -> tuple | None:
    """The last sale on the property's OWN record (curated pool).

    `properties` stores a sale YEAR, not a date, so this cannot be folded
    into the deed-index reader above — but it is the same evidence, and
    it is the sale the screener already shows the owner in the Last Sale
    column. Ignoring it while printing "no sale on this parcel" is the
    bug this function exists to close.
    """
    try:
        price = float(prop.get("last_sold_amount") or 0)
    except (TypeError, ValueError):
        return None
    year = _year_of(prop.get("last_sold_year"))
    if price < 50_000 or year is None:
        return None
    age = dt.date.today().year - year
    if age < 0 or age > MAX_SALE_AGE_Y:
        return None
    trended = price * ((1 + TREND_PER_YEAR) ** age)
    note = (f"last sale ${price:,.0f} ({year}) on the property record"
            + (f", trended +{TREND_PER_YEAR:.0%}/yr to today" if age else ""))
    return trended, note, {"kind": "sale", "price": price, "year": year,
                           "from": "record"}


def _price_from_assessment(prop: dict) -> tuple | None:
    def _f(key: str) -> float:
        try:
            return float(prop.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    av = _f("assessed_value")
    per_unit = _f("assessed_value_per_unit")
    detail = ""
    if av < 50_000 and per_unit > 0:
        # The curated pool stores assessment PER UNIT (data/legacy_loader).
        try:
            units = int(prop.get("units") or 0)
        except (TypeError, ValueError):
            units = 0
        if units > 0:
            av = per_unit * units
            detail = f" ({units:,} units × ${per_unit:,.0f}/unit assessed)"
    if av < 50_000:
        return None
    est = av / ASSESSMENT_RATIO
    note = (f"assessed ${av:,.0f}{detail} ÷ {ASSESSMENT_RATIO:.0%} "
            "assessment ratio")
    return est, note, {"kind": "assessed", "assessed_value": av}


def _assessed_from_backbone(prop: dict, db_path) -> tuple | None:
    """The assessor's value off the county row this property is matched to.

    The curated records carry no `assessed_value` column, but most of them
    ARE matched to a backbone parcel through `property_crosswalk` — which
    is where the assessment already sits, pulled nightly. Same bridge
    `core.sale_history` uses for the parcel id: the data is in hand, this
    is a read, not a new source.
    """
    legacy_id = prop.get("property_id")
    if not legacy_id:
        return None
    import sqlite3

    from core.sale_history import _muni_db_path
    try:
        path = _muni_db_path(db_path)
    except Exception:
        return None
    if path is None or not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT p.assessed_value FROM property_crosswalk x "
            "  JOIN properties_8r p ON p.property_id = x.r8_property_id "
            " WHERE x.legacy_property_id = ?", (str(legacy_id),)).fetchone()
    except sqlite3.Error:
        return None        # no crosswalk/backbone on this box - pre-Phase 0
    finally:
        conn.close()
    try:
        av = float(row[0]) if row and row[0] else 0.0
    except (TypeError, ValueError):
        return None
    if av < 50_000:
        return None
    note = (f"assessed ${av:,.0f} on the matched county parcel ÷ "
            f"{ASSESSMENT_RATIO:.0%} assessment ratio")
    return av / ASSESSMENT_RATIO, note, {"kind": "assessed",
                                         "assessed_value": av,
                                         "from": "backbone"}


def build_seed(prop: dict, *, db_path: Path | None = None,
               expense_ratio: float | None = None,
               vacancy: float | None = None) -> DealSeed:
    """Seed price + NOI for a property with no saved deal."""
    import config

    units_raw = prop.get("units")
    try:
        units = int(units_raw) if units_raw else 0
    except (TypeError, ValueError):
        units = 0
    units_assumed = units <= 0
    if units_assumed:
        units = ASSUMED_UNITS

    # ---- price: best anchor wins ----------------------------------------
    price = basis = note = None
    evidence: list = []
    hit = _price_from_sale(prop, db_path)
    if hit is not None:
        price, note, ev = hit
        basis = "recent_sale"
        evidence.append(ev)
    if price is None:
        hit = _price_from_record_sale(prop)
        if hit is not None:
            price, note, ev = hit
            basis = "recent_sale"
            evidence.append(ev)
    if price is None:
        hit = _price_from_assessment(prop)
        if hit is not None:
            price, note, ev = hit
            basis = "assessed"
            evidence.append(ev)
    if price is None:
        hit = _assessed_from_backbone(prop, db_path)
        if hit is not None:
            price, note, ev = hit
            basis = "assessed"
            evidence.append(ev)
    if price is None:
        price = units * FALLBACK_PPU
        basis = "ppu"
        note = (f"{units:,} units × ${FALLBACK_PPU:,} market $/unit"
                + (" (unit count assumed - not on the roll)"
                   if units_assumed else "")
                + " - no sale or assessed value on this parcel")

    # ---- NOI: rent x units, class expense ratio -------------------------
    rent = prop.get("avg_rent")
    try:
        rent = float(rent) if rent else 0.0
    except (TypeError, ValueError):
        rent = 0.0
    rent_basis = str(prop.get("rent_source") or "").strip() or "fallback"
    if rent <= 0:
        rent = FALLBACK_RENT
        rent_basis = "fallback"
    elif rent_basis == "fallback":
        # An avg_rent with no rent_source came off the property's own
        # record (the curated pool has no rent_source column). It is the
        # asset's own number - captioning it "no rent estimate for this
        # asset" contradicted the figure printed beside it.
        rent_basis = "record"
    rent_note = {
        "listings": f"${rent:,.0f}/mo from scraped listings",
        "hud_fmr": f"${rent:,.0f}/mo HUD FMR county blend",
        "record": f"${rent:,.0f}/mo average rent on the property record",
    }.get(rent_basis, f"${rent:,.0f}/mo market placeholder - no rent "
                      "estimate for this asset")

    vac = config.VACANCY_DEFAULT if vacancy is None else vacancy
    er = (expense_ratio if expense_ratio is not None
          else config.EXPENSE_RATIOS.get(prop.get("asset_class") or "C", 0.45))
    gpr = units * rent * 12
    noi = gpr * (1 - vac) - gpr * er

    return DealSeed(purchase_price=round(price, -3), noi=round(noi),
                    units=units, units_assumed=units_assumed,
                    price_basis=basis, price_note=note,
                    rent_basis=rent_basis, rent_note=rent_note,
                    evidence=evidence)


def seed_caption(seed: DealSeed) -> str:
    """One line for the UI, directly under the seeded fields."""
    label = {"recent_sale": "Price seeded from this parcel's sale record",
             "assessed": "Price seeded from the assessor's value",
             "ppu": "Price is a MARKET PLACEHOLDER"}[seed.price_basis]
    return (f"{label}: {seed.price_note}. "
            f"NOI seeded from {seed.rent_note}, "
            f"{'assumed ' if seed.units_assumed else ''}{seed.units:,} units. "
            "Seeded values are a starting point, not underwriting.")


def seed_caption_md(seed: DealSeed) -> str:
    """`seed_caption` escaped for a Streamlit markdown surface.

    Streamlit renders `$...$` as LaTeX, so "…$4,200,000 (2024)… NOI seeded
    from $1,159/mo…" silently became one italic maths run with BOTH dollar
    signs eaten — which is how the owner's report of this caption reached
    us reading "46 units × 130,000 market /unit" (2026-08-27). Any money
    string bound for st.info/st.warning/st.markdown needs this.
    """
    return seed_caption(seed).replace("$", r"\$")
