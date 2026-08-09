"""Pull Virginia Beach sale/deed history from the Spatialest API into
muni_records as kind='sales' (owner ask 2026-08-08).

VB's assessor feed carries no sale data, but the public property portal
(propertysearch.virginiabeach.gov) is a Spatialest front end whose API
returns full deed chains per parcel:
  https://api.spatialest.com/v1/va/virginiabeach/<VB_SALES_RESOURCE>/<GPIN>

Design (host-only; build env is firewalled):
  * GPINs come from the VB rows already in muni_records - we only fetch sale
    history for parcels we actually track, not the whole city.
  * Rows land as kind='sales' so core.sale_index picks them up automatically
    (extract_sale_records already handles the field spellings) and the Sale
    History card + radar tenure fill with zero further wiring.
  * Rate-limited and resumable: a per-GPIN freshness stamp means a killed run
    resumes, and steady-state re-fetches only stale parcels.
  * The resource name is the ONE unknown until the probe confirms it - set
    ER_VB_SALES_RESOURCE (default 'sales'); the script no-ops loudly if the
    endpoint 404s so it can never silently write nothing and look done.

Env:
  ER_VB_SALES_RESOURCE   Spatialest resource segment (default 'sales')
  ER_VB_SALES_LIMIT      max parcels per run (default 500; politeness)
  ER_VB_SALES_REFRESH_D  re-fetch a parcel only if older than N days (default 30)
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

from core import phase0  # noqa: E402

BASE = "https://api.spatialest.com/v1/va/virginiabeach"
LIMIT = int(os.environ.get("ER_VB_SALES_LIMIT", "500"))
REFRESH_D = int(os.environ.get("ER_VB_SALES_REFRESH_D", "30"))
_PROBE_REPORT = ROOT / "reports" / "vb-sales-probe.txt"


def resolve_resource() -> str | None:
    """The confirmed Spatialest sales resource segment, or None (skip).

    Priority: explicit ER_VB_SALES_RESOURCE override > the resource parsed
    from a probe report that said FOUND. Until one exists we do NOT guess
    (the Apollo-adapter lesson: never pull blind against an unverified
    contract) - the step skips cleanly instead of 404-ing every cycle."""
    env = os.environ.get("ER_VB_SALES_RESOURCE", "").strip()
    if env:
        return env
    try:
        txt = _PROBE_REPORT.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    import re
    m = re.search(r"FOUND: https?://[^\s;]*?/v1/va/virginiabeach/"
                  r"([A-Za-z0-9_-]+)/", txt)
    return m.group(1) if m else None
UA = {"User-Agent": "EightRockWorkbench/1.0 (contact bmccune@gmail.com)",
      "Accept": "application/json"}


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


def _vb_gpins(conn: sqlite3.Connection) -> list[str]:
    """GPINs of VB parcels we already track (from any VB muni row)."""
    rows = conn.execute(
        "SELECT DISTINCT record FROM muni_records "
        "WHERE market = 'Virginia Beach' AND kind LIKE 'assessor%'").fetchall()
    gpins = set()
    for (rec,) in rows:
        try:
            raw = json.loads(rec) if rec else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        norm = phase0.normalize_record("Virginia Beach", "VA", raw)
        apn = (norm.get("apn") or "").strip()
        if apn:
            gpins.add(apn)
    return sorted(gpins)


def _already_fresh(conn, gpin, cutoff_iso) -> bool:
    row = conn.execute(
        "SELECT pulled_at FROM muni_records WHERE kind='sales' "
        "AND source_url=? AND record LIKE ? LIMIT 1",
        (SOURCE_URL, f'%"_gpin": "{gpin}"%')).fetchone()
    return bool(row and row[0] and row[0] >= cutoff_iso)


def main() -> int:
    resource = resolve_resource()
    if not resource:
        print("[vbsales] sales endpoint not confirmed yet - skipping "
              "(set ER_VB_SALES_RESOURCE, or wait for the probe to report "
              "FOUND in reports/vb-sales-probe.txt)")
        return 0
    global SOURCE_URL
    SOURCE_URL = f"{BASE}/{resource}"
    db = phase0.find_workbench_db()
    if db is None or not Path(db).exists():
        print("[vbsales] no workbench.db - skipping")
        return 0
    conn = sqlite3.connect(db)
    try:
        gpins = _vb_gpins(conn)
        print(f"[vbsales] {len(gpins)} VB parcels tracked; resource='{resource}'")
        if not gpins:
            return 0
        cutoff = (dt.datetime.now() - dt.timedelta(days=REFRESH_D)).isoformat()
        todo = [g for g in gpins if not _already_fresh(conn, g, cutoff)][:LIMIT]
        print(f"[vbsales] {len(todo)} parcels to fetch this run (limit {LIMIT})")

        wrote = miss = err = 0
        first_status = None
        now_iso = dt.datetime.now().isoformat(timespec="seconds")
        for i, gpin in enumerate(todo, 1):
            code, payload = _get(f"{SOURCE_URL}/{gpin}")
            if first_status is None:
                first_status = code
            if code == 404:
                miss += 1
                continue
            if code != 200 or payload is None:
                err += 1
                time.sleep(0.4)
                continue
            # Stamp the GPIN into the stored record so the app matcher and the
            # freshness check can find it regardless of the vendor's own keys.
            rec = payload if isinstance(payload, dict) else {"sales": payload}
            rec["_gpin"] = gpin
            with conn:
                conn.execute(
                    "DELETE FROM muni_records WHERE kind='sales' AND "
                    "source_url=? AND record LIKE ?",
                    (SOURCE_URL, f'%"_gpin": "{gpin}"%'))
                conn.execute(
                    "INSERT INTO muni_records (market,state,county,kind,"
                    "source_url,pulled_at,record) VALUES (?,?,?,?,?,?,?)",
                    ("Virginia Beach", "VA", "Virginia Beach", "sales",
                     SOURCE_URL, now_iso, json.dumps(rec)))
            wrote += 1
            time.sleep(0.4)                       # politeness
            if i % 50 == 0:
                print(f"[vbsales] ...{i}/{len(todo)} (wrote {wrote})")

        if wrote == 0 and (first_status == 404 or miss == len(todo)) and todo:
            print(f"[vbsales] ENDPOINT WRONG: '{resource}' 404s for every "
                  f"parcel - set ER_VB_SALES_RESOURCE to the confirmed name "
                  f"(see reports/vb-sales-probe.txt). Wrote nothing.")
            return 1
        print(f"[vbsales] done: wrote {wrote}, no-record {miss}, errors {err}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
