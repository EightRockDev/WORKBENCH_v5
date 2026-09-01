"""Contract tests for the Eight Rock ingest sidecar.

Run:  uv run python -m pytest test_ingest_api.py -v

These prove the three properties that matter for pointing this at a 6.5 GB
production workbench.db:

  1. It only ever creates its own table — pre-existing tables are untouched.
  2. Auth is enforced on every write and read path.
  3. Re-pushing an unchanged deal is a no-op (idempotent daily sweep).
"""

import base64
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-that-is-definitely-long-enough-32"


@pytest.fixture()
def client(tmp_path: Path):
    db_file = tmp_path / "workbench.db"

    # Stand up a fake "existing" production table so we can prove we never
    # touch it.
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE properties (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO properties (name) VALUES ('Miars Farm')")
    conn.execute("INSERT INTO properties (name) VALUES ('Long Lofts')")
    conn.commit()
    conn.close()

    docs_root = tmp_path / "deal_docs"
    os.environ["EIGHT_ROCK_DB_PATH"] = str(db_file)
    os.environ["EIGHT_ROCK_INGEST_TOKEN"] = TOKEN
    os.environ["EIGHT_ROCK_DOCS_ROOT"] = str(docs_root)
    docs_root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).parent))
    import ingest_routes as ingest_api

    importlib.reload(ingest_api)
    from fastapi import FastAPI as _F
    _app = _F()
    ingest_api.include_ingest_routes(_app)
    ingest_api.app = _app
    with TestClient(ingest_api.app) as c:
        c.db_file = db_file  # type: ignore[attr-defined]
        c.docs_root = docs_root  # type: ignore[attr-defined]
        yield c


def deal(key="016ABC", name="River's Edge", doc_hash="hash-v1", **kw):
    body = {
        "deal_key": key,
        "deal_name": name,
        "state": "nc",
        "city": "Elizabeth City",
        "sharepoint_url": "https://example.sharepoint.com/deal",
        "completeness": "complete",
        "doc_hash": doc_hash,
        "metrics": {"units": 56, "occupancy_pct": 94.5, "t12_noi": 412000},
        "documents": [
            {"kind": "rentRoll", "name": "RR 07.2026.xlsx", "item_id": "016RR"},
            {"kind": "t12", "name": "T12 07.2026.xlsx", "item_id": "016T12"},
        ],
    }
    body.update(kw)
    return body


def auth():
    return {"X-Ingest-Token": TOKEN}


# --- 1. Safety: additive only -------------------------------------------

def test_never_touches_existing_tables(client):
    client.post("/v1/ingest/deal", json=deal(), headers=auth())

    conn = sqlite3.connect(client.db_file)  # type: ignore[attr-defined]
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    props = conn.execute("SELECT name FROM properties ORDER BY id").fetchall()
    conn.close()

    assert "deal_sweep_inbox" in names, "inbox table should be created"
    assert "properties" in names, "existing table must survive"
    assert props == [("Miars Farm",), ("Long Lofts",)], "existing rows untouched"


def test_journal_mode_not_mutated(client):
    """The service must not flip the DB into WAL behind Streamlit's back."""
    conn = sqlite3.connect(client.db_file)  # type: ignore[attr-defined]
    before = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()

    client.post("/v1/ingest/deal", json=deal(), headers=auth())

    conn = sqlite3.connect(client.db_file)  # type: ignore[attr-defined]
    after = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert before == after


# --- 2. Auth -------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/ingest/deal"),
        ("post", "/v1/ingest/batch"),
        ("get", "/v1/ingest/inbox"),
    ],
)
def test_requires_token(client, method, path):
    if method == "get":
        resp = client.get(path)
    else:
        body = {"deals": [deal()]} if "batch" in path else deal()
        resp = client.post(path, json=body)
    assert resp.status_code == 401


def test_rejects_wrong_token(client):
    resp = client.post("/v1/ingest/deal", json=deal(),
                       headers={"X-Ingest-Token": "wrong"})
    assert resp.status_code == 401


def test_health_is_open(client):
    resp = client.get("/v1/ingest/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_health_leaks_no_deal_content(client):
    client.post("/v1/ingest/deal", json=deal(name="Secret Deal"), headers=auth())
    assert "Secret Deal" not in client.get("/v1/ingest/health").text


# --- 3. Idempotency ------------------------------------------------------

def test_create_then_unchanged(client):
    first = client.post("/v1/ingest/deal", json=deal(), headers=auth()).json()
    assert first["action"] == "created"

    second = client.post("/v1/ingest/deal", json=deal(), headers=auth()).json()
    assert second["action"] == "unchanged"
    assert second["id"] == first["id"], "must not duplicate the deal"


def test_changed_docs_produce_update(client):
    client.post("/v1/ingest/deal", json=deal(), headers=auth())
    resp = client.post("/v1/ingest/deal", json=deal(doc_hash="hash-v2"),
                       headers=auth()).json()
    assert resp["action"] == "updated"


def test_merged_deal_reopens_only_on_real_change(client):
    created = client.post("/v1/ingest/deal", json=deal(), headers=auth()).json()
    client.post(f"/v1/ingest/inbox/{created['id']}/status?new_status=merged",
                headers=auth())

    # Same docs -> stays merged, does not nag.
    client.post("/v1/ingest/deal", json=deal(), headers=auth())
    rows = client.get("/v1/ingest/inbox?status_filter=merged", headers=auth()).json()
    assert rows["count"] == 1

    # New docs -> reopens as pending.
    client.post("/v1/ingest/deal", json=deal(doc_hash="hash-v2"), headers=auth())
    pending = client.get("/v1/ingest/inbox?status_filter=pending", headers=auth()).json()
    assert pending["count"] == 1
    assert pending["deals"][0]["revision"] == 2


# --- 4. Batch + payload fidelity ----------------------------------------

def test_batch_is_atomic_and_summarised(client):
    body = {"run_id": "sweep-2026-08-28", "deals": [deal(key="016AAA", name="A"),
                                                    deal(key="016BBB", name="B"),
                                                    deal(key="016CCC", name="C")]}
    resp = client.post("/v1/ingest/batch", json=body, headers=auth()).json()
    assert resp["summary"] == {"created": 3, "updated": 0, "unchanged": 0}

    again = client.post("/v1/ingest/batch", json=body, headers=auth()).json()
    assert again["summary"]["unchanged"] == 3


def test_payload_round_trips(client):
    client.post("/v1/ingest/deal", json=deal(), headers=auth())
    got = client.get("/v1/ingest/inbox?include_payload=true", headers=auth()).json()
    payload = got["deals"][0]["payload"]
    assert payload["metrics"]["t12_noi"] == 412000
    assert payload["state"] == "NC", "state should be normalised to upper case"
    assert len(payload["documents"]) == 2


def test_rejects_malformed_deal(client):
    resp = client.post("/v1/ingest/deal", json={"deal_name": "no key"}, headers=auth())
    assert resp.status_code == 422


def test_rejects_impossible_occupancy(client):
    bad = deal()
    bad["metrics"]["occupancy_pct"] = 150
    assert client.post("/v1/ingest/deal", json=bad, headers=auth()).status_code == 422


# --- 5. Document transfer ------------------------------------------------

def doc_body(filename="RR 08.2026.xlsx", kind="rentRoll", blob=b"unit,rent\n101,1250\n"):
    return {"kind": kind, "filename": filename,
            "content_b64": base64.b64encode(blob).decode(),
            "source_item_id": "016RR", "source_modified": "2026-08-27T21:59:43Z"}


def test_document_lands_on_disk_with_real_bytes(client, tmp_path):
    client.post("/v1/ingest/deal", json=deal(), headers=auth())
    blob = b"unit,rent\n101,1250\n102,1310\n"
    resp = client.post("/v1/ingest/deal/016ABC/document",
                       json=doc_body(blob=blob), headers=auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "stored"
    assert body["bytes"] == len(blob)

    written = Path(client.docs_root) / "016ABC" / "rentRoll" / "RR 08.2026.xlsx"
    assert written.exists(), "file must be on disk where the Workbench can open it"
    assert written.read_bytes() == blob, "bytes must survive the round trip intact"
    assert not list(written.parent.glob("*.part")), "no temp files left behind"


def test_document_upload_is_idempotent(client):
    client.post("/v1/ingest/deal", json=deal(), headers=auth())
    first = client.post("/v1/ingest/deal/016ABC/document", json=doc_body(),
                        headers=auth()).json()
    second = client.post("/v1/ingest/deal/016ABC/document", json=doc_body(),
                         headers=auth()).json()
    assert first["action"] == "stored"
    assert second["action"] == "unchanged", "same bytes must not re-transfer"


def test_changed_document_replaces_in_place(client):
    client.post("/v1/ingest/deal/016ABC/document", json=doc_body(), headers=auth())
    resp = client.post("/v1/ingest/deal/016ABC/document",
                       json=doc_body(blob=b"unit,rent\n101,1400\n"),
                       headers=auth()).json()
    assert resp["action"] == "replaced"
    docs = client.get("/v1/ingest/deal/016ABC/documents", headers=auth()).json()
    assert docs["count"] == 1, "revision must not create a duplicate row"


@pytest.mark.parametrize("evil", [
    "../../../../Windows/System32/evil.xlsx",
    "..\\..\\workbench.db",
    "/etc/passwd.csv",
    "C:\\Users\\bmccu\\.env.txt",
])
def test_path_traversal_is_neutralised(client, evil):
    resp = client.post("/v1/ingest/deal/016ABC/document",
                       json=doc_body(filename=evil), headers=auth())
    # Either rejected outright, or written safely inside the docs root.
    if resp.status_code == 200:
        stored = Path(client.docs_root).resolve()
        written = (stored / "016ABC").rglob(resp.json()["filename"])
        for p in written:
            assert stored in p.resolve().parents
    else:
        assert resp.status_code in (400, 415)


def test_deal_key_cannot_escape_docs_root(client):
    resp = client.post("/v1/ingest/deal/..%2F..%2Fescape/document",
                       json=doc_body(), headers=auth())
    if resp.status_code == 200:
        stored = Path(client.docs_root).resolve()
        for p in stored.rglob("RR 08.2026.xlsx"):
            assert stored in p.resolve().parents


def test_rejects_disallowed_extension(client):
    resp = client.post("/v1/ingest/deal/016ABC/document",
                       json=doc_body(filename="payload.exe"), headers=auth())
    assert resp.status_code == 415


def test_rejects_bad_base64(client):
    resp = client.post("/v1/ingest/deal/016ABC/document",
                       json={"kind": "om", "filename": "x.pdf",
                             "content_b64": "not!!base64!!"}, headers=auth())
    assert resp.status_code == 400


def test_document_upload_requires_token(client):
    assert client.post("/v1/ingest/deal/016ABC/document",
                       json=doc_body()).status_code == 401


def test_manifest_lets_sweep_skip_unchanged(client):
    client.post("/v1/ingest/deal/016ABC/document", json=doc_body(), headers=auth())
    client.post("/v1/ingest/deal/016ABC/document",
                json=doc_body(filename="T12.xlsx", kind="t12"), headers=auth())
    m = client.get("/v1/ingest/documents/manifest", headers=auth()).json()
    assert m["document_count"] == 2
    kinds = {d["kind"] for d in m["manifest"]["016ABC"]}
    assert kinds == {"rentRoll", "t12"}
    assert all(len(d["sha256"]) == 64 for d in m["manifest"]["016ABC"])


def test_register_records_a_locally_copied_file(client):
    """doc_sync copies the file, then registers it. Without registration the
    manifest stays empty and every run re-copies everything."""
    dest = Path(client.docs_root) / "016ABC" / "rentRoll"
    dest.mkdir(parents=True)
    blob = b"unit,rent\n101,1250\n"
    (dest / "RR 08.2026.xlsx").write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()

    resp = client.post("/v1/ingest/deal/016ABC/document/register", headers=auth(), json={
        "kind": "rentRoll", "filename": "RR 08.2026.xlsx",
        "rel_path": "016ABC/rentRoll/RR 08.2026.xlsx",
        "size_bytes": len(blob), "sha256": digest})
    assert resp.status_code == 200
    assert resp.json()["action"] == "registered"

    manifest = client.get("/v1/ingest/documents/manifest", headers=auth()).json()
    assert manifest["manifest"]["016ABC"][0]["sha256"] == digest

    again = client.post("/v1/ingest/deal/016ABC/document/register", headers=auth(), json={
        "kind": "rentRoll", "filename": "RR 08.2026.xlsx",
        "rel_path": "016ABC/rentRoll/RR 08.2026.xlsx",
        "size_bytes": len(blob), "sha256": digest})
    assert again.json()["action"] == "unchanged"


def test_register_refuses_when_no_file_on_disk(client):
    resp = client.post("/v1/ingest/deal/016ABC/document/register", headers=auth(), json={
        "kind": "om", "filename": "ghost.pdf", "rel_path": "016ABC/om/ghost.pdf",
        "size_bytes": 10, "sha256": "0" * 64})
    assert resp.status_code == 404


def test_register_catches_incomplete_copy(client):
    dest = Path(client.docs_root) / "016ABC" / "t12"
    dest.mkdir(parents=True)
    (dest / "T12.xlsx").write_bytes(b"short")
    resp = client.post("/v1/ingest/deal/016ABC/document/register", headers=auth(), json={
        "kind": "t12", "filename": "T12.xlsx", "rel_path": "016ABC/t12/T12.xlsx",
        "size_bytes": 999999, "sha256": "a" * 64})
    assert resp.status_code == 409


# --- 6. Digest feed ------------------------------------------------------

def digest_body(run_id="sweep-2026-08-28", changes=True):
    return {"run_id": run_id, "ran_at_utc": "2026-08-28T16:00:00Z",
            "had_changes": changes, "summary": "2 new deals, 5 documents",
            "new_deals": ["River's Edge-56u"], "documents_transferred": 5}


def test_digest_round_trips(client):
    assert client.post("/v1/ingest/digest", json=digest_body(),
                       headers=auth()).json()["ok"] is True
    got = client.get("/v1/ingest/digest", headers=auth()).json()
    assert got["count"] == 1
    assert got["runs"][0]["payload"]["documents_transferred"] == 5


def test_digest_only_changes_filter(client):
    client.post("/v1/ingest/digest", json=digest_body("r1", True), headers=auth())
    client.post("/v1/ingest/digest", json=digest_body("r2", False), headers=auth())
    assert client.get("/v1/ingest/digest", headers=auth()).json()["count"] == 2
    quiet = client.get("/v1/ingest/digest?only_changes=true", headers=auth()).json()
    assert quiet["count"] == 1


def test_digest_replay_does_not_duplicate(client):
    client.post("/v1/ingest/digest", json=digest_body(), headers=auth())
    client.post("/v1/ingest/digest", json=digest_body(), headers=auth())
    assert client.get("/v1/ingest/digest", headers=auth()).json()["count"] == 1
