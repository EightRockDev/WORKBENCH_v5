"""Sale history surfaced from the assessor transfer record (owner ask
2026-08-05: "do sale history and deed feed").

Two layers: extract_sale_records (tolerant of the many county field spellings)
and sale_history_for (matches a property to its muni_records assessor row and
pulls the sale). Read-only; the matcher is isolated from phase0's alias table
by stubbing normalize_record.
"""

from __future__ import annotations

import json
import sqlite3

import core.sale_history as sh


# ------------------------------------------------------- extraction

def test_chesapeake_style_last_sale_fields():
    raw = {"last_sale_price": 18_995_000, "last_sale_date": 1_776_000_000_000,
           "last_sale_buyer": "SPADA III LLC", "owner": "SPADA III LLC"}
    recs = sh.extract_sale_records(raw)
    assert len(recs) == 1
    r = recs[0]
    assert r["price"] == 18_995_000.0
    assert r["grantee"] == "SPADA III LLC"
    assert r["date"] and r["date"].startswith("20")     # epoch-ms -> ISO date


def test_generic_saleprice_saledate_grantor_grantee():
    raw = {"SalePrice": "1,250,000", "SaleDate": "03/15/2019",
           "Grantor": "Old Owner LLC", "Grantee": "New Owner LP"}
    r = sh.extract_sale_records(raw)[0]
    assert r["price"] == 1_250_000.0
    assert r["date"] == "2019-03-15"
    assert r["grantor"] == "Old Owner LLC" and r["grantee"] == "New Owner LP"


def test_deed_book_and_page_go_to_notes():
    raw = {"saleprice": 500000, "saledate": "2020-01-02",
           "deedbk": "4412", "deedpg": "88"}
    assert "4412/88" in sh.extract_sale_records(raw)[0]["notes"]


def test_a_record_with_no_price_or_date_is_not_a_sale():
    # A bare current-owner name is not a dated transaction.
    assert sh.extract_sale_records({"grantee": "Current Owner LLC"}) == []
    assert sh.extract_sale_records({}) == []


def test_zero_price_is_dropped_not_reported_as_free():
    raw = {"saleprice": 0, "saledate": "2021-06-01"}
    r = sh.extract_sale_records(raw)[0]
    assert r["price"] is None and r["date"] == "2021-06-01"


# ------------------------------------------------------- matching

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
    # Isolate the matcher from phase0's alias table: apn <- APN, address <- ADDR.
    def fake(city, state, raw, report=None):
        return {"apn": raw.get("APN"), "address": raw.get("ADDR")}
    monkeypatch.setattr("core.phase0.normalize_record", fake)


def test_matches_on_apn_and_returns_the_sale(tmp_path, monkeypatch):
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "1234-56", "ADDR": "2110 Richmond St",
                      "saleprice": 18_995_000, "saledate": "2026-07-10",
                      "grantee": "SPADA III LLC"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "Norfolk", "assessor",
                              "u", "t", rec)])
    prop = {"apn": "1234-56", "address": "somewhere else", "city": "Norfolk",
            "state": "VA", "market": "Norfolk"}
    out = sh.sale_history_for(prop, db_path=db)
    assert len(out) == 1 and out[0]["grantee"] == "SPADA III LLC"


def test_matches_on_address_when_apn_absent(tmp_path, monkeypatch):
    _stub_normalize(monkeypatch)
    rec = json.dumps({"ADDR": "100 Main St", "saleprice": 900000,
                      "saledate": "2018-05-05"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "Norfolk", "assessor",
                              "u", "t", rec)])
    prop = {"address": "100  MAIN st", "city": "Norfolk", "state": "VA",
            "market": "Norfolk"}                 # differing case/spacing
    out = sh.sale_history_for(prop, db_path=db)
    assert len(out) == 1 and out[0]["price"] == 900000.0


def test_no_match_returns_empty(tmp_path, monkeypatch):
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "9999", "ADDR": "999 Elsewhere Ave",
                      "saleprice": 1, "saledate": "2000-01-01"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "Norfolk", "assessor",
                              "u", "t", rec)])
    prop = {"apn": "1234-56", "address": "2110 Richmond St", "city": "Norfolk",
            "state": "VA", "market": "Norfolk"}
    assert sh.sale_history_for(prop, db_path=db) == []


def test_non_assessor_rows_are_ignored(tmp_path, monkeypatch):
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "1234-56", "saleprice": 5, "saledate": "2001-01-01"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "Norfolk", "permits",
                              "u", "t", rec)])   # kind != assessor
    prop = {"apn": "1234-56", "city": "Norfolk", "state": "VA", "market": "Norfolk"}
    assert sh.sale_history_for(prop, db_path=db) == []


def test_missing_db_is_safe(tmp_path):
    prop = {"apn": "1", "city": "Norfolk", "state": "VA", "market": "Norfolk"}
    assert sh.sale_history_for(prop, db_path=tmp_path / "nope.db") == []
