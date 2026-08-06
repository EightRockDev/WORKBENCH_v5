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


def test_wake_style_totsalprice_uppercase_arcgis_keys():
    """Regression (2026-08-06): the live DB's 1.38M kind='assessor+sales' rows
    (Wake et al.) carry TOTSALPRICE/SALE_DATE/DEED_BOOK — TOTSALPRICE was
    missing from _PRICE_KEYS, so every one extracted price=None and the
    diagnose script concluded the feed 'has NO transfer/sale fields'."""
    raw = {"TOTSALPRICE": 795000.0, "SALE_DATE": 1734307200000,
           "DEED_BOOK": "019788", "DEED_PAGE": "00619", "DEED_ACRES": 4.81}
    r = sh.extract_sale_records(raw)[0]
    assert r["price"] == 795000.0
    assert r["date"] == "2024-12-16"          # epoch-ms -> ISO
    assert "019788/00619" in r["notes"]


def test_forsyth_and_nashville_registry_key_spellings():
    # Forsyth: LASTQUALIFIEDSALEPRICE/-DATE; Nashville: SalePrice + OwnDate.
    r = sh.extract_sale_records({"LASTQUALIFIEDSALEPRICE": 2_400_000,
                                 "LASTQUALIFIEDSALEDATE": "2023-11-01"})[0]
    assert r["price"] == 2_400_000.0 and r["date"] == "2023-11-01"
    r = sh.extract_sale_records({"SalePrice": 650000, "OwnDate": 1600000000000})[0]
    assert r["price"] == 650000.0 and r["date"] == "2020-09-13"


def test_sale_date_zero_sentinel_is_not_a_phantom_date():
    # Assessors stamp 0 for "never sold"; it must not surface as date "0".
    assert sh.extract_sale_records({"SALE_DATE": 0}) == []
    r = sh.extract_sale_records({"TOTSALPRICE": 100000, "SALE_DATE": 0})[0]
    assert r["date"] is None and r["price"] == 100000.0


def test_yyyymmdd_integer_reads_as_calendar_not_epoch_seconds():
    # 20190315 sits in the epoch-seconds band (would decode as Aug 1970).
    r = sh.extract_sale_records({"saleprice": 1000, "saledate": 20190315})[0]
    assert r["date"] == "2019-03-15"
    r = sh.extract_sale_records({"saleprice": 1000, "saledate": "20190315"})[0]
    assert r["date"] == "2019-03-15"


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


def test_matches_across_street_abbreviation(tmp_path, monkeypatch):
    """'2110 Richmond Street' (assessor situs) must match '2110 Richmond St'
    (property record): _norm_addr now rides parity's abbreviation-collapsing
    normalize_address instead of raw string equality."""
    _stub_normalize(monkeypatch)
    rec = json.dumps({"ADDR": "2110 Richmond Street", "saleprice": 750000,
                      "saledate": "2022-04-01"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "Norfolk", "assessor",
                              "u", "t", rec)])
    prop = {"address": "2110 Richmond St", "city": "Norfolk", "state": "VA",
            "market": "Norfolk"}
    out = sh.sale_history_for(prop, db_path=db)
    assert len(out) == 1 and out[0]["price"] == 750000.0


def test_market_scope_is_case_insensitive(tmp_path, monkeypatch):
    # Feed filed under "NORFOLK" must still be scanned for city "Norfolk".
    _stub_normalize(monkeypatch)
    rec = json.dumps({"APN": "77-1", "saleprice": 320000,
                      "saledate": "2019-09-09"})
    db = _muni_db(tmp_path, [("NORFOLK", "VA", "Norfolk", "assessor+sales",
                              "u", "t", rec)])
    prop = {"apn": "77-1", "city": "Norfolk", "state": "VA",
            "market": "Hampton Roads"}     # the 8r shape: market never matches
    out = sh.sale_history_for(prop, db_path=db)
    assert len(out) == 1 and out[0]["price"] == 320000.0


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


def test_default_db_path_uses_the_real_phase0_locator(monkeypatch):
    """Regression (owner 2026-08-05, 'no sale history on ANY property'): the
    default-path branch called phase0.workbench_db(), which does not exist —
    every call raised AttributeError, swallowed by the broad except, so the
    card blanket-showed 'No sale history available'. The whole test suite passed
    because every other test injects db_path= and never touches this branch.
    Pin that _muni_db_path(None) resolves through find_workbench_db without
    raising."""
    from core import phase0
    assert not hasattr(phase0, "workbench_db"), (
        "sale_history must call find_workbench_db, not workbench_db")

    from pathlib import Path
    sentinel = Path("/tmp/does-not-exist-muni.db")
    monkeypatch.setattr(phase0, "find_workbench_db", lambda: sentinel)
    assert sh._muni_db_path(None) == sentinel

    # And None from the locator must be tolerated (no muni DB on this box).
    monkeypatch.setattr(phase0, "find_workbench_db", lambda: None)
    assert sh._muni_db_path(None) is None
    # end-to-end: a None locator degrades to [] rather than crashing.
    prop = {"apn": "1", "city": "Norfolk", "state": "VA", "market": "Norfolk"}
    assert sh.sale_history_for(prop) == []
