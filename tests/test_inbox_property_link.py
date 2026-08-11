"""Inbox -> property-details bridge (owner 2026-08-11: "reading emails from
O365 ... populating property details from them"): address matching against
the backbone, gate-respecting intel writes, and never breaking ingest."""

from __future__ import annotations

import sqlite3

from core.inbox import extract as ex
from core.inbox import property_link as pl


def _mk_backbone(tmp_path):
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE properties_8r (property_id TEXT, address TEXT,"
                 " city TEXT, units INTEGER)")
    conn.executemany(
        "INSERT INTO properties_8r VALUES (?,?,?,?)",
        [("8R-NOR-001", "1200 Ballentine Blvd", "Norfolk", 26),
         ("8R-NOR-002", "77 Granby Street", "Norfolk", 44),
         ("8R-RIC-001", "1200 Ballentine Blvd", "Richmond", 8)])
    conn.commit()
    conn.close()
    return db


def _use_backbone(monkeypatch, db):
    from data import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)


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


def test_link_message_writes_matched_intel(tmp_path, monkeypatch):
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    written = {}

    def fake_write(org_id, key, matched, message_id, msg, fields,
                   confidence, status):
        written.update(org=org_id, key=key, matched=matched,
                       message_id=message_id, fields=fields,
                       confidence=confidence, status=status)

    monkeypatch.setattr(pl, "_write_intel", fake_write)
    e = ex.extract_deal(
        subject="New to Market: Crossroads Townhomes - 26 Units, Norfolk VA",
        body="OM for Crossroads Townhomes, 1200 Ballentine Blvd, Norfolk, VA "
             "23504. 26 units, asking $2,850,000 at a 7.25% cap.")
    key = pl.link_message("org-1", "msg-1", {"from_email": "b@x.com"},
                          e, status="applied")
    assert key == "8R-NOR-001" and written["matched"] is True
    assert written["fields"].get("units") == 26
    assert written["status"] == "applied"


def test_link_message_keeps_unmatched_intel_by_address_key(monkeypatch,
                                                           tmp_path):
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))
    written = {}
    monkeypatch.setattr(pl, "_write_intel",
                        lambda *a, **k: written.setdefault("key", a[1]))
    e = ex.extract_deal(
        subject="Off market - 900 Colonial Ave, Portsmouth VA - 12 units",
        body="900 Colonial Ave, Portsmouth, VA. 12 units, asking $1,100,000.")
    key = pl.link_message("org-1", "msg-2", {}, e, status="applied")
    assert key and key == written["key"]
    assert "colonial" in key            # normalized-address key, retrievable
    assert pl.norm_key("900 Colonial Avenue", "Portsmouth") == key


def test_link_message_skips_empty_extractions(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(pl, "_write_intel",
                        lambda *a, **k: called.__setitem__("n", 1))
    e = ex.extract_deal(subject="lunch?", body="see you at noon")
    assert pl.link_message("org-1", "m", {}, e, status="applied") is None
    assert called["n"] == 0


def test_write_failure_never_raises_into_ingest(tmp_path, monkeypatch):
    _use_backbone(monkeypatch, _mk_backbone(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(pl, "_write_intel", boom)
    e = ex.extract_deal(subject="x", body="1200 Ballentine Blvd, Norfolk, VA "
                                          "- 26 units, $2,850,000")
    assert pl.link_message("org-1", "m", {}, e, status="applied") is None


def test_engine_apply_links_property(monkeypatch):
    from core.inbox import engine
    calls = {}
    monkeypatch.setattr(pl, "link_message",
                        lambda org, mid, msg, e, status: calls.update(
                            org=org, mid=mid, status=status) or "K")
    engine._link_property("org-1", "mid-1", {"from_email": "b@x.com"},
                          ex.Extraction(fields={"address": "x"}))
    assert calls == {"org": "org-1", "mid": "mid-1", "status": "applied"}
