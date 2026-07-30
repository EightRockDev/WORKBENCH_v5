"""Rent signal v1 for the 8R backbone (spec 7.2/7.3 survey values).

The backbone carries no rent data, which made the P0-2 rent-delta gate
pass VACUOUSLY - `avg_rent_delta is None` counted as "ok", so a gate the
spec defines as <= 5% was a no-op. v1 anchors every multifamily row to
HUD Fair Market Rent for its county (already pulled by the ETL) as a
bedroom-blended monthly estimate.

Honesty note: HUD FMR is a 40th-percentile gross-rent standard, not a
market average, so the first measured delta is expected to be LARGE.
That is the real baseline the listings scraper must close - do NOT tune
the blend to flatter the gate; deriving any market-adjustment factor
from ALN data itself would defeat the ALN-free requirement (spec 7.3).

Source hierarchy (rent_source column, best wins, never downgraded):
  listings  - scraped asking/effective rents (pullers/listings)   [next]
  hud_fmr   - county FMR bedroom blend                            [this]
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core import etl_db
from core.market_data import HR_CITY_TO_COUNTY_FIPS_5

# Bedroom mix for a typical Hampton Roads garden community. Assessor rolls
# carry no unit-mix data, so this is a fixed documented assumption:
# mostly 1BR/2BR with thin studio/3BR tails.
FMR_BLEND = (
    ("fmr_efficiency", 0.05),
    ("fmr_one_bedroom", 0.40),
    ("fmr_two_bedroom", 0.45),
    ("fmr_three_bedroom", 0.10),
)


def blend_fmr(rents: dict[str, float | None]) -> float | None:
    """One county's FMR columns -> a single blended monthly estimate.

    Missing bedroom columns drop out and the remaining weights renormalize,
    so a county publishing only 1BR/2BR still yields an estimate.
    """
    parts = [(rents.get(col), w) for col, w in FMR_BLEND
             if rents.get(col) is not None]
    if not parts:
        return None
    total_w = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / total_w


def county_fmr_blend(etl_path: Path | None = None) -> dict[str, float]:
    """county FIPS-5 -> blended monthly FMR (latest year per county)."""
    path = etl_path or etl_db.resolve_etl_db()
    if path is None:
        return {}
    try:
        with sqlite3.connect(path) as db:
            rows = db.execute(
                """SELECT fips_county_5, year, fmr_efficiency,
                          fmr_one_bedroom, fmr_two_bedroom, fmr_three_bedroom
                     FROM hud_fmr"""
            ).fetchall()
    except sqlite3.Error:
        return {}
    latest: dict[str, tuple] = {}
    for fips, year, *rents in rows:
        if fips not in latest or (year or 0) > latest[fips][0]:
            latest[fips] = ((year or 0), rents)
    out: dict[str, float] = {}
    for fips, (_, (eff, br1, br2, br3)) in latest.items():
        est = blend_fmr({"fmr_efficiency": eff, "fmr_one_bedroom": br1,
                         "fmr_two_bedroom": br2, "fmr_three_bedroom": br3})
        if est is not None:
            out[fips] = est
    return out


def ensure_rent_columns(conn: sqlite3.Connection) -> None:
    """Older backbones predate the rent columns; add them in place."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(properties_8r)")}
    if "est_avg_rent" not in cols:
        conn.execute("ALTER TABLE properties_8r ADD COLUMN est_avg_rent REAL")
    if "rent_source" not in cols:
        conn.execute("ALTER TABLE properties_8r ADD COLUMN rent_source TEXT")


def apply_rent_signal(spine_db: Path, etl_path: Path | None = None) -> int:
    """Stamp est_avg_rent on multifamily backbone rows lacking a better
    source. Returns rows updated. No ETL DB (fresh checkout) -> 0, no-op.
    """
    blend = county_fmr_blend(etl_path)
    if not blend:
        return 0
    # Membership must be the shared product rule (is_mf_ten_plus), not a
    # bare units>=10 SQL filter: Norfolk's rolls carry no unit counts at
    # all, so its multifamily is code-only - exactly the rows a units
    # filter would skip.
    from core.phase0 import is_mf_ten_plus
    updated = 0
    with sqlite3.connect(spine_db, timeout=60) as conn:
        ensure_rent_columns(conn)
        for city, fips in HR_CITY_TO_COUNTY_FIPS_5.items():
            est = blend.get(fips)
            if est is None:
                continue
            pids = [pid for pid, uc, u in conn.execute(
                        """SELECT property_id, use_code, units
                             FROM properties_8r
                            WHERE city = ? AND (rent_source IS NULL
                                                OR rent_source = 'hud_fmr')""",
                        (city,))
                    if is_mf_ten_plus(uc, u)]
            conn.executemany(
                """UPDATE properties_8r
                      SET est_avg_rent = ?, rent_source = 'hud_fmr'
                    WHERE property_id = ?""",
                [(round(est, 2), pid) for pid in pids])
            updated += len(pids)
    return updated
