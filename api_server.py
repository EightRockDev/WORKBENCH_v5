"""Eight Rock Data API — metered, read-only access to the reference layer.

Owner ask 2026-08-07 ("an API that allows users to connect and pull data, for
a fee"); spec §6.5 Module G names the machinery (FastAPI service layer, usage
meters). v1 serves the SHARED REFERENCE LAYER only (spec §10.1): the property
backbone and municipal sale history — global, org-blind data. No org-private
deal data is reachable here, so a key leak can never expose an underwrite.

Auth: ``Authorization: Bearer 8rk_...`` (per-org keys, hashed at rest —
core/data_api_keys). Every request writes one usage row; a per-key daily cap
(ER_API_DAILY_CAP, default 10k) returns 429 until billing tiers exist.

Run on the host:  uv run uvicorn api_server:app --port 8600
(or run-api.bat). Public exposure = a Caddy route later; NOT wired by default.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from core import data_api_keys as keys
from core import sale_history
from data import pg

app = FastAPI(title="Eight Rock Data API", version="1.0",
              docs_url="/v1/docs", openapi_url="/v1/openapi.json")

_BACKBONE_COLS = ("property_id, fips, apn, address, city, state, zip, units, "
                  "year_built, sqft, use_code, r8_form, r8_market, "
                  "r8_submarket, assessed_value, owner_name, lat, lng, "
                  "est_avg_rent, rent_source, provenance")


def _workbench_db() -> Path:
    from core import phase0
    path = phase0.find_workbench_db()
    if path is None or not Path(path).exists():
        raise HTTPException(503, "reference data store unavailable")
    return Path(path)


def _auth(request: Request) -> keys.ApiKey:
    """Bearer-key auth + metering, one call per endpoint hit."""
    if not pg.is_reachable():
        raise HTTPException(503, "key service unavailable")
    header = request.headers.get("authorization", "")
    secret = header.removeprefix("Bearer ").strip()
    key = keys.verify_key(secret)
    if key is None:
        raise HTTPException(401, "missing, unknown, or revoked API key")
    if not keys.meter(key, request.url.path):
        raise HTTPException(429, f"daily request cap reached "
                                 f"({keys.DAILY_CAP}/day per key)")
    return key


@app.get("/v1/health")
def health() -> dict:
    # Unauthenticated liveness probe: no data, no metering.
    return {"ok": True, "service": "eight-rock-data-api", "version": "1.0"}


@app.get("/v1/properties")
def list_properties(
    key: keys.ApiKey = Depends(_auth),
    city: str | None = Query(None, description="exact city, case-insensitive"),
    zip: str | None = Query(None),
    min_units: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Backbone properties (multifamily reference records)."""
    where, args = ["1=1"], []
    if city:
        where.append("city = ? COLLATE NOCASE")
        args.append(city)
    if zip:
        where.append("zip = ?")
        args.append(zip)
    if min_units:
        where.append("units >= ?")
        args.append(min_units)
    sql = (f"SELECT {_BACKBONE_COLS} FROM properties_8r "
           f"WHERE {' AND '.join(where)} ORDER BY property_id LIMIT ? OFFSET ?")
    conn = sqlite3.connect(f"file:{_workbench_db()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, (*args, limit, offset))]
    finally:
        conn.close()
    return {"count": len(rows), "offset": offset, "results": rows}


@app.get("/v1/properties/{property_id}")
def get_property(property_id: str, key: keys.ApiKey = Depends(_auth)) -> dict:
    conn = sqlite3.connect(f"file:{_workbench_db()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_BACKBONE_COLS} FROM properties_8r WHERE property_id = ?",
            (property_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "unknown property_id")
    return dict(row)


@app.get("/v1/properties/{property_id}/sales")
def get_sales(property_id: str, key: keys.ApiKey = Depends(_auth)) -> dict:
    """Most-recent recorded sale(s) from the municipal assessor record."""
    db = _workbench_db()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT property_id, apn, address, city, state, r8_market "
            "FROM properties_8r WHERE property_id = ?",
            (property_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "unknown property_id")
    prop = {"apn": row["apn"], "address": row["address"], "city": row["city"],
            "state": row["state"], "market": row["r8_market"]}
    records = sale_history.sale_history_for(prop, db_path=db)
    return {"property_id": property_id, "count": len(records),
            "results": records}
