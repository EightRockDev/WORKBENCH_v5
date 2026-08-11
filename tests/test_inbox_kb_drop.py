"""KB drop-folder ingestion (owner 2026-08-11: the Cowork outlook-connector
feeds the workbench through a watched folder): flexible record shapes,
idempotent file lifecycle, Pg-free property-data path, bad files quarantined
without sinking the sweep."""

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


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ER_INBOX_KB_DIR", str(tmp_path / "kb"))
    assert kb_drop.kb_dir() == tmp_path / "kb"


def test_absent_dir_is_a_note_not_an_error(tmp_path):
    res = kb_drop.ingest_dir(tmp_path / "nope")
    assert res.files == 0 and res.notes and "absent" in res.notes[0]


def test_pre_extracted_record_ingests_property_data(tmp_path, monkeypatch):
    db = _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    drop = tmp_path / "kb"
    drop.mkdir()
    (drop / "msg1.json").write_text(json.dumps({
        "external_id": "AAkM-1", "from_email": "sarah@nmrk.com",
        "subject": "Crossroads Townhomes - Norfolk",
        "fields": {"address": "1200 Ballentine Blvd", "city": "Norfolk",
                   "units": 26, "asking_price": 2850000},
    }))
    res = kb_drop.ingest_dir(drop)
    assert (res.files, res.records, res.ingested, res.failed) == (1, 1, 1, 0)
    assert res.linked == 1
    conn = sqlite3.connect(db)
    kind, tag, rec = conn.execute(
        "SELECT kind, source_url, record FROM muni_records").fetchone()
    assert kind == "assessor-email" and tag == "inbox:AAkM-1"
    assert json.loads(rec)["units"] == 26
    # file moved aside - a second sweep ingests nothing new
    assert not list(drop.glob("*.json"))
    assert (drop / "processed" / "msg1.json").exists()
    assert kb_drop.ingest_dir(drop).records == 0


def test_raw_body_record_runs_the_extractor(tmp_path, monkeypatch):
    db = _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    drop = tmp_path / "kb"
    drop.mkdir()
    (drop / "raw.json").write_text(json.dumps({
        "subject": "New to Market: 26 Units, Norfolk VA",
        "body": "OM for Crossroads, 1200 Ballentine Blvd, Norfolk, VA. "
                "26 units, asking $2,850,000 at a 7.25% cap."}))
    res = kb_drop.ingest_dir(drop)
    assert res.ingested == 1 and res.linked == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM muni_records").fetchone()[0] == 1


def test_list_and_envelope_shapes(tmp_path, monkeypatch):
    _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    drop = tmp_path / "kb"
    drop.mkdir()
    (drop / "many.json").write_text(json.dumps({"records": [
        {"subject": "a", "fields": {"address": "1200 Ballentine Blvd",
                                    "city": "Norfolk", "units": 26}},
        {"subject": "b", "fields": {"address": "1200 Ballentine Blvd",
                                    "city": "Norfolk", "units": 26}},
    ]}))
    res = kb_drop.ingest_dir(drop)
    assert res.records == 2 and res.ingested == 2


def test_bad_file_quarantined_and_rest_processed(tmp_path, monkeypatch):
    _mk_backbone(tmp_path, monkeypatch)
    _no_pg(monkeypatch)
    drop = tmp_path / "kb"
    drop.mkdir()
    (drop / "bad.json").write_text("{not json")
    (drop / "good.json").write_text(json.dumps({
        "subject": "x", "fields": {"address": "1200 Ballentine Blvd",
                                   "city": "Norfolk", "units": 26}}))
    res = kb_drop.ingest_dir(drop)
    assert res.failed == 1 and res.ingested == 1
    assert (drop / "failed" / "bad.json").exists()
    assert (drop / "failed" / "bad.json.error.txt").exists()
    assert (drop / "processed" / "good.json").exists()


def test_pg_path_routes_through_engine(tmp_path, monkeypatch):
    _mk_backbone(tmp_path, monkeypatch)
    from data import pg
    monkeypatch.setattr(pg, "is_reachable", lambda: True)
    monkeypatch.setattr(kb_drop, "_resolve_org_and_owner",
                        lambda: ("org-1", "user-1"))
    seen = {}

    class _R:
        status = "auto_applied"
        deal_id = "d-1"

    from core.inbox import engine
    monkeypatch.setattr(engine, "ingest_message",
                        lambda org, msg, owner_user_id=None:
                        seen.update(org=org, ext=msg["external_id"],
                                    owner=owner_user_id) or _R())
    drop = tmp_path / "kb"
    drop.mkdir()
    (drop / "m.json").write_text(json.dumps({
        "external_id": "X-9", "subject": "s", "body": "b"}))
    res = kb_drop.ingest_dir(drop)
    assert res.ingested == 1 and res.linked == 1
    assert seen == {"org": "org-1", "ext": "X-9", "owner": "user-1"}
