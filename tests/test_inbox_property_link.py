"""Inbox -> property-data ingestion (owner 2026-08-11: "just ingest data",
not per-email display): backbone matching, muni_records kind='assessor-email'
rows the spine can merge, idempotent per message, never breaking ingest."""

from __future__ import annotations

import json
import sqlite3

from core.inbox import extract as ex
from core.inbox import property_link as pl


def _mk_backbone(tmp_path):
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE properties_8r (property_id TEXT, apn TEXT,"
                 " address TEXT, city TEXT, state TEXT, units INTEGER)")
    conn.executemany(
        "INSERT INTO properties_8r VALUES (?,?,?,?,?,?)",
        [("8R-NOR-001", "APN-1", "1200 Ballentine Blvd", "Norfolk", "VA", None),
         ("8R-NOR-002", "APN-2", "77 Granby Street", "Norfolk", "VA", 44),
         ("8R-RIC-001", "APN-3", "1200 Ballentine Blvd", "Richmond", "VA", 8),
         ("8R-NOR-003", None, "9 Parcelless Way", "Norfolk", "VA", None)])
    conn.execute("CREATE TABLE muni_records (market TEXT, state TEXT, "
                 "county TEXT, kind TEXT, source_url TEXT, pulled_at TEXT, "
                 "record TEXT)")
    conn.commit()
    conn.close()
    return db


def _use_backbone(monkeypatch, db):
    from data import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    return db


_OM = dict(
    subject="New to Market: Crossroads Townhomes - 26 Units, Norfolk VA",
    body="OM for Crossroads Townhomes, 1200 Ballentine Blvd, Norfolk, VA "
         "23504. 26 units, asking $2,850,000 at a 7.25% cap.")


def test_match_normalizes_abbreviations(tmp_path, monkeypatch):
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    hit = pl.match_property({"address": "1200 Ballentine Boulevard",
                             "city": "Norfolk"})
    assert hit and hit["property_id"] == "8R-NOR-001"


def test_match_requires_city_agreement_when_extracted(tmp_path, monkeypatch):
    # Same street exists in two cities - the city must disambiguate.
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    hit = pl.match_property({"address": "1200 Ballentine Blvd",
                             "city": "Richmond"})
    assert hit and hit["property_id"] == "8R-RIC-001"


def test_no_address_is_no_match():
    assert pl.match_property({"units": 26}) is None


def test_link_message_ingests_a_spine_mergeable_row(tmp_path, monkeypatch):
    db = _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    e = ex.extract_deal(**_OM)
    pid = pl.link_message("org-1", "msg-1", {"from_email": "b@x.com"}, e)
    assert pid == "8R-NOR-001"
    conn = sqlite3.connect(db)
    kind, tag, record = conn.execute(
        "SELECT kind, source_url, record FROM muni_records").fetchone()
    assert kind == "assessor-email"          # kind LIKE 'assessor%' -> spine
    assert tag == "inbox:msg-1"
    rec = json.loads(record)
    assert rec["apn"] == "APN-1"             # parcel identity from the match
    assert rec["units"] == 26
    assert rec["_source"] == "inbox-module-d"


def test_link_message_is_idempotent_per_message(tmp_path, monkeypatch):
    db = _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    e = ex.extract_deal(**_OM)
    pl.link_message("org-1", "msg-1", {}, e)
    pl.link_message("org-1", "msg-1", {}, e)     # re-sync of the same mail
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM muni_records").fetchone()[0] == 1


def test_no_backbone_match_means_no_ingest(tmp_path, monkeypatch):
    db = _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    e = ex.extract_deal(
        subject="Off market - 900 Colonial Ave, Portsmouth VA - 12 units",
        body="900 Colonial Ave, Portsmouth, VA. 12 units, asking $1,100,000.")
    assert pl.link_message("org-1", "msg-2", {}, e) is None
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM muni_records").fetchone()[0] == 0


def test_parcelless_match_is_not_ingested(tmp_path, monkeypatch):
    # A backbone row without an apn cannot join the spine build - writing a
    # muni row against it would strand the data (and guessing poisons).
    db = _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    e = ex.extract_deal(
        subject="9 Parcelless Way, Norfolk - 30 units",
        body="9 Parcelless Way, Norfolk, VA. 30 units, asking $3,000,000.")
    assert pl.link_message("org-1", "msg-3", {}, e) is None
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM muni_records").fetchone()[0] == 0


def test_link_message_skips_empty_extractions(tmp_path, monkeypatch):
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    e = ex.extract_deal(subject="lunch?", body="see you at noon")
    assert pl.link_message("org-1", "m", {}, e) is None


def test_ingest_failure_never_raises(tmp_path, monkeypatch):
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(pl, "_ingest_row", boom)
    e = ex.extract_deal(**_OM)
    assert pl.link_message("org-1", "m", {}, e) is None


def test_engine_apply_links_property(monkeypatch):
    from core.inbox import engine
    calls = {}
    monkeypatch.setattr(pl, "link_message",
                        lambda org, mid, msg, e, status="applied":
                        calls.update(org=org, mid=mid) or "K")
    engine._link_property("org-1", "mid-1", {"from_email": "b@x.com"},
                          ex.Extraction(fields={"address": "x"}))
    assert calls == {"org": "org-1", "mid": "mid-1"}
