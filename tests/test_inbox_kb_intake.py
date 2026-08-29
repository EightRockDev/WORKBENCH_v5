"""Git-delivered KB intake (data/inbox_kb_intake): tracked files are never
moved, idempotency rides a content-hash ledger, corrupt files are recorded
once and never grind, and an edited file re-ingests."""

from __future__ import annotations

import json
import sqlite3

from core.inbox import kb_drop


def _mk_backbone(tmp_path, monkeypatch):
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE properties_8r (property_id TEXT, apn TEXT,"
                 " address TEXT, city TEXT, state TEXT, units INTEGER)")
    conn.execute("INSERT INTO properties_8r VALUES "
                 "('8R-NOR-001','APN-1','1200 Ballentine Blvd','Norfolk',"
                 "'VA',NULL)")
    conn.execute("CREATE TABLE muni_records (market TEXT, state TEXT, "
                 "county TEXT, kind TEXT, source_url TEXT, pulled_at TEXT, "
                 "record TEXT)")
    conn.commit()
    conn.close()
    from data import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    return db


def _no_pg(monkeypatch):
    from data import pg
    monkeypatch.setattr(pg, "is_reachable", lambda: False)


def _intake(tmp_path):
    d = tmp_path / "intake"
    d.mkdir(exist_ok=True)
    return d, tmp_path / "seen.json"


REC = {"external_id": "gm-abc123", "from_email": "listings@broker.com",
       "subject": "JUST LISTED! 26 Units in Norfolk, VA",
       "received_at": "2026-08-11T14:00:00Z",
       "body": "Crossroads at 1200 Ballentine Blvd, Norfolk, VA.",
       "fields": {"address": "1200 Ballentine Blvd", "city": "Norfolk",
                  "state": "VA", "units": 26}}


def test_intake_ingests_and_never_moves_the_file(tmp_path, monkeypatch):
    db = _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    d, seen = _intake(tmp_path)
    (d / "gm-abc123.json").write_text(json.dumps(REC))
    res = kb_drop.ingest_git_intake(d, seen)
    assert (res.files, res.records, res.ingested, res.failed) == (1, 1, 1, 0)
    assert res.linked == 1
    assert (d / "gm-abc123.json").exists()          # tracked file untouched
    conn = sqlite3.connect(db)
    kind, tag = conn.execute(
        "SELECT kind, source_url FROM muni_records").fetchone()
    assert kind == "assessor-email" and tag == "inbox:gm-abc123"


def test_second_sweep_is_a_noop_via_hash_ledger(tmp_path, monkeypatch):
    _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    d, seen = _intake(tmp_path)
    (d / "a.json").write_text(json.dumps(REC))
    assert kb_drop.ingest_git_intake(d, seen).records == 1
    again = kb_drop.ingest_git_intake(d, seen)
    assert (again.files, again.records) == (0, 0)


def test_edited_file_reingests_on_new_hash(tmp_path, monkeypatch):
    _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    d, seen = _intake(tmp_path)
    (d / "a.json").write_text(json.dumps(REC))
    kb_drop.ingest_git_intake(d, seen)
    updated = dict(REC, subject="PRICE REDUCED! 26 Units in Norfolk, VA")
    (d / "a.json").write_text(json.dumps(updated))
    res = kb_drop.ingest_git_intake(d, seen)
    assert (res.files, res.records, res.ingested) == (1, 1, 1)


def test_corrupt_file_recorded_once_without_sinking_sweep(tmp_path,
                                                          monkeypatch):
    _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    d, seen = _intake(tmp_path)
    (d / "bad.json").write_text("{not json")
    (d / "good.json").write_text(json.dumps(REC))
    res = kb_drop.ingest_git_intake(d, seen)
    assert res.failed == 1 and res.ingested == 1
    assert any("unparseable" in n for n in res.notes)
    # the bad file is remembered at this content - no grind next cycle
    again = kb_drop.ingest_git_intake(d, seen)
    assert (again.files, again.failed) == (0, 0)


def test_absent_intake_dir_is_silent(tmp_path):
    res = kb_drop.ingest_git_intake(tmp_path / "nope", tmp_path / "s.json")
    assert res.files == 0 and not res.notes
