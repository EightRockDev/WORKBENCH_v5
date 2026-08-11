"""Autopilot step: research WHERE property-sales data lives for every tracked
locality - central catalogs only, never the gated city domains.

Owner directive 2026-08-11 ("spend more time researching the methods to pull
data and less time trying to make something difficult work"): when a source
fights back (data.richmondgov.com 403s API reads; 17 localities sit on
"needs vendor discovery" skips), the answer is not tokens/headers/retries -
it is finding the SAME data on an easier shelf. Municipal data is nearly
always published in several places at once, and three central catalogs index
almost all of them without auth or bot-filtering:

  * Socrata Discovery API (api.us.socrata.com/api/catalog/v1) - indexes every
    Socrata domain (Norfolk, Richmond, Cook County, Nashville...) INCLUDING
    column names, so a dataset can be scored sight-unseen even when its home
    domain 403s direct reads.
  * ArcGIS Hub search (hub.arcgis.com/api/v3/datasets) - indexes every public
    Esri feed (VB's Property_Sales_ lives here) with recordCount + field
    list + the FeatureServer URL: everything our existing arcgis adapter
    needs, discovered in one GET.
  * CKAN on data.virginia.gov - the Commonwealth FEDERATES locality datasets
    (Norfolk's FY sales files are already mirrored there) and serves plain
    CSVs from the state domain: a bot-filter-free mirror for VA cities.
  * HRGEO (hrgeo.org, Hampton Roads Planning District Commission) - regional
    Esri hub aggregating exactly the cities whose own portals fail us
    (Hampton, Portsmouth, Suffolk, Newport News).

This step RESEARCHES AND RANKS; it never activates. Candidates land in
data/sales_feeds_candidates.json + the report, each with its evidence
(matched price/date/parcel columns, record count, updated date, which
existing adapter would pull it). Promotion into pull_arcgis_sales.SALES_SOURCES
stays a reviewed edit - the "verified sources ONLY" rule stands.

Host-only (the build env is firewalled from all of these). Self-gated to
every ER_SALES_DISCOVERY_EVERY_DAYS days (default 7) - research, not
hammering.

Env:
  ER_SALES_DISCOVERY_EVERY_DAYS  re-run gate in days (default 7)
  ER_SALES_DISCOVERY_FORCE       =1 runs regardless of the gate
  ER_SALES_DISCOVERY_MARKETS     comma list to restrict (default = tracked)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

EVERY_DAYS = int(os.environ.get("ER_SALES_DISCOVERY_EVERY_DAYS", "7"))
_STAMP = ROOT / "reports" / ".sales-discovery-stamp"
OUT_JSON = ROOT / "data" / "sales_feeds_candidates.json"

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) "
                   "Gecko/20100101 Firefox/153.0"),
    "Accept": "application/json, text/plain, */*",
}

SOCRATA_CATALOG = "https://api.us.socrata.com/api/catalog/v1"
HUB_DATASETS = "https://hub.arcgis.com/api/v3/datasets"
CKAN_VA = "https://data.virginia.gov/api/3/action/package_search"
HRGEO_V3 = "https://www.hrgeo.org/api/v3/datasets"

# Fallback when the DB has no assessor rows yet - the tracked-market set as
# of 2026-08-11 (pull_sales.tracked_localities is the live source of truth).
FALLBACK_LOCALITIES = (
    ("Virginia Beach", "VA"), ("Norfolk", "VA"), ("Chesapeake", "VA"),
    ("Hampton", "VA"), ("Newport News", "VA"), ("Portsmouth", "VA"),
    ("Suffolk", "VA"), ("Richmond", "VA"), ("Alexandria", "VA"),
    ("Arlington", "VA"), ("Raleigh", "NC"), ("Durham", "NC"),
    ("Greensboro", "NC"), ("Winston-Salem", "NC"), ("Greenville", "SC"),
    ("Nashville", "TN"), ("Atlanta", "GA"), ("Savannah", "GA"),
    ("Augusta", "GA"), ("Athens", "GA"), ("Decatur", "GA"),
)

# Column-name evidence. A sales feed we can actually use must name a price,
# a date, and a parcel id somewhere in its schema.
_PRICE_RE = re.compile(
    r"price|consideration|sale_?am(oun)?t|amount", re.I)
_DATE_RE = re.compile(
    r"sales?_?date|transfer_?date|deed_?date|date_?of_?(sale|transfer)|"
    r"recordation", re.I)
_PARCEL_RE = re.compile(
    r"gpin|\bpin\b|parcel|apn|parid|account|map_?(no|num|parcel)|"
    r"property_?id", re.I)
# Sales-shaped dataset titles (vs zoning/permits/crime noise).
_TITLE_RE = re.compile(
    r"sale|transfer|deed|assessment|land\s?book", re.I)

# Terms swept per locality on each catalog. Kept short: catalogs rank well,
# and every extra term is another network call per locality.
QUERY_TERMS = ("property sales", "property transfers", "real estate sales")


# ---------------------------------------------------------------- plumbing

_LAST_ERR = ""


def _get_json(url: str, params: dict | None = None):
    """One catalog GET with small retries; failures are recorded, not fatal -
    a catalog being down should cost one report line, not the cycle."""
    global _LAST_ERR
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=45)
            if r.status_code == 200:
                _LAST_ERR = ""
                try:
                    return r.json()
                except ValueError:
                    _LAST_ERR = "HTTP 200 but non-JSON body"
                    return None
            _LAST_ERR = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            _LAST_ERR = repr(exc)
        time.sleep(1.5 * (attempt + 1))
    return None


def _fresh(now: dt.datetime) -> bool:
    if EVERY_DAYS <= 0:
        return False
    try:
        last = dt.datetime.fromisoformat(_STAMP.read_text().strip())
    except (OSError, ValueError):
        return False
    return (now - last) < dt.timedelta(days=EVERY_DAYS)


def tracked_localities() -> list[tuple[str, str]]:
    """The live market list from the DB, else the static fallback."""
    db = ROOT / "workbench.db"
    try:
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT DISTINCT market, state FROM muni_records "
                "WHERE kind LIKE 'assessor%' AND market IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        if rows:
            return [(m, s or "VA") for m, s in rows]
    except sqlite3.Error:
        pass
    return list(FALLBACK_LOCALITIES)


# ---------------------------------------------------------------- scoring

def score_columns(field_names: list[str]) -> tuple[int, dict[str, str]]:
    """Evidence score from a schema: price 40, date 30, parcel id 20.
    Returns (score, {evidence_kind: matched_column})."""
    hits: dict[str, str] = {}
    for name in field_names:
        n = str(name or "")
        if "price" not in hits and _PRICE_RE.search(n):
            hits["price"] = n
        if "date" not in hits and _DATE_RE.search(n):
            hits["date"] = n
        if "parcel" not in hits and _PARCEL_RE.search(n):
            hits["parcel"] = n
    score = (40 if "price" in hits else 0) + \
            (30 if "date" in hits else 0) + \
            (20 if "parcel" in hits else 0)
    return score, hits


def size_bonus(count) -> int:
    """A citywide sales history is thousands of rows; tiny layers are
    extracts. Unknown counts (CKAN, Socrata catalog) score 0, not negative -
    absence of evidence only fails to help."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return 0
    if n >= 5000:
        return 10
    if n >= 500:
        return 3
    return -10


def freshness_bonus(updated_iso: str | None, now: dt.datetime) -> int:
    if not updated_iso:
        return 0
    try:
        ts = dt.datetime.fromisoformat(
            str(updated_iso).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return 0
    return 5 if (now - ts) < dt.timedelta(days=400) else -5


def locality_match(locality: str, *texts: str) -> bool:
    """The locality's name must appear in the dataset's title/org/domain -
    catalogs return national noise for city-name queries."""
    loc = locality.lower()
    return any(loc in (t or "").lower() for t in texts)


def titled_like_sales(title: str) -> bool:
    return bool(_TITLE_RE.search(title or ""))


# ---------------------------------------------------------------- catalogs

def sweep_socrata(locality: str, now: dt.datetime, fetch=_get_json):
    """Socrata Discovery API - central index of every Socrata domain, with
    column names, so gated domains are scored without touching them."""
    out = []
    for term in QUERY_TERMS:
        js = fetch(SOCRATA_CATALOG, {
            "q": f"{locality} {term}", "only": "dataset", "limit": 20})
        if js is None:
            out.append({"method": "socrata-catalog", "error":
                        f"catalog query failed: {_LAST_ERR}"})
            continue
        for res in js.get("results") or []:
            r = res.get("resource") or {}
            meta = res.get("metadata") or {}
            domain = meta.get("domain") or ""
            title = r.get("name") or ""
            if not titled_like_sales(title):
                continue
            if not locality_match(locality, title, domain,
                                  r.get("description") or ""):
                continue
            cols = [c for c in (r.get("columns_field_name") or [])]
            col_score, hits = score_columns(cols)
            score = col_score + freshness_bonus(r.get("updatedAt"), now)
            out.append({
                "method": "socrata-catalog", "adapter": "socrata_stack",
                "title": title, "domain": domain,
                "resource_id": r.get("id"),
                "base": f"https://{domain}/resource/",
                "updated": r.get("updatedAt"),
                "columns_hit": hits, "score": score,
            })
    return out


def sweep_arcgis_hub(locality: str, now: dt.datetime, fetch=_get_json,
                     endpoint: str = HUB_DATASETS, method="arcgis-hub"):
    """ArcGIS Hub central datasets API - recordCount + fields + layer URL in
    one response: a candidate our existing arcgis adapter can pull as-is."""
    out = []
    for term in QUERY_TERMS:
        js = fetch(endpoint, {"q": f"{locality} {term}", "page[size]": 20})
        if js is None:
            out.append({"method": method, "error":
                        f"hub query failed: {_LAST_ERR}"})
            continue
        for item in js.get("data") or []:
            a = item.get("attributes") or {}
            title = a.get("name") or ""
            if not titled_like_sales(title):
                continue
            if not locality_match(locality, title, a.get("orgTitle") or "",
                                  a.get("source") or "",
                                  a.get("searchDescription") or ""):
                continue
            fields = [f.get("name", "") for f in (a.get("fields") or [])]
            col_score, hits = score_columns(fields)
            url = a.get("url") or (a.get("layer") or {}).get("url") or ""
            score = (col_score + size_bonus(a.get("recordCount")) +
                     freshness_bonus(a.get("modified"), now))
            out.append({
                "method": method, "adapter": "arcgis",
                "title": title, "org": a.get("orgTitle") or a.get("source"),
                "url": url, "record_count": a.get("recordCount"),
                "updated": a.get("modified"),
                "columns_hit": hits, "score": score,
            })
    return out


def sweep_ckan_va(locality: str, now: dt.datetime, fetch=_get_json):
    """data.virginia.gov CKAN - state-federated locality files served as
    plain CSV from the state domain (no city bot filters). CKAN has no
    column metadata, so these score on title/format/recency only and carry
    a 'needs field sniff on first pull' note."""
    js = fetch(CKAN_VA, {"q": f"{locality} property sales", "rows": 20})
    if js is None:
        return [{"method": "ckan-va", "error":
                 f"package_search failed: {_LAST_ERR}"}]
    out = []
    for pkg in ((js.get("result") or {}).get("results") or []):
        title = pkg.get("title") or ""
        org = ((pkg.get("organization") or {}).get("title")) or ""
        if not titled_like_sales(title):
            continue
        if not locality_match(locality, title, org,
                              pkg.get("notes") or ""):
            continue
        for res in pkg.get("resources") or []:
            fmt = (res.get("format") or "").upper()
            if fmt not in ("CSV", "XLSX", "XLS", "JSON"):
                continue
            score = 30 + freshness_bonus(
                res.get("last_modified") or pkg.get("metadata_modified"), now)
            out.append({
                "method": "ckan-va", "adapter": "csv_download",
                "title": f"{title} [{fmt}]", "org": org,
                "url": res.get("url"),
                "updated": (res.get("last_modified")
                            or pkg.get("metadata_modified")),
                "columns_hit": {}, "score": score,
                "note": "state mirror - fields sniff on first sized pull",
            })
    return out


def sweep_hrgeo(locality: str, now: dt.datetime, fetch=_get_json):
    """HRGEO regional hub (HRPDC) - same v3 datasets API shape as central
    hub, scoped to Hampton Roads publishers."""
    return sweep_arcgis_hub(locality, now, fetch=fetch,
                            endpoint=HRGEO_V3, method="hrgeo")


VA_ONLY_SWEEPS = ("ckan-va", "hrgeo")


def research_locality(locality: str, state: str, now: dt.datetime,
                      fetch=_get_json) -> list[dict]:
    cands = []
    cands += sweep_socrata(locality, now, fetch)
    cands += sweep_arcgis_hub(locality, now, fetch)
    if state == "VA":
        cands += sweep_ckan_va(locality, now, fetch)
        cands += sweep_hrgeo(locality, now, fetch)
    # Dedupe (same dataset surfaces under several query terms), errors last.
    seen: set = set()
    uniq: list[dict] = []
    errors: list[dict] = []
    for c in cands:
        if "error" in c:
            errors.append(c)
            continue
        key = (c.get("method"), c.get("resource_id") or c.get("url")
               or c.get("title"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    uniq.sort(key=lambda c: c.get("score", 0), reverse=True)
    # One line per distinct error source is plenty.
    err_seen: set = set()
    for e in errors:
        k = (e.get("method"), e.get("error"))
        if k not in err_seen:
            err_seen.add(k)
            uniq.append(e)
    return uniq


# ---------------------------------------------------------------- report

def main(argv=None) -> int:
    now = dt.datetime.now()
    print(f"autopilot salesdiscovery @ {now.isoformat(timespec='seconds')}\n")
    if _fresh(now) and not os.environ.get("ER_SALES_DISCOVERY_FORCE"):
        print(f"[sales-discovery] ran within {EVERY_DAYS}d - skipping "
              "(ER_SALES_DISCOVERY_FORCE=1 to override)")
        return 0

    locs = tracked_localities()
    only = {m.strip() for m in
            os.environ.get("ER_SALES_DISCOVERY_MARKETS", "").split(",")
            if m.strip()}
    if only:
        locs = [(m, s) for m, s in locs if m in only]
    print(f"[sales-discovery] researching {len(locs)} localities across "
          "central catalogs (Socrata Discovery / ArcGIS Hub / CKAN-VA / "
          "HRGEO) - metadata only, no gated-domain reads\n")

    results: dict[str, list[dict]] = {}
    for market, state in sorted(locs):
        cands = research_locality(market, state, now)
        results[market] = cands
        strong = [c for c in cands if c.get("score", 0) >= 60]
        print(f"[{market}] {len([c for c in cands if 'error' not in c])} "
              f"candidate(s), {len(strong)} strong (score>=60)")
        for c in cands[:5]:
            if "error" in c:
                print(f"    !! {c['method']}: {c['error']}")
                continue
            ev = ", ".join(f"{k}={v}" for k, v in
                           (c.get("columns_hit") or {}).items()) or "no cols"
            n = c.get("record_count")
            size = f" rows={n:,}" if isinstance(n, int) else ""
            print(f"    {c.get('score', 0):>3}  [{c['method']}] "
                  f"{c.get('title', '')[:70]}")
            print(f"         {ev}{size}  ->  "
                  f"{c.get('url') or c.get('base', '') or ''}"
                  f"{c.get('resource_id') or ''}")
        print()

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(
        {"generated_at": now.isoformat(timespec="seconds"),
         "localities": results}, indent=2))
    print(f"[sales-discovery] wrote {OUT_JSON.name} - promotion into "
          "SALES_SOURCES stays a reviewed edit (verified sources only)")
    try:
        _STAMP.parent.mkdir(exist_ok=True)
        _STAMP.write_text(now.isoformat())
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
