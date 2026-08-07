"""Data API v1 — auth, reference-layer reads, metering (spec §6.5 Module G).

Endpoint logic is tested everywhere against a temp SQLite backbone with the
key layer stubbed; the Postgres key/meter round-trip runs when a database is
reachable (same auto-skip discipline as the other pg suites).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import api_server
from core import data_api_keys as dak
from data import pg


# ------------------------------------------------------- fixtures

def _backbone(tmp_path):
    db = tmp_path / "workbench.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE properties_8r (
        property_id TEXT PRIMARY KEY, fips TEXT, apn TEXT, address TEXT,
        city TEXT, state TEXT, zip TEXT, units INTEGER, year_built INTEGER,
        sqft REAL, use_code TEXT, r8_form TEXT, r8_market TEXT,
        r8_submarket TEXT, assessed_value REAL, owner_name TEXT, lat REAL,
        lng REAL, est_avg_rent REAL, rent_source TEXT, provenance TEXT)""")
    conn.executemany(
        "INSERT INTO properties_8r (property_id, apn, address, city, state, "
        "units, r8_market) VALUES (?,?,?,?,?,?,?)",
        [("8R-1", "77-1", "1 Main St", "Norfolk", "VA", 24, "Hampton Roads"),
         ("8R-2", "77-2", "2 Oak Ave", "Norfolk", "VA", 8, "Hampton Roads"),
         ("8R-3", "88-1", "9 Elm Rd", "Suffolk", "VA", 50, "Hampton Roads")])
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with the key layer stubbed valid and a temp backbone."""
    db = _backbone(tmp_path)
    from core import phase0
    monkeypatch.setattr(phase0, "find_workbench_db", lambda: db)
    monkeypatch.setattr(pg, "is_reachable", lambda: True)
    fake = dak.ApiKey(id="k1", org_id="o1", label="t", prefix_hint="8rk_test",
                      status="active")
    monkeypatch.setattr(dak, "verify_key",
                        lambda s: fake if s == "8rk_good" else None)
    metered: list[str] = []
    monkeypatch.setattr(dak, "meter",
                        lambda key, endpoint, units=1: metered.append(endpoint) or True)
    c = TestClient(api_server.app)
    c.metered = metered
    return c


def _get(client, path, key="8rk_good"):
    return client.get(path, headers={"Authorization": f"Bearer {key}"})


# ------------------------------------------------------- endpoints

def test_health_is_open_and_unmetered(client):
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.metered == []


def test_missing_or_bad_key_is_401(client):
    assert client.get("/v1/properties").status_code == 401
    assert _get(client, "/v1/properties", key="8rk_wrong").status_code == 401


def test_list_properties_filters_and_meters(client):
    r = _get(client, "/v1/properties?city=norfolk&min_units=10")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["results"][0]["property_id"] == "8R-1"
    assert client.metered == ["/v1/properties"]


def test_get_single_property_and_404(client):
    assert _get(client, "/v1/properties/8R-3").json()["units"] == 50
    assert _get(client, "/v1/properties/8R-999").status_code == 404


def test_sales_endpoint_shapes_reference_lookup(client, monkeypatch):
    from core import sale_history
    seen = {}

    def fake_history(prop, db_path=None):
        seen.update(prop)
        return [{"date": "2024-12-16", "price": 795000.0, "grantor": "",
                 "grantee": "", "notes": "", "source": "assessor"}]

    monkeypatch.setattr(sale_history, "sale_history_for", fake_history)
    r = _get(client, "/v1/properties/8R-1/sales")
    assert r.status_code == 200 and r.json()["count"] == 1
    assert seen["apn"] == "77-1" and seen["city"] == "Norfolk"


def test_over_cap_is_429(client, monkeypatch):
    monkeypatch.setattr(dak, "meter", lambda key, endpoint, units=1: False)
    assert _get(client, "/v1/properties").status_code == 429


def test_no_postgres_is_503_not_a_leak(client, monkeypatch):
    monkeypatch.setattr(pg, "is_reachable", lambda: False)
    assert _get(client, "/v1/properties").status_code == 503


# ------------------------------------------------------- Postgres round-trip

pytestmark_pg = pytest.mark.skipif(
    not pg.is_reachable(), reason="no reachable Postgres")


@pytest.fixture
def pg_org():
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO organizations (name, type) VALUES (%s, 'sponsor') "
                    "RETURNING id", (f"api-{uuid.uuid4().hex[:8]}",))
        org_id = str(cur.fetchone()["id"])
        conn.commit()
    try:
        yield org_id
    finally:
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
            conn.commit()


@pytestmark_pg
def test_key_lifecycle_and_metering(pg_org):
    rec, secret = dak.create_key(pg_org, "test-key", created_by=None)
    assert secret.startswith(dak.KEY_PREFIX)

    key = dak.verify_key(secret)
    assert key is not None and key.org_id == pg_org

    assert dak.meter(key, "/v1/properties") is True
    listed = dak.list_keys(pg_org)
    assert listed[0].requests_today == 1
    assert dak.usage_summary(pg_org)[0]["requests"] == 1

    assert dak.revoke_key(pg_org, key.id) is True
    assert dak.verify_key(secret) is None          # revoked keys stop working
    # Another org cannot revoke or see this org's keys.
    assert dak.revoke_key(str(uuid.uuid4()), key.id) is False
