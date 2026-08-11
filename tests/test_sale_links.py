"""Clickable sale sources (owner ask 2026-08-11): every sale row carries its
reporting source's HUMAN page - machine endpoints map to dataset/portal
pages, and the index round-trips source_url."""

from __future__ import annotations

import json
import sqlite3

import core.sale_history as sh
from core import sale_index
from core.sale_links import sale_source_link


def test_link_mapping_per_source():
    # VB Esri layer -> city dataset page
    assert "gis.data.vbgov.com" in sale_source_link(
        "https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/"
        "Property_Sales_/FeatureServer/0")
    # Norfolk stack tag -> live FY dataset page
    assert sale_source_link(
        "socrata-stack:data.norfolk.gov/property-assessment-and-sales"
    ) == "https://data.norfolk.gov/d/qva7-tzrf"
    # Richmond stack tag -> transfer-history dataset page
    assert sale_source_link(
        "socrata-stack:data.richmondgov.com/property-transfers"
    ) == "https://data.richmondgov.com/d/uxre-by3i"
    # Chesapeake LandBook tag -> assessor open-data page
    assert "cityofchesapeake.net" in sale_source_link(
        "landbook:gis.cityofchesapeake.net FY26-27")
    # Generic Socrata resource -> /d/ human page
    assert sale_source_link(
        "https://data.norfolk.gov/resource/qva7-tzrf.json"
    ) == "https://data.norfolk.gov/d/qva7-tzrf"
    # Spatialest API -> community portal
    assert sale_source_link(
        "https://api.spatialest.com/v1/va/suffolk/sales/123"
    ) == "https://community.spatialest.com/va/suffolk/"
    # Browsable fallback + none
    assert sale_source_link("https://maps.nnva.gov/gis/rest/services/x/MapServer/0"
                            ).startswith("https://maps.nnva.gov")
    assert sale_source_link("") is None
    assert sale_source_link(None) is None
    assert sale_source_link("not-a-url") is None


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


def test_index_round_trips_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.phase0.normalize_record",
        lambda city, state, raw, report=None: {"apn": raw.get("APN"),
                                               "address": raw.get("ADDR")})
    src = "socrata-stack:data.richmondgov.com/property-transfers"
    rec = json.dumps({"APN": "W0001", "ADDR": "1 Main St",
                      "consideration": 750000, "transfer_date": "2024-03-01"})
    db = _muni_db(tmp_path,
                  [("Richmond", "VA", "Richmond", "sales", src, "t", rec)])
    sale_index.build(db)
    hit = sale_index.lookup(db, apn_norm=sh._norm_apn("W0001"), addr_norm="")
    assert hit and hit[0]["source_url"] == src
    assert sale_source_link(hit[0]["source_url"]) == \
        "https://data.richmondgov.com/d/uxre-by3i"


def test_migration_adds_column_and_forces_rebuild(tmp_path, monkeypatch):
    """A pre-existing index without source_url gains the column AND rebuilds
    immediately (the fresh-stamp gate must not keep link-less rows alive)."""
    monkeypatch.setattr(
        "core.phase0.normalize_record",
        lambda city, state, raw, report=None: {"apn": raw.get("APN"),
                                               "address": raw.get("ADDR")})
    rec = json.dumps({"APN": "77-1", "ADDR": "2 Oak St",
                      "saleprice": 500000, "saledate": "2023-01-01"})
    db = _muni_db(tmp_path, [("Norfolk", "VA", "N", "assessor", "u", "t", rec)])
    # Simulate the OLD schema (no source_url) already built + stamped fresh.
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sale_records (market TEXT, state TEXT, apn_norm TEXT,
            addr_norm TEXT, date TEXT, price REAL, grantor TEXT,
            grantee TEXT, notes TEXT);
        CREATE TABLE sale_index_meta (id INTEGER PRIMARY KEY,
            muni_stamp TEXT NOT NULL, built_at TEXT NOT NULL);
    """)
    stamp = conn.execute("SELECT count(*), COALESCE(max(id),0) FROM "
                         "muni_records WHERE kind LIKE 'assessor%' OR kind "
                         "LIKE 'sales%'").fetchone()
    conn.execute("INSERT INTO sale_index_meta VALUES (1, ?, 'then')",
                 (f"{stamp[0]}:{stamp[1]}",))
    conn.commit()
    conn.close()

    stats = sale_index.build(db)          # would be skipped without migration
    assert stats["skipped"] is False
    hit = sale_index.lookup(db, apn_norm=sh._norm_apn("77-1"), addr_norm="")
    assert hit and hit[0]["source_url"] == "u"
