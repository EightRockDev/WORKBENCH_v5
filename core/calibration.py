"""Market Calibration — dynamic underwriting thresholds.

Replaces the hardcoded constants in ``config.py`` with a market-aware layer.
Constants in ``config.py`` remain as the **locked floor** (Brian's ratified
bars). Market data can only **widen** a threshold in the conservative
direction; compression below the floor requires an explicit override.

Architecture
------------
::

    config.py (floors)  ─┐
                         ├─► Threshold registry (this module)
    hampton_roads.db    ─┤        │
      • fred_series      │        │  compute_calibration()
      • va_multifamily_  │        ▼
        inventory        │   market_values per threshold
      • census_acs       │        │
    workbench.db        ─┤        │  apply_calibration()
      • properties (ALN) │        ▼
                         │   calibration_current (effective values)
                         │   calibration_history (append-only snapshots)
                         │        │
                         │        ▼
                         └─► verdict.py, underwriting UI, exec summary

Threshold direction semantics
-----------------------------
``conservative_up``  — higher is more conservative (e.g. ``GO_CAP``,
                       ``EXIT_CAP_DEFAULT``, ``VACANCY_DEFAULT``,
                       ``MIN_DEBT_YIELD``). Widening = increase from floor.
                       ``effective = max(floor, market)``.

``conservative_down`` — lower is more conservative (e.g.
                        ``PPU_GO_<CITY>``, ``PPU_WATCH_<CITY>``).
                        Widening = decrease from floor.
                        ``effective = min(floor, market)``.

Floor-as-permanent-direction:
    The market value is permitted to move the effective threshold ONLY
    further in the conservative direction. If market data wants to compress
    (i.e. relax) the threshold below the floor, ``effective_value`` stays
    pinned to ``floor_value`` and the market reading is recorded but not
    applied — the UI surfaces this as "Market suggests 7.21%; floored at
    7.50%; override required to relax."

Brian override path:
    An explicit ``override_value`` in ``calibration_current`` overrides both
    floor and market. This is the only way to compress a threshold below
    its locked floor. The UI exposes a confirm-flow that writes the override
    with a reason string.

Refresh cadence:
    The Monday ETL job (``etl-weekly-monday``) chains
    ``scripts/recalibrate_thresholds.py`` after the ETL pull. Compute reads
    the freshly-updated ``hampton_roads.db`` and rewrites
    ``calibration_current`` + appends to ``calibration_history``.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import config


# ---------------------------------------------------------------------------
# DB paths — calibration tables live in workbench.db alongside ALN properties.
# ETL data is read from hampton-roads-etl/hampton_roads.db.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
WORKBENCH_DB_PATH = _HERE.parent.parent / "data" / "workbench.db"
CALIBRATION_SCHEMA_PATH = _HERE.parent.parent / "data" / "calibration_schema.sql"
ETL_DB_PATH = _HERE.parent.parent.parent / "hampton-roads-etl" / "hampton_roads.db"


# ---------------------------------------------------------------------------
# Threshold dataclass — what consumers read
# ---------------------------------------------------------------------------

Direction = Literal["conservative_up", "conservative_down"]
Units = Literal["pct", "ratio", "usd", "x"]
Category = Literal["returns", "debt", "ppu", "operating"]
EffectiveSource = Literal["floor", "market", "override"]


@dataclass(frozen=True)
class Threshold:
    """Calibrated underwriting threshold with full provenance.

    `value` is what verdict.py / sensitivity.py / UI components compare
    against. `floor_value` is the locked literal from config.py. `market_value`
    is the live computed candidate (None if compute failed). `effective_source`
    indicates which of the three is currently in effect.
    """

    name: str
    display_label: str
    units: Units
    direction: Direction
    category: Category
    floor_value: float
    market_value: float | None
    market_source: str | None
    market_as_of: dt.date | None
    override_value: float | None
    override_reason: str | None
    override_set_at: dt.date | None
    override_set_by: str | None
    effective_value: float
    effective_source: EffectiveSource
    last_compute_at: dt.datetime
    last_apply_at: dt.datetime
    notes: str = ""

    # Convenience aliases used by consumers ----------------------------------
    @property
    def value(self) -> float:
        """Shortcut for `effective_value` — the number to compare against."""
        return self.effective_value

    @property
    def is_market_widened(self) -> bool:
        """True if market data pushed the threshold further than the floor."""
        return self.effective_source == "market"

    @property
    def is_overridden(self) -> bool:
        return self.effective_source == "override"

    def format_value(self) -> str:
        """Human-readable formatting matching Eight Rock conventions."""
        return _format_for_units(self.effective_value, self.units)


# ---------------------------------------------------------------------------
# Threshold registry — the source of truth for what gets calibrated and how
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Spec:
    name: str
    display_label: str
    units: Units
    direction: Direction
    category: Category
    floor_value: float
    notes: str = ""


def _build_specs() -> list[_Spec]:
    """Build the threshold registry from `config.py` floors.

    PPU specs are expanded per Hampton Roads city. All other thresholds are
    market-wide.
    """
    specs: list[_Spec] = [
        # Returns / verdict bars --------------------------------------------
        _Spec(
            name="GO_CAP",
            display_label="GO Cap Rate",
            units="pct", direction="conservative_up", category="returns",
            floor_value=config.GO_CAP,
            notes=(
                "Lowest cap rate at which a deal still earns a GO verdict. "
                "Market input: max(10Y Treasury + spread, "
                "city-median going-in cap implied from ALN+assessor data)."
            ),
        ),
        _Spec(
            name="WATCH_CAP",
            display_label="WATCH Cap Rate",
            units="pct", direction="conservative_up", category="returns",
            floor_value=config.WATCH_CAP,
            notes="Drops 50 bps below GO_CAP — moves in lockstep.",
        ),
        _Spec(
            name="NOGO_CAP",
            display_label="NO-GO Cap Floor",
            units="pct", direction="conservative_up", category="returns",
            floor_value=config.NOGO_CAP,
            notes="Hard floor. Calibrated to track 10Y + 235 bps.",
        ),
        # Debt --------------------------------------------------------------
        _Spec(
            name="MIN_DEBT_YIELD",
            display_label="Min Refi Debt Yield",
            units="pct", direction="conservative_up", category="debt",
            floor_value=0.07,  # Currently lives in risk_metrics.py as MIN_DEBT_YIELD
            notes=(
                "Lender stress-case minimum. Flexes with MORTGAGE30US — debt "
                "yield must spread above the amortized debt constant."
            ),
        ),
        # Operating ---------------------------------------------------------
        _Spec(
            name="VACANCY_DEFAULT",
            display_label="Default Vacancy",
            units="pct", direction="conservative_up", category="operating",
            floor_value=config.VACANCY_DEFAULT,
            notes=(
                "Market input: max of (1 - ALN city occupancy median) and "
                "ACS rental vacancy rate by city."
            ),
        ),
        # Exit cap ----------------------------------------------------------
        _Spec(
            name="EXIT_CAP_DEFAULT",
            display_label="Default Exit Cap",
            units="pct", direction="conservative_up", category="returns",
            floor_value=config.EXIT_CAP_DEFAULT,
            notes="Going-in cap + 25 bps (Eight Rock convention).",
        ),
    ]

    # Per-city PPU ceilings — one threshold per (city, tier) pair.
    for city, tiers in config.CITY_PPU_CEILINGS.items():
        if "go" in tiers:
            specs.append(_Spec(
                name=f"PPU_GO_{_normalize_city(city)}",
                display_label=f"{city} GO PPU Ceiling",
                units="usd", direction="conservative_down", category="ppu",
                floor_value=float(tiers["go"]),
                notes=(
                    f"70th-percentile defensible PPU for {city} Class C, "
                    "from last-90-day va_multifamily_inventory sales."
                ),
            ))
        if "watch" in tiers:
            specs.append(_Spec(
                name=f"PPU_WATCH_{_normalize_city(city)}",
                display_label=f"{city} WATCH PPU Ceiling",
                units="usd", direction="conservative_down", category="ppu",
                floor_value=float(tiers["watch"]),
                notes="GO PPU × ~1.07 — moves in lockstep with GO ceiling.",
            ))

    return specs


def _normalize_city(city: str) -> str:
    """Norfolk -> NORFOLK; Virginia Beach -> VIRGINIA_BEACH."""
    return city.upper().replace(" ", "_")


def _denormalize_city(token: str) -> str:
    """VIRGINIA_BEACH -> Virginia Beach. Inverse of `_normalize_city`."""
    return " ".join(part.title() for part in token.split("_"))


SPECS: list[_Spec] = _build_specs()
SPECS_BY_NAME: dict[str, _Spec] = {s.name: s for s in SPECS}


# ---------------------------------------------------------------------------
# DB connection helpers
# ---------------------------------------------------------------------------

def _resolve_db_path(db_path: Path | None) -> Path:
    """Late-bind WORKBENCH_DB_PATH so tests can monkeypatch the module
    attribute. Default parameter values capture WORKBENCH_DB_PATH at
    function DEFINITION time, which is too early for monkeypatch-based
    test isolation (see test_verdict.py's _calibration_falls_back_to_floor
    fixture).
    """
    if db_path is not None:
        return db_path
    import sys
    mod = sys.modules[__name__]
    return mod.WORKBENCH_DB_PATH


def _ensure_calibration_tables(db_path: Path | None = None) -> None:
    """Idempotent: create calibration tables in workbench.db if missing."""
    db_path = _resolve_db_path(db_path)
    if not CALIBRATION_SCHEMA_PATH.is_file():
        raise FileNotFoundError(
            f"Calibration schema SQL missing at {CALIBRATION_SCHEMA_PATH}"
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = CALIBRATION_SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)


def _connect_etl_readonly() -> sqlite3.Connection | None:
    if not ETL_DB_PATH.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{ETL_DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# Compute layer — pull live data and propose market values
# ---------------------------------------------------------------------------

# Spread (in fractional terms, i.e. 0.0300 = 300 bps) between 10Y Treasury
# and the GO cap floor. Industry standard for Class C value-add is ~300 bps;
# Eight Rock's locked 7.5% floor at a 4.5% 10Y is exactly 300 bps.
CAP_SPREAD_OVER_10Y = 0.0300
NOGO_SPREAD_OVER_10Y = 0.0235  # 7.50% - 5.15% historical mid
WATCH_SPREAD_BELOW_GO = 0.0050  # WATCH = GO - 50 bps
EXIT_CAP_PREMIUM_OVER_GOING_IN = 0.0025  # +25 bps
PPU_WATCH_MULTIPLIER = 1.075  # WATCH ceiling = GO × 1.075 (matches SUMMARY-FORMAT.md)
GOING_IN_CAP_PERCENTILE_BY_CITY = 50  # median


@dataclass
class ComputedThreshold:
    """One proposed market value before floor resolution + apply."""
    name: str
    market_value: float | None
    market_source: str
    market_as_of: dt.date | None
    notes: str = ""


def _latest_fred_value(series_id: str) -> tuple[float | None, dt.date | None]:
    db = _connect_etl_readonly()
    if db is None:
        return None, None
    try:
        cur = db.execute(
            "SELECT value, date FROM fred_series "
            "WHERE series_id = ? AND value IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (series_id,),
        )
        row = cur.fetchone()
        if not row:
            return None, None
        return float(row[0]), dt.date.fromisoformat(row[1])
    except (sqlite3.Error, ValueError, TypeError):
        return None, None
    finally:
        db.close()


def _amortized_debt_constant(annual_rate_pct: float, amort_months: int = 300) -> float:
    """Annual debt constant for a 25-yr amortizing loan at the given rate.

    Rate is in percent (e.g. 6.5 for 6.5%). Returns decimal (e.g. 0.0810).
    """
    r_monthly = (annual_rate_pct / 100.0) / 12.0
    if r_monthly <= 0:
        return 1.0 / amort_months * 12  # straight-line approximation
    pmt_per_dollar = r_monthly / (1.0 - (1.0 + r_monthly) ** (-amort_months))
    return pmt_per_dollar * 12.0


def _city_median_going_in_cap(city: str) -> float | None:
    """Estimate median going-in cap rate for a city from ALN + assessor data.

    Method:
      1. Pull ALN properties matching the city.
      2. For each, estimate NOI: avg_rent × 12 × units × (1 - max(0.07, 1 - occ))
         × (1 - expense_ratio_by_class).
      3. Pull assessed_value from va_multifamily_inventory and adjust for
         the Virginia state-mandated 100% market-value assessment ratio
         (we apply a 0.85 reassessment haircut as a conservative proxy
         for the gap between assessor + actual market value).
      4. Match ALN <-> assessor records by address (city + first 12 chars of
         normalized street).
      5. Implied cap = NOI_est / market_value_est.
      6. Return median.

    This is a noisy proxy. Movement matters more than level. If the join
    matches fewer than 5 properties, returns None.
    """
    if not WORKBENCH_DB_PATH.is_file():
        return None

    try:
        # ALN rows (in workbench.db)
        with sqlite3.connect(WORKBENCH_DB_PATH) as wb:
            wb.row_factory = sqlite3.Row
            aln_rows = wb.execute(
                """
                SELECT name, address, units, avg_rent, occupancy_pct, asset_class
                FROM properties
                WHERE city = ?
                  AND units IS NOT NULL AND units > 0
                  AND avg_rent IS NOT NULL AND avg_rent > 0
                  AND asset_class = 'C'
                """,
                (city,),
            ).fetchall()
    except sqlite3.Error:
        return None

    if not aln_rows:
        return None

    # Assessor rows (in hampton_roads.db)
    db = _connect_etl_readonly()
    if db is None:
        return None
    try:
        asr_rows = db.execute(
            """
            SELECT address, assessed_value
            FROM va_multifamily_inventory
            WHERE city = ? AND assessed_value > 0
            """,
            (city,),
        ).fetchall()
    finally:
        db.close()

    if not asr_rows:
        return None

    asr_by_norm: dict[str, float] = {}
    for addr, av in asr_rows:
        key = _norm_addr(addr)
        if key and key not in asr_by_norm:
            asr_by_norm[key] = float(av)

    expense_ratios = config.EXPENSE_RATIOS

    implied_caps: list[float] = []
    for row in aln_rows:
        norm = _norm_addr(row["address"])
        av = asr_by_norm.get(norm)
        if av is None:
            continue
        # Conservative reassessment ratio (assessor ~ 85% of market for VA HR)
        market_val_est = av / 0.85
        units = float(row["units"])
        avg_rent = float(row["avg_rent"])
        gpr = avg_rent * 12.0 * units
        occ = row["occupancy_pct"]
        vac = max(0.07, 1.0 - float(occ)) if occ is not None else 0.07
        er = expense_ratios.get(row["asset_class"] or "C", 0.45)
        noi_est = gpr * (1.0 - vac) - gpr * er
        if noi_est <= 0 or market_val_est <= 0:
            continue
        implied_cap = noi_est / market_val_est
        # Sanity-clip the wild tails (poorly matched addresses)
        if 0.02 <= implied_cap <= 0.20:
            implied_caps.append(implied_cap)

    if len(implied_caps) < 5:
        return None
    implied_caps.sort()
    # Median (50th pctile)
    n = len(implied_caps)
    if n % 2:
        return implied_caps[n // 2]
    return 0.5 * (implied_caps[n // 2 - 1] + implied_caps[n // 2])


def _norm_addr(addr: str | None) -> str:
    if not addr:
        return ""
    s = addr.strip().upper()
    # Collapse double spaces, strip punctuation that varies between systems
    for ch in (",", ".", "#"):
        s = s.replace(ch, "")
    s = " ".join(s.split())
    # First 30 chars is usually plenty to ID the building
    return s[:30]


def _city_recent_sale_ppu(city: str, days: int = 90, min_sales: int = 5) -> float | None:
    """70th-percentile PPU from va_multifamily_inventory last-{days} sales.

    Joins to ALN by address (best-effort) to get unit count. If a sale's
    address doesn't match an ALN property with units, the sale is skipped.

    Returns None if fewer than `min_sales` matched.
    """
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()

    db = _connect_etl_readonly()
    if db is None:
        return None
    try:
        sales = db.execute(
            """
            SELECT address, last_sale_date, last_sale_price
            FROM va_multifamily_inventory
            WHERE city = ?
              AND last_sale_price IS NOT NULL
              AND last_sale_price > 100000
              AND last_sale_date IS NOT NULL
              AND last_sale_date >= ?
            """,
            (city, cutoff),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()

    if not sales:
        return None

    # Match sales to ALN unit counts
    try:
        with sqlite3.connect(WORKBENCH_DB_PATH) as wb:
            wb.row_factory = sqlite3.Row
            aln = wb.execute(
                """
                SELECT address, units
                FROM properties
                WHERE city = ? AND units IS NOT NULL AND units > 0
                """,
                (city,),
            ).fetchall()
    except sqlite3.Error:
        return None

    aln_units: dict[str, int] = {}
    for r in aln:
        key = _norm_addr(r["address"])
        if key and key not in aln_units:
            aln_units[key] = int(r["units"])

    ppus: list[float] = []
    for addr, sale_date, sale_price in sales:
        norm = _norm_addr(addr)
        units = aln_units.get(norm)
        if not units or units < 5:  # ignore non-multifamily / mismatch noise
            continue
        ppu = float(sale_price) / float(units)
        if 20_000 <= ppu <= 400_000:  # sanity band for Class C HR
            ppus.append(ppu)

    if len(ppus) < min_sales:
        return None
    ppus.sort()
    # 70th percentile, linear interpolation
    idx_f = 0.70 * (len(ppus) - 1)
    lo = int(idx_f)
    hi = min(lo + 1, len(ppus) - 1)
    frac = idx_f - lo
    return ppus[lo] * (1.0 - frac) + ppus[hi] * frac


def _city_acs_rental_vacancy(city: str) -> float | None:
    """Census ACS rental vacancy rate by city.

    The puller writes total + vacant housing units; we approximate rental
    vacancy with overall vacancy because the ACS table here doesn't break it
    out per-tenure. (Renter-occupied vs total is in census_acs but the puller
    blocked on API key for the rental-vacancy series specifically — see
    reference_etl_refresh_schedule.md.)
    """
    db = _connect_etl_readonly()
    if db is None:
        return None
    try:
        cur = db.execute(
            """
            SELECT vacant_housing_units, total_housing_units
            FROM census_acs
            WHERE place_name LIKE ?
            ORDER BY acs_year DESC
            LIMIT 1
            """,
            (f"{city}%",),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        return None
    finally:
        db.close()

    if not row or not row[0] or not row[1]:
        return None
    try:
        return float(row[0]) / float(row[1])
    except (TypeError, ZeroDivisionError):
        return None


def _aln_city_vacancy(city: str) -> float | None:
    """1 - median(occupancy) for Class C ALN properties in the city."""
    if not WORKBENCH_DB_PATH.is_file():
        return None
    try:
        with sqlite3.connect(WORKBENCH_DB_PATH) as wb:
            rows = wb.execute(
                """
                SELECT occupancy_pct FROM properties
                WHERE city = ?
                  AND asset_class = 'C'
                  AND occupancy_pct IS NOT NULL
                """,
                (city,),
            ).fetchall()
    except sqlite3.Error:
        return None
    occs = [float(r[0]) for r in rows if r[0] is not None]
    if len(occs) < 5:
        return None
    occs.sort()
    n = len(occs)
    median_occ = occs[n // 2] if n % 2 else 0.5 * (occs[n // 2 - 1] + occs[n // 2])
    return max(0.0, 1.0 - median_occ)


def compute_calibration() -> list[ComputedThreshold]:
    """Produce a market value for every threshold in `SPECS`.

    Each returned `ComputedThreshold` carries the proposed `market_value`,
    a `market_source` label that explains where the number came from, and
    the as-of date. `market_value=None` means we couldn't compute (e.g.
    sparse city data, missing FRED series) — `apply_calibration()` will then
    leave that threshold pinned to its floor.
    """
    today = dt.date.today()
    out: list[ComputedThreshold] = []

    # --- 10Y-driven cap thresholds ----------------------------------------
    dgs10_val, dgs10_date = _latest_fred_value("DGS10")
    if dgs10_val is not None:
        ten_yr = dgs10_val / 100.0  # FRED stores percent; convert to fraction
        ten_yr_label = f"{dgs10_val:.2f}% as of {dgs10_date}"

        # GO cap floor from spread vs. 10Y treasury
        spread_floor = ten_yr + CAP_SPREAD_OVER_10Y

        # Take the conservative max of (10Y+spread, city-median going-in cap
        # across all HR cities).
        city_medians: list[float] = []
        for city in config.CITY_PPU_CEILINGS:
            mc = _city_median_going_in_cap(city)
            if mc is not None:
                city_medians.append(mc)
        # Use the LOWER 25th percentile of city medians as the "market floor"
        # — if a quarter of cities are clearing cheaper than this, the bar
        # must hold there. This biases conservative.
        if city_medians:
            city_medians.sort()
            idx = max(0, int(0.25 * (len(city_medians) - 1)))
            city_floor = city_medians[idx]
            go_cap_market = max(spread_floor, city_floor)
            source = (
                f"max(10Y {dgs10_val:.2f}% + {int(CAP_SPREAD_OVER_10Y*1e4)} bps, "
                f"city-median going-in cap {city_floor:.2%})"
            )
        else:
            go_cap_market = spread_floor
            source = f"10Y {dgs10_val:.2f}% + {int(CAP_SPREAD_OVER_10Y*1e4)} bps"

        out.append(ComputedThreshold(
            name="GO_CAP",
            market_value=go_cap_market,
            market_source=source,
            market_as_of=dgs10_date,
            notes=ten_yr_label,
        ))
        out.append(ComputedThreshold(
            name="WATCH_CAP",
            market_value=go_cap_market - WATCH_SPREAD_BELOW_GO,
            market_source="GO_CAP - 50 bps",
            market_as_of=dgs10_date,
        ))
        out.append(ComputedThreshold(
            name="NOGO_CAP",
            market_value=ten_yr + NOGO_SPREAD_OVER_10Y,
            market_source=f"10Y {dgs10_val:.2f}% + {int(NOGO_SPREAD_OVER_10Y*1e4)} bps",
            market_as_of=dgs10_date,
        ))

        # EXIT_CAP_DEFAULT = going-in (GO_CAP) + 25 bps
        out.append(ComputedThreshold(
            name="EXIT_CAP_DEFAULT",
            market_value=go_cap_market + EXIT_CAP_PREMIUM_OVER_GOING_IN,
            market_source="GO_CAP + 25 bps",
            market_as_of=dgs10_date,
        ))
    else:
        for nm in ("GO_CAP", "WATCH_CAP", "NOGO_CAP", "EXIT_CAP_DEFAULT"):
            out.append(ComputedThreshold(
                name=nm, market_value=None,
                market_source="FRED DGS10 missing",
                market_as_of=None,
            ))

    # --- 30Y-mortgage-driven debt yield -----------------------------------
    mort_val, mort_date = _latest_fred_value("MORTGAGE30US")
    if mort_val is not None:
        debt_constant = _amortized_debt_constant(mort_val, 300)
        # Lender stress: debt yield ≥ amortized debt constant + 50 bps cushion
        market_min_dy = debt_constant + 0.005
        out.append(ComputedThreshold(
            name="MIN_DEBT_YIELD",
            market_value=market_min_dy,
            market_source=(
                f"amortized debt constant @ {mort_val:.2f}% mortgage "
                f"({debt_constant:.2%}) + 50 bps"
            ),
            market_as_of=mort_date,
        ))
    else:
        out.append(ComputedThreshold(
            name="MIN_DEBT_YIELD", market_value=None,
            market_source="FRED MORTGAGE30US missing",
            market_as_of=None,
        ))

    # --- VACANCY_DEFAULT — market-wide, max across cities -----------------
    city_vacs: list[tuple[str, float]] = []
    for city in config.CITY_PPU_CEILINGS:
        acs = _city_acs_rental_vacancy(city)
        aln = _aln_city_vacancy(city)
        candidates = [v for v in (acs, aln) if v is not None]
        if candidates:
            city_vacs.append((city, max(candidates)))
    if city_vacs:
        # Use the 75th percentile across cities — biased conservative
        city_vacs.sort(key=lambda t: t[1])
        idx = int(0.75 * (len(city_vacs) - 1))
        worst_city, worst_v = city_vacs[idx]
        out.append(ComputedThreshold(
            name="VACANCY_DEFAULT",
            market_value=worst_v,
            market_source=(
                f"75th-pctile city vacancy across HR ({worst_city} drives it); "
                "max(ACS, 1 - ALN occupancy median)"
            ),
            market_as_of=today,
        ))
    else:
        out.append(ComputedThreshold(
            name="VACANCY_DEFAULT", market_value=None,
            market_source="No ACS/ALN vacancy data",
            market_as_of=None,
        ))

    # --- City PPU ceilings — re-fit from recent sales ---------------------
    for city in config.CITY_PPU_CEILINGS:
        go_ppu = _city_recent_sale_ppu(city, days=90, min_sales=5)
        if go_ppu is None:
            # Try 180-day window before giving up
            go_ppu = _city_recent_sale_ppu(city, days=180, min_sales=3)
            window_note = "180-day window (sparse 90d)"
        else:
            window_note = "90-day window"

        token = _normalize_city(city)
        if go_ppu is not None:
            out.append(ComputedThreshold(
                name=f"PPU_GO_{token}",
                market_value=go_ppu,
                market_source=f"70th-pctile of {city} sales, {window_note}",
                market_as_of=today,
            ))
            out.append(ComputedThreshold(
                name=f"PPU_WATCH_{token}",
                market_value=go_ppu * PPU_WATCH_MULTIPLIER,
                market_source=f"GO PPU × {PPU_WATCH_MULTIPLIER}",
                market_as_of=today,
            ))
        else:
            out.append(ComputedThreshold(
                name=f"PPU_GO_{token}", market_value=None,
                market_source=f"No recent {city} sales matched ALN units",
                market_as_of=None,
            ))
            out.append(ComputedThreshold(
                name=f"PPU_WATCH_{token}", market_value=None,
                market_source=f"No recent {city} sales matched ALN units",
                market_as_of=None,
            ))

    return out


# ---------------------------------------------------------------------------
# Apply layer — resolve effective value + persist
# ---------------------------------------------------------------------------

def _resolve_effective(
    spec: _Spec,
    market_value: float | None,
    override_value: float | None,
) -> tuple[float, EffectiveSource]:
    """Apply floor + direction semantics to produce the in-effect value.

    Resolution order (most binding first):
      1. Brian override (if set) — wins even below floor
      2. Market vs floor, resolved by direction:
         - conservative_up:  effective = max(floor, market)
         - conservative_down: effective = min(floor, market)
      3. Floor only (when market_value is None or doesn't widen)
    """
    if override_value is not None:
        return override_value, "override"

    if market_value is None:
        return spec.floor_value, "floor"

    if spec.direction == "conservative_up":
        if market_value > spec.floor_value:
            return market_value, "market"
        return spec.floor_value, "floor"
    # conservative_down
    if market_value < spec.floor_value:
        return market_value, "market"
    return spec.floor_value, "floor"


def apply_calibration(
    computed: list[ComputedThreshold] | None = None,
    *,
    db_path: Path | None = None,
) -> list[Threshold]:
    """Write applied thresholds + history snapshot. Returns the list of
    `Threshold` rows now in effect.

    If `computed` is None, calls `compute_calibration()` internally.
    """
    db_path = _resolve_db_path(db_path)
    _ensure_calibration_tables(db_path)
    if computed is None:
        computed = compute_calibration()

    computed_by_name = {c.name: c for c in computed}
    now = dt.datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    out: list[Threshold] = []

    with sqlite3.connect(db_path) as conn:
        # Preload existing overrides so we don't lose them on re-apply
        existing = {
            row[0]: row
            for row in conn.execute(
                "SELECT name, override_value, override_reason, "
                "       override_set_at, override_set_by "
                "FROM calibration_current"
            )
        }

        for spec in SPECS:
            c = computed_by_name.get(spec.name)
            mv = c.market_value if c else None
            msrc = c.market_source if c else None
            mdate = c.market_as_of if c else None
            notes = c.notes if c else ""

            existing_row = existing.get(spec.name)
            override_value: float | None = None
            override_reason: str | None = None
            override_set_at: str | None = None
            override_set_by: str | None = None
            if existing_row is not None:
                _, override_value, override_reason, override_set_at, override_set_by = existing_row

            effective_value, effective_source = _resolve_effective(
                spec, mv, override_value,
            )

            conn.execute(
                """
                INSERT INTO calibration_current
                  (name, display_label, units, direction, category,
                   floor_value, market_value, market_source, market_as_of,
                   override_value, override_reason, override_set_at, override_set_by,
                   effective_value, effective_source,
                   last_compute_at, last_apply_at, notes)
                VALUES
                  (?, ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?,
                   ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                   display_label = excluded.display_label,
                   units = excluded.units,
                   direction = excluded.direction,
                   category = excluded.category,
                   floor_value = excluded.floor_value,
                   market_value = excluded.market_value,
                   market_source = excluded.market_source,
                   market_as_of = excluded.market_as_of,
                   effective_value = excluded.effective_value,
                   effective_source = excluded.effective_source,
                   last_compute_at = excluded.last_compute_at,
                   last_apply_at = excluded.last_apply_at,
                   notes = excluded.notes
                """,
                (
                    spec.name, spec.display_label, spec.units,
                    spec.direction, spec.category,
                    spec.floor_value, mv, msrc,
                    mdate.isoformat() if mdate else None,
                    override_value, override_reason, override_set_at, override_set_by,
                    effective_value, effective_source,
                    now_iso, now_iso, notes,
                ),
            )

            conn.execute(
                """
                INSERT INTO calibration_history
                  (name, snapshot_at, market_value, effective_value,
                   effective_source, market_source, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.name, now_iso, mv, effective_value,
                    effective_source, msrc, notes,
                ),
            )

            out.append(Threshold(
                name=spec.name,
                display_label=spec.display_label,
                units=spec.units,
                direction=spec.direction,
                category=spec.category,
                floor_value=spec.floor_value,
                market_value=mv,
                market_source=msrc,
                market_as_of=mdate,
                override_value=override_value,
                override_reason=override_reason,
                override_set_at=(
                    dt.date.fromisoformat(override_set_at)
                    if override_set_at else None
                ),
                override_set_by=override_set_by,
                effective_value=effective_value,
                effective_source=effective_source,
                last_compute_at=now,
                last_apply_at=now,
                notes=notes,
            ))
        conn.commit()

    return out


# ---------------------------------------------------------------------------
# Override management — Brian's compress-below-floor lever
# ---------------------------------------------------------------------------

def set_override(
    name: str,
    value: float,
    reason: str,
    set_by: str = "brian",
    *,
    db_path: Path | None = None,
) -> Threshold:
    """Set an explicit override that bypasses floor + market.

    The override stays in effect until cleared with `clear_override()`. Every
    subsequent `apply_calibration()` preserves the override and re-resolves
    `effective_value` against it.
    """
    if name not in SPECS_BY_NAME:
        raise ValueError(f"Unknown threshold: {name}")
    db_path = _resolve_db_path(db_path)
    _ensure_calibration_tables(db_path)

    now = dt.datetime.now().isoformat(timespec="seconds")
    today = dt.date.today().isoformat()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM calibration_current WHERE name = ?", (name,),
        ).fetchone()
        if row is None:
            apply_calibration(db_path=db_path)
        conn.execute(
            """
            UPDATE calibration_current
               SET override_value = ?,
                   override_reason = ?,
                   override_set_at = ?,
                   override_set_by = ?,
                   effective_value = ?,
                   effective_source = 'override',
                   last_apply_at = ?
             WHERE name = ?
            """,
            (value, reason, today, set_by, value, now, name),
        )
        conn.execute(
            """
            INSERT INTO calibration_history
              (name, snapshot_at, market_value, effective_value,
               effective_source, market_source, notes)
            SELECT name, ?, market_value, ?, 'override', market_source,
                   'override set: ' || ?
            FROM calibration_current WHERE name = ?
            """,
            (now, value, reason, name),
        )
        conn.commit()

    return get_threshold(name, db_path=db_path)  # type: ignore[return-value]


def clear_override(
    name: str,
    *,
    db_path: Path | None = None,
) -> Threshold:
    """Remove an override and re-resolve against floor + market."""
    if name not in SPECS_BY_NAME:
        raise ValueError(f"Unknown threshold: {name}")
    db_path = _resolve_db_path(db_path)
    _ensure_calibration_tables(db_path)

    spec = SPECS_BY_NAME[name]
    now = dt.datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT market_value FROM calibration_current WHERE name = ?",
            (name,),
        ).fetchone()
        mv = float(row[0]) if row and row[0] is not None else None
        eff, src = _resolve_effective(spec, mv, None)
        conn.execute(
            """
            UPDATE calibration_current
               SET override_value = NULL,
                   override_reason = NULL,
                   override_set_at = NULL,
                   override_set_by = NULL,
                   effective_value = ?,
                   effective_source = ?,
                   last_apply_at = ?
             WHERE name = ?
            """,
            (eff, src, now, name),
        )
        conn.execute(
            """
            INSERT INTO calibration_history
              (name, snapshot_at, market_value, effective_value,
               effective_source, market_source, notes)
            SELECT name, ?, market_value, ?, ?, market_source, 'override cleared'
            FROM calibration_current WHERE name = ?
            """,
            (now, eff, src, name),
        )
        conn.commit()

    return get_threshold(name, db_path=db_path)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Read accessors — what verdict.py and UI components call
# ---------------------------------------------------------------------------

def get_threshold(
    name: str,
    *,
    db_path: Path | None = None,
) -> Threshold | None:
    """Fetch a single calibrated threshold by name. Returns None if not yet
    calibrated (caller should fall back to the floor in `config.py`)."""
    if name not in SPECS_BY_NAME:
        return None
    db_path = _resolve_db_path(db_path)
    if not db_path.is_file():
        return _floor_only_threshold(SPECS_BY_NAME[name])
    try:
        _ensure_calibration_tables(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM calibration_current WHERE name = ?", (name,),
            ).fetchone()
    except sqlite3.Error:
        return _floor_only_threshold(SPECS_BY_NAME[name])
    if row is None:
        return _floor_only_threshold(SPECS_BY_NAME[name])
    return _row_to_threshold(row)


def get_all_thresholds(
    *,
    db_path: Path | None = None,
) -> list[Threshold]:
    """All calibrated thresholds in canonical order. Missing rows fall back
    to floor-only `Threshold` objects so UI can always render every spec."""
    db_path = _resolve_db_path(db_path)
    if not db_path.is_file():
        return [_floor_only_threshold(s) for s in SPECS]
    _ensure_calibration_tables(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = {
                r["name"]: r for r in conn.execute(
                    "SELECT * FROM calibration_current",
                )
            }
    except sqlite3.Error:
        return [_floor_only_threshold(s) for s in SPECS]

    out: list[Threshold] = []
    for spec in SPECS:
        row = rows.get(spec.name)
        if row is None:
            out.append(_floor_only_threshold(spec))
        else:
            out.append(_row_to_threshold(row))
    return out


def effective_value(
    name: str,
    *,
    db_path: Path | None = None,
) -> float:
    """The number consumers should compare against. Always returns a float
    (floor when no calibration has run)."""
    t = get_threshold(name, db_path=db_path)
    if t is None:
        spec = SPECS_BY_NAME.get(name)
        if spec is None:
            raise KeyError(f"Unknown threshold: {name}")
        return spec.floor_value
    return t.effective_value


def get_history(
    name: str,
    *,
    db_path: Path | None = None,
    days: int | None = None,
) -> list[tuple[dt.datetime, float, EffectiveSource]]:
    """Return (snapshot_at, effective_value, effective_source) tuples for the
    threshold, newest first. Used by the UI for 30/90/365-day deltas."""
    db_path = _resolve_db_path(db_path)
    if not db_path.is_file():
        return []
    _ensure_calibration_tables(db_path)
    sql = (
        "SELECT snapshot_at, effective_value, effective_source "
        "FROM calibration_history WHERE name = ? "
    )
    params: list[Any] = [name]
    if days is not None:
        cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
        sql += "AND snapshot_at >= ? "
        params.append(cutoff)
    sql += "ORDER BY snapshot_at DESC"
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    out: list[tuple[dt.datetime, float, EffectiveSource]] = []
    for snap, val, src in rows:
        try:
            ts = dt.datetime.fromisoformat(snap)
        except (TypeError, ValueError):
            continue
        out.append((ts, float(val), src))
    return out


def delta_bps_over(
    name: str,
    days: int,
    *,
    db_path: Path | None = None,
) -> float | None:
    """Movement of `effective_value` over the last `days`, in basis points
    for percentage thresholds, in dollars for USD thresholds, in raw units
    for ratios/multiples. Returns None if no historical reading exists.

    For a pct threshold currently 7.85% that was 7.50% N days ago, returns 35.
    """
    history = get_history(name, db_path=db_path)
    if len(history) < 2:
        return None
    now = dt.datetime.now()
    target_ts = now - dt.timedelta(days=days)
    # find the snapshot CLOSEST to target_ts (and at least `days` old)
    candidate = None
    for ts, v, _src in history:
        if ts <= target_ts:
            candidate = (ts, v)
            break
    # If no snapshot is that old yet, use the oldest one we have
    if candidate is None:
        candidate = (history[-1][0], history[-1][1])
    current_val = history[0][1]
    prior_val = candidate[1]
    spec = SPECS_BY_NAME.get(name)
    if spec is None:
        return None
    if spec.units in ("pct", "ratio"):
        return (current_val - prior_val) * 10_000.0  # bps
    # usd / x: return raw delta
    return current_val - prior_val


# ---------------------------------------------------------------------------
# City-level convenience: read PPU ceilings as the nested dict shape that
# config.CITY_PPU_CEILINGS exposes — drop-in replacement for consumers.
# ---------------------------------------------------------------------------

def effective_city_ppu_ceilings(
    *,
    db_path: Path | None = None,
) -> dict[str, dict[str, float]]:
    """Mirror of config.CITY_PPU_CEILINGS shape, but with calibrated values."""
    out: dict[str, dict[str, float]] = {}
    for city in config.CITY_PPU_CEILINGS:
        token = _normalize_city(city)
        go = effective_value(f"PPU_GO_{token}", db_path=db_path)
        watch = effective_value(f"PPU_WATCH_{token}", db_path=db_path)
        out[city] = {"go": go, "watch": watch}
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _floor_only_threshold(spec: _Spec) -> Threshold:
    """Build a Threshold object representing a never-yet-calibrated row.
    Effective = floor, source = "floor". Used when workbench.db is missing
    or `apply_calibration()` has never been run."""
    now = dt.datetime.now()
    return Threshold(
        name=spec.name,
        display_label=spec.display_label,
        units=spec.units,
        direction=spec.direction,
        category=spec.category,
        floor_value=spec.floor_value,
        market_value=None,
        market_source=None,
        market_as_of=None,
        override_value=None,
        override_reason=None,
        override_set_at=None,
        override_set_by=None,
        effective_value=spec.floor_value,
        effective_source="floor",
        last_compute_at=now,
        last_apply_at=now,
        notes=spec.notes,
    )


def _row_to_threshold(row: sqlite3.Row) -> Threshold:
    def _date(v: Any) -> dt.date | None:
        if not v:
            return None
        try:
            return dt.date.fromisoformat(str(v)[:10])
        except (TypeError, ValueError):
            return None

    def _dt(v: Any) -> dt.datetime:
        try:
            return dt.datetime.fromisoformat(str(v))
        except (TypeError, ValueError):
            return dt.datetime.now()

    return Threshold(
        name=row["name"],
        display_label=row["display_label"],
        units=row["units"],
        direction=row["direction"],
        category=row["category"],
        floor_value=float(row["floor_value"]),
        market_value=(
            float(row["market_value"])
            if row["market_value"] is not None else None
        ),
        market_source=row["market_source"],
        market_as_of=_date(row["market_as_of"]),
        override_value=(
            float(row["override_value"])
            if row["override_value"] is not None else None
        ),
        override_reason=row["override_reason"],
        override_set_at=_date(row["override_set_at"]),
        override_set_by=row["override_set_by"],
        effective_value=float(row["effective_value"]),
        effective_source=row["effective_source"],
        last_compute_at=_dt(row["last_compute_at"]),
        last_apply_at=_dt(row["last_apply_at"]),
        notes=row["notes"] or "",
    )


def _format_for_units(value: float, units: Units) -> str:
    if units == "pct":
        return f"{value*100:.2f}%"
    if units == "ratio":
        return f"{value:.2f}x"
    if units == "x":
        return f"{value:.2f}x"
    if units == "usd":
        return f"${value:,.0f}"
    return f"{value}"
