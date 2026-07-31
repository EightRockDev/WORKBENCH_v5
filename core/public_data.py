"""In-workbench public-data pullers: HMDA loans + HUD Fair Market Rents.

Adapted from Eight Rock's hampton-roads-etl pullers so the workbench
FEEDS ITSELF - no file copies from other machines, in any metro (the
50-metro prerequisite). Runs as an autopilot step; writes to the same
ETL database `core/etl_db.py` resolves (creating data/hampton_roads.db
when absent), so GRANITE Loans and the FMR rent signal light up from
the app's own cycle.

Politeness contract (cycles chain every ~35 min):
  * Freshness-gated: a table pulled within REFRESH_DAYS is not re-pulled
    - the step no-ops in seconds.
  * LEI->lender-name lookups (GLEIF, ~3s each) persist in `lei_names`;
    only never-seen LEIs are resolved, so the cost is paid once ever.

Sources (both public):
  * FFIEC HMDA data-browser CSV API - no auth.
  * HUD FMR API - needs the free HUD_API_TOKEN in .env; without it the
    FMR pull skips with instructions (an already-present hud_fmr table,
    e.g. from the copied v2.4.1 db, keeps serving).
"""

from __future__ import annotations

import datetime as dt
import io
import os
import sqlite3
from pathlib import Path

from core import etl_db
from core.market_data import HR_CITY_TO_COUNTY_FIPS_5

# Autopilot runs this module OUTSIDE the app, where nothing else has
# loaded .env - without this, HUD_API_TOKEN added to C:\WORKBENCH_V5\.env
# is invisible to every cycle and the FMR pull skips forever (the exact
# failure verified on the host 2026-07-31). Never overrides real env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HMDA_API = "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"
HUD_FMR_API = "https://www.huduser.gov/hudapi/public/fmr/data"
GLEIF_API = "https://api.gleif.org/api/v1/lei-records"
REFRESH_DAYS = 30
HMDA_YEARS_BACK = 3

_HMDA_COLS = {
    "lei": "lei",
    "state_code": "state_code",
    "county_code": "county_code",
    "census_tract": "census_tract",
    "loan_amount": "loan_amount",
    "loan_purpose": "loan_purpose",
    "dwelling_category": "dwelling_category",
    "action_taken": "action_taken",
    "rate_spread": "rate_spread",
    "loan_to_value_ratio": "loan_to_value",
}


def target_db() -> Path:
    """The ETL db to write: the resolved one, else the preferred home."""
    path = etl_db.resolve_etl_db() or etl_db.preferred_location()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_fresh(db_path: Path, table: str, days: int = REFRESH_DAYS) -> bool:
    """True when `table` was pulled within `days` and has rows."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT last_pull_at, row_count FROM etl_metadata "
                " WHERE table_name = ?", (table,)).fetchone()
        if not row or not row[0] or not row[1]:
            return False
        pulled = dt.datetime.fromisoformat(str(row[0])[:19])
        return (dt.datetime.now() - pulled) < dt.timedelta(days=days)
    except (sqlite3.Error, ValueError):
        return False


def _stamp(conn: sqlite3.Connection, table: str, display: str, source: str,
           rows: int) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
        table_name TEXT PRIMARY KEY, display_name TEXT, description TEXT,
        source_url TEXT, fetch_method TEXT, row_count INTEGER,
        last_pull_at TEXT, last_pull_date TEXT)""")
    now = dt.datetime.now()
    conn.execute(
        """INSERT INTO etl_metadata VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(table_name) DO UPDATE SET row_count=excluded.row_count,
             last_pull_at=excluded.last_pull_at,
             last_pull_date=excluded.last_pull_date""",
        (table, display, f"Pulled in-workbench by core/public_data.py",
         source, "api", rows, now.isoformat(timespec="seconds"),
         now.date().isoformat()))


# ------------------------------------------------------------------ HMDA

def fetch_hmda_year(year: int, county_fips: tuple[str, ...]):
    """One year of multifamily originations as a DataFrame ('' on failure)."""
    import pandas as pd
    import requests
    params = {"states": "VA", "counties": ",".join(county_fips),
              "years": str(year), "actions_taken": "1",
              "dwelling_categories": "Multifamily:Site-Built"}
    try:
        r = requests.get(HMDA_API, params=params, timeout=120)
        if r.status_code == 400:   # legacy parameter fallback
            params.pop("dwelling_categories")
            params["total_units"] = "5-1000"
            r = requests.get(HMDA_API, params=params, timeout=120)
        r.raise_for_status()
        return pd.read_csv(io.StringIO(r.text), low_memory=False)
    except Exception as e:                       # noqa: BLE001 - report, never crash the cycle
        print(f"  [hmda] {year} fetch failed: {type(e).__name__}: {e}")
        return pd.DataFrame()


def normalize_hmda(raw, year: int):
    """Raw HMDA CSV frame -> normalized originations frame (may be empty)."""
    import pandas as pd
    keep = [c for c in raw.columns if c in _HMDA_COLS]
    if not keep:
        return pd.DataFrame()
    norm = raw[keep].rename(columns=_HMDA_COLS)
    norm["year"] = year
    for ncol in ("loan_amount", "rate_spread", "loan_to_value"):
        if ncol in norm.columns:
            norm[ncol] = pd.to_numeric(norm[ncol], errors="coerce")
    return norm


def summarize_lenders(orig):
    """Originations -> per (year, lei, lender_name, county) rollup."""
    return (orig.groupby(["year", "lei", "lender_name", "county_code"],
                         dropna=False)
            .agg(n_originations=("loan_amount", "count"),
                 total_loan_amount=("loan_amount", lambda s: s.dropna().sum()),
                 median_loan_amount=("loan_amount",
                                     lambda s: s.dropna().median()),
                 median_rate_spread=("rate_spread",
                                     lambda s: s.dropna().median()))
            .reset_index())


def resolve_lender_names(db_path: Path, leis: list[str]) -> dict[str, str]:
    """LEI -> legal name, via the persistent lei_names cache + GLEIF for
    never-seen LEIs only. Cache survives across cycles - GLEIF is hit
    once per lender, ever."""
    import requests
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS lei_names (
            lei TEXT PRIMARY KEY, name TEXT, resolved_at TEXT)""")
        cache = dict(conn.execute("SELECT lei, name FROM lei_names"))
        missing = [x for x in leis if x and x not in cache]
        if missing:
            print(f"  [hmda] resolving {len(missing)} new LEIs via GLEIF...")
        for i, lei in enumerate(missing, 1):
            name = None
            try:
                r = requests.get(f"{GLEIF_API}/{lei}", timeout=15,
                                 headers={"Accept": "application/vnd.api+json"})
                if r.status_code == 200:
                    name = (r.json().get("data", {}).get("attributes", {})
                            .get("entity", {}).get("legalName", {}).get("name"))
            except Exception:                    # noqa: BLE001
                pass
            cache[lei] = name
            conn.execute("INSERT OR REPLACE INTO lei_names VALUES (?,?,?)",
                         (lei, name, dt.datetime.now().isoformat()))
            if i % 25 == 0:
                print(f"  [hmda]   {i}/{len(missing)}")
        conn.commit()
    return cache


def pull_hmda(db_path: Path | None = None) -> int:
    """Pull-or-skip HMDA into the ETL db. Returns originations written
    (0 = fresh-skip or total failure; the printout says which)."""
    import pandas as pd
    db = db_path or target_db()
    if is_fresh(db, "hmda_originations"):
        print("  [hmda] fresh (pulled within "
              f"{REFRESH_DAYS} days) - skipping")
        return 0
    counties = tuple(HR_CITY_TO_COUNTY_FIPS_5.values())
    year_now = dt.date.today().year
    frames = [normalize_hmda(fetch_hmda_year(y, counties), y)
              for y in range(year_now - HMDA_YEARS_BACK, year_now)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        print("  [hmda] nothing pulled (network or schema shift) - "
              "existing tables left untouched")
        return 0
    orig = pd.concat(frames, ignore_index=True)
    names = resolve_lender_names(db, [str(x) for x in
                                      orig["lei"].dropna().unique()])
    orig["lender_name"] = orig["lei"].map(lambda x: names.get(str(x)))
    summary = summarize_lenders(orig)
    with sqlite3.connect(db) as conn:
        orig.to_sql("hmda_originations", conn, if_exists="replace",
                    index=False)
        summary.to_sql("hmda_lender_summary", conn, if_exists="replace",
                       index=False)
        _stamp(conn, "hmda_originations", "HMDA multifamily originations",
               HMDA_API, len(orig))
        _stamp(conn, "hmda_lender_summary", "HMDA lender rollup",
               HMDA_API, len(summary))
    print(f"  [hmda] wrote {len(orig):,} originations, "
          f"{len(summary):,} lender-summary rows")
    return len(orig)


# ---------------------------------------------------------------- HUD FMR

def pull_hud_fmr(db_path: Path | None = None) -> int:
    """Pull-or-skip HUD FMR. Needs HUD_API_TOKEN (free); without it the
    pull skips with instructions and any existing hud_fmr keeps serving."""
    import requests
    db = db_path or target_db()
    token = os.environ.get("HUD_API_TOKEN", "").strip()
    # A newly added token forces the FIRST live pull even if copied data
    # looks fresh - only our own in-workbench stamp counts as "pulled
    # with the token" (owner adds token -> next cycle pulls live).
    pulled_by_us = False
    try:
        with sqlite3.connect(db) as _c:
            row = _c.execute("SELECT description FROM etl_metadata "
                             "WHERE table_name = 'hud_fmr'").fetchone()
            pulled_by_us = bool(row and "in-workbench" in str(row[0]))
    except sqlite3.Error:
        pass
    if is_fresh(db, "hud_fmr", days=90) and (not token or pulled_by_us):
        # Say WHY in the report - "fresh - skipping" alone hid the real
        # question (is a token even visible?) during the 2026-07-31 debug.
        why = ("already pulled live with token" if pulled_by_us
               else "no HUD_API_TOKEN visible to this run")
        print(f"  [hud_fmr] fresh - skipping ({why})")
        return 0
    if not token:
        print("  [hud_fmr] HUD_API_TOKEN not set - skipping. Free token:")
        print("    https://www.huduser.gov/portal/dataset/fmr-api.html")
        print("    then add HUD_API_TOKEN=... to C:\\WORKBENCH_V5\\.env")
        return 0
    year = dt.date.today().year
    rows = []
    for fips5 in HR_CITY_TO_COUNTY_FIPS_5.values():
        data = None
        try:
            for entity in (f"{fips5}99999", fips5):
                for y in (year, year - 1):
                    r = requests.get(
                        f"{HUD_FMR_API}/{entity}", params={"year": y},
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=60)
                    if r.status_code == 200:
                        data = r.json()
                        break
                if data:
                    break
        except Exception as e:                   # noqa: BLE001
            print(f"  [hud_fmr] {fips5}: {type(e).__name__}: {e}")
        if not data:
            continue
        payload = data.get("data", data) if isinstance(data, dict) else {}
        basic = payload.get("basicdata", {})
        if isinstance(basic, list):
            basic = basic[0] if basic else {}
        rows.append((fips5, payload.get("year", year),
                     basic.get("Efficiency") or basic.get("efficiency"),
                     basic.get("One-Bedroom") or basic.get("one-bedroom"),
                     basic.get("Two-Bedroom") or basic.get("two-bedroom"),
                     basic.get("Three-Bedroom") or basic.get("three-bedroom"),
                     basic.get("Four-Bedroom") or basic.get("four-bedroom")))
    if not rows:
        print("  [hud_fmr] nothing pulled - existing table left untouched")
        return 0
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS hud_fmr (
            fips_county_5 TEXT, year INTEGER, fmr_efficiency REAL,
            fmr_one_bedroom REAL, fmr_two_bedroom REAL,
            fmr_three_bedroom REAL, fmr_four_bedroom REAL)""")
        conn.executemany(
            "DELETE FROM hud_fmr WHERE fips_county_5 = ? AND year = ?",
            [(r[0], r[1]) for r in rows])
        conn.executemany("INSERT INTO hud_fmr VALUES (?,?,?,?,?,?,?)", rows)
        _stamp(conn, "hud_fmr", "HUD Fair Market Rents", HUD_FMR_API,
               len(rows))
    print(f"  [hud_fmr] wrote {len(rows)} county rows")
    return len(rows)
