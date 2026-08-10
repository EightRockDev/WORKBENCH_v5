"""Autopilot step: pull sale/deed history for EVERY tracked locality from the
Spatialest API (owner directive 2026-08-09: sales for all top-50 metros).

Generalizes the Virginia Beach discovery (found via F12 -> Network -> XHR:
the portal is a Spatialest front end, api.spatialest.com/v1/<state>/<locality>/
<resource>/<GPIN>). For each locality we already track in muni_records this
step:
  1. AUTO-DISCOVERS the Spatialest (locality-slug, sales-resource) by probing a
     sample parcel and keeping the combo whose payload actually yields a sale
     record via core.sale_history.extract_sale_records - RUNTIME-VERIFIED, never
     assumed (the Apollo-adapter lesson: no blind pulls).
  2. Caches the discovered endpoint per locality (data/spatialest_endpoints.json)
     so later runs skip discovery.
  3. Pulls sales for that locality's tracked GPINs into muni_records kind='sales'
     (core.sale_index indexes them; the card + radar tenure fill automatically).

Localities NOT on Spatialest simply don't discover and are listed in the report
as "no Spatialest endpoint" - their vendor needs the same F12 discovery, tracked
as backlog. Host-only (build env is firewalled from these hosts). Rate-limited,
resumable, self-diagnosing per the 50-metro playbook.

Env: ER_SALES_LIMIT (parcels/locality/run, default 400)
     ER_SALES_REFRESH_D (re-fetch older than N days, default 30)
     ER_SALES_MARKETS (comma list to restrict; default = all tracked)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

from core import phase0, sale_history

SPATIALEST = "https://api.spatialest.com/v1"
CACHE = ROOT / "data" / "spatialest_endpoints.json"
LIMIT = int(os.environ.get("ER_SALES_LIMIT", "400"))
REFRESH_D = int(os.environ.get("ER_SALES_REFRESH_D", "30"))
CANDIDATE_RESOURCES = ("sales", "sales-history", "saleshistory", "sale-history",
                       "deeds", "transfers", "sales-information",
                       "transfer-history", "recordcard")
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) "
                   "Gecko/20100101 Firefox/153.0"),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://propertysearch.virginiabeach.gov",
    "Referer": "https://propertysearch.virginiabeach.gov/",
    "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site",
}


def slug_variants(market: str) -> list[str]:
    """Spatialest locality slugs to try for a market name."""
    base = market.strip().lower()
    return list(dict.fromkeys([
        base.replace(" ", ""),          # "virginia beach" -> virginiabeach
        base.replace(" ", "-"),         # -> virginia-beach
        base.replace(" ", "_"),
        "".join(ch for ch in base if ch.isalnum()),
    ]))


def _get(url: str) -> tuple[int, object]:
    for verify in (True, False):
        try:
            r = requests.get(url, headers=UA, timeout=25, verify=verify)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None
        except requests.exceptions.SSLError:
            try:
                import urllib3
                urllib3.disable_warnings()
            except Exception:
                pass
            continue
        except Exception:
            return 0, None
    return 0, None


def _has_sale(payload: object) -> bool:
    """True when a payload yields a real dated/priced sale (runtime proof the
    resource is the sales endpoint)."""
    recs = []
    if isinstance(payload, dict):
        recs = sale_history.extract_sale_records(payload)
        if not recs:                    # sales sometimes nested under a key
            for v in payload.values():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            recs += sale_history.extract_sale_records(item)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                recs += sale_history.extract_sale_records(item)
    return any(r.get("price") or r.get("date") for r in recs)


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=2))
    except OSError:
        pass


def tracked_localities(conn) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT market, state FROM muni_records "
        "WHERE kind LIKE 'assessor%' AND market IS NOT NULL").fetchall()
    return [(m, s or "VA") for m, s in rows]


def sample_gpins(conn, market: str, state: str, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT record FROM muni_records WHERE market=? AND kind LIKE 'assessor%'",
        (market,)).fetchall()
    out = []
    for (rec,) in rows:
        try:
            raw = json.loads(rec) if rec else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        # str() FIRST: some rolls store APN/GPIN as a bare integer, and
        # `int or ""` stays an int -> `.strip()` raises AttributeError and
        # crashes the whole sales pull mid-locality (autopilot 2026-08-10).
        apn = str(phase0.normalize_record(market, state, raw).get("apn") or "").strip()
        if apn:
            out.append(apn)
            if len(out) >= n:
                break
    return out


def discover(market: str, state: str, probe_gpins: list[str]) -> dict | None:
    """Find (slug, resource) whose payload yields a real sale, using up to a
    few sample parcels (some parcels have no sale on record)."""
    st = state.strip().lower()
    for slug in slug_variants(market):
        # cheap reachability check: does this slug exist at all?
        base = f"{SPATIALEST}/{st}/{slug}"
        reachable = False
        for res in CANDIDATE_RESOURCES:
            for gpin in probe_gpins:
                code, payload = _get(f"{base}/{res}/{gpin}")
                if code == 200:
                    reachable = True
                    if _has_sale(payload):
                        return {"base": base, "resource": res}
                time.sleep(0.3)
        # All resources tried. A slug that answered at all is the right
        # locality; report it with no sales resource (sampled parcels had no
        # sale, or the vendor has no sales feed) rather than breaking early.
        if reachable:
            return {"base": base, "resource": None}
    return None


def _fresh(conn, source_url, gpin, cutoff) -> bool:
    row = conn.execute(
        "SELECT pulled_at FROM muni_records WHERE kind='sales' AND source_url=? "
        "AND record LIKE ? LIMIT 1",
        (source_url, f'%"_gpin": "{gpin}"%')).fetchone()
    return bool(row and row[0] and row[0] >= cutoff)


def pull_locality(conn, market, state, endpoint, cutoff, now_iso) -> dict:
    src = f"{endpoint['base']}/{endpoint['resource']}"
    gpins = sample_gpins(conn, market, state, 10_000)
    todo = [g for g in gpins if not _fresh(conn, src, g, cutoff)][:LIMIT]
    wrote = miss = err = 0
    for g in todo:
        code, payload = _get(f"{src}/{g}")
        if code == 404:
            miss += 1
        elif code == 200 and payload is not None:
            rec = payload if isinstance(payload, dict) else {"sales": payload}
            rec["_gpin"] = g
            with conn:
                conn.execute("DELETE FROM muni_records WHERE kind='sales' AND "
                             "source_url=? AND record LIKE ?",
                             (src, f'%"_gpin": "{g}"%'))
                conn.execute(
                    "INSERT INTO muni_records (market,state,county,kind,"
                    "source_url,pulled_at,record) VALUES (?,?,?,?,?,?,?)",
                    (market, state, market, "sales", src, now_iso,
                     json.dumps(rec)))
            wrote += 1
        else:
            err += 1
        time.sleep(0.35)
    return {"tracked": len(gpins), "todo": len(todo),
            "wrote": wrote, "miss": miss, "err": err}


def main() -> int:
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("[sales] no workbench.db - skipping")
        return 0
    only = {m.strip() for m in os.environ.get("ER_SALES_MARKETS", "").split(",")
            if m.strip()}
    cache = _load_cache()
    cutoff = (dt.datetime.now() - dt.timedelta(days=REFRESH_D)).isoformat()
    now_iso = dt.datetime.now().isoformat(timespec="seconds")

    conn = sqlite3.connect(db)
    try:
        localities = tracked_localities(conn)
        if only:
            localities = [(m, s) for m, s in localities if m in only]
        print(f"[sales] {len(localities)} localities tracked")
        for market, state in localities:
            ep = cache.get(market)
            if ep is None:
                probes = sample_gpins(conn, market, state, 5)
                if not probes:
                    print(f"[sales] {market}: no parcels to probe - skip")
                    continue
                ep = discover(market, state, probes)
                cache[market] = ep or {"base": None, "resource": None}
                _save_cache(cache)
            if not ep or not ep.get("resource"):
                print(f"[sales] {market}: no Spatialest sales endpoint "
                      f"(needs vendor discovery) - skip")
                continue
            stats = pull_locality(conn, market, state, ep, cutoff, now_iso)
            print(f"[sales] {market}: wrote {stats['wrote']}, "
                  f"no-record {stats['miss']}, err {stats['err']} "
                  f"(of {stats['todo']} due, {stats['tracked']} tracked) "
                  f"via {ep['resource']}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
