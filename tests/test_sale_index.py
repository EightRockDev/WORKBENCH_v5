"""Offline sale-history index (core/sale_index.py) — the fix for render-time
muni scans (owner "too slow" report 2026-08-09)."""

from __future__ import annotations

import json
import sqlite3

import core.sale_history as sh
from core import sale_index


def _muni_db(tmp_path, rows):
    db = tmp_path / "workbench.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE muni_records (
        id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
        kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
    conn.executemany(
        "INSERT INTO muni_records (market,state,county,kind,source_url,"
        "pulled_at,record) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _stub_normalize(monkeypatch):
    def fake(city, state, raw, report=None):
        return {"apn": raw.get("APN"), "address": raw.get("ADDR")}
    monkeypatch.setattr("core.phase0.normalize_record", fake)


def test_build_extracts_and_gates_on_muni_stamp(tmp_path, monkeypatch):
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "77-1", "ADDR": "2110 Richmond Street",
                      "TOTSALPRICE": 795000.0, "SALE_DATE": 1734307200000})
    noise = json.dumps({"APN": "77-2", "ADDR": "9 Elm St"})   # no sale fields
    db = _muni_db(tmp_path, [
        ("Virginia Beach", "VA", "VB", "assessor+sales", "u", "t", rec),
        ("Virginia Beach", "VA", "VB", "assessor", "u2", "t", noise)])

    stats = sale_index.build(db)
    assert stats == {"skipped": False, "stamp": stats["stamp"],
                     "scanned": 2, "sales": 1}
    # Second build with unchanged muni data is a no-op.
    assert sale_index.build(db)["skipped"] is True
    # force=True rebuilds anyway.
    assert sale_index.build(db, force=True)["skipped"] is False


def test_lookup_by_apn_and_normalized_address(tmp_path, monkeypatch):
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "77-1", "ADDR": "2110 Richmond Street",
                      "saleprice": 750000, "saledate": "2022-04-01"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "N", "assessor", "u", "t", rec)])
    sale_index.build(db)

    hit = sale_index.lookup(db, apn_norm=sh._norm_apn("77-1"), addr_norm="")
    assert hit and hit[0]["price"] == 750000.0
    # Address matches across abbreviation (Street vs St) via shared _norm_addr.
    hit = sale_index.lookup(db, apn_norm="",
                            addr_norm=sh._norm_addr("2110 Richmond St"))
    assert hit and hit[0]["date"] == "2022-04-01"
    # Present index + unknown parcel = definitive empty, not None.
    assert sale_index.lookup(db, apn_norm="nope", addr_norm="nope") == []


def test_missing_index_returns_none_for_fallback(tmp_path):
    db = _muni_db(tmp_path, [])
    assert sale_index.lookup(db, apn_norm="x", addr_norm="y") is None


def test_sale_history_uses_index_and_skips_the_scan(tmp_path, monkeypatch):
    """The whole point: once the index exists, the render path must not need
    muni_records at all — proven by dropping the table after the build and
    still getting the answer."""
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "77-1", "ADDR": "1 Main St",
                      "saleprice": 500000, "saledate": "2019-01-01"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "N", "assessor", "u", "t", rec)])
    sale_index.build(db)

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE muni_records")     # scan path now impossible
    conn.commit()
    conn.close()

    prop = {"apn": "77-1", "address": "somewhere", "city": "Norfolk",
            "state": "VA", "market": "Norfolk"}
    out = sh.sale_history_for(prop, db_path=db)
    assert len(out) == 1 and out[0]["price"] == 500000.0
