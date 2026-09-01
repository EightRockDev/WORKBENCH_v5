"""
Eight Rock Workbench — deal ingest sidecar.

A small FastAPI service that runs alongside the Streamlit Workbench and accepts
deal records pushed from the daily Cowork sweep of the SharePoint 03-Deals tree.

DESIGN CONSTRAINT — READ THIS BEFORE CHANGING ANYTHING
------------------------------------------------------
workbench.db is ~6.5 GB of live production data. This service is deliberately
ADDITIVE ONLY:

  * It creates exactly one new table (deal_sweep_inbox) plus its indexes.
  * It never reads, writes, alters, or drops any pre-existing table.
  * It never changes journal_mode, synchronous, or any other persistent PRAGMA,
    so it cannot alter how Streamlit sees the database.
  * Every write is a single short transaction with a busy_timeout, so a
    concurrent Streamlit read is never starved.

Inbound deals land in deal_sweep_inbox with status='pending'. Promoting a
pending row into your real property tables is a separate, deliberate step you
control (see merge_helper.py and INGEST-API-README.md). Nothing is auto-merged.

Run:
    uv run python -m uvicorn ingest_api:app --host 127.0.0.1 --port 8601
or just double-click ingest_api.bat
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import (APIRouter, BackgroundTasks, Depends, FastAPI, Header,
                     HTTPException, Request, status)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = APP_DIR / "data" / "workbench.db"


def _load_dotenv() -> None:
    """Read .env from this folder into the environment.

    api_server.py may not load .env itself, which would leave this service
    running with no password while the client has one - every call then comes
    back 401. Reading it here removes that dependency. Anything already set in
    the real environment wins.
    """
    path = APP_DIR / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()



DB_PATH = Path(os.environ.get("EIGHT_ROCK_DB_PATH", DEFAULT_DB))
TOKEN = os.environ.get("EIGHT_ROCK_INGEST_TOKEN", "").strip()
BUSY_TIMEOUT_MS = 5000
INBOX_TABLE = "deal_sweep_inbox"
DOCS_TABLE = "deal_sweep_docs"

# Where the actual RR / T-12 / OM files land on disk, so the Workbench can
# open them without going back to SharePoint.
DOCS_ROOT = Path(os.environ.get(
    "EIGHT_ROCK_DOCS_ROOT", str(APP_DIR / "data" / "deal_docs")))
MAX_DOC_BYTES = int(os.environ.get("EIGHT_ROCK_MAX_DOC_BYTES", 75 * 1024 * 1024))

# After the daily sweep pushes deals, run the rest of the chain automatically:
# promote them into the property list, then copy the documents across.
AUTO_PROMOTE = os.environ.get("EIGHT_ROCK_AUTO_PROMOTE", "1").strip() not in ("0", "false", "no")
AUTO_DOCSYNC = os.environ.get("EIGHT_ROCK_AUTO_DOCSYNC", "1").strip() not in ("0", "false", "no")

ALLOWED_DOC_EXT = {
    ".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".docx", ".doc", ".txt", ".json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s ingest_api %(message)s",
)
log = logging.getLogger("ingest_api")


def _fail_fast() -> None:
    """Refuse to start misconfigured rather than run wide open."""
    problems: list[str] = []
    if not TOKEN:
        problems.append(
            "EIGHT_ROCK_INGEST_TOKEN is not set. Generate one with:\n"
            '      python -c "import secrets;print(secrets.token_urlsafe(32))"\n'
            "    then add it to python_workbench\\.env"
        )
    elif len(TOKEN) < 24:
        problems.append(
            f"EIGHT_ROCK_INGEST_TOKEN is only {len(TOKEN)} characters. "
            "Use at least 24 (token_urlsafe(32) gives 43)."
        )
    if not DB_PATH.parent.exists():
        problems.append(f"Database directory does not exist: {DB_PATH.parent}")
    if problems:
        for p in problems:
            log.error("STARTUP REFUSED: %s", p)
        sys.exit(1)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {INBOX_TABLE} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_key        TEXT    NOT NULL UNIQUE,
    deal_name       TEXT    NOT NULL,
    state           TEXT,
    city            TEXT,
    units           INTEGER,
    sharepoint_url  TEXT,
    completeness    TEXT,
    doc_hash        TEXT,
    payload_json    TEXT    NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'cowork-deals-sweep',
    first_seen_utc  TEXT    NOT NULL,
    last_seen_utc   TEXT    NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1,
    status          TEXT    NOT NULL DEFAULT 'pending',
    merged_at_utc   TEXT
);
CREATE INDEX IF NOT EXISTS ix_{INBOX_TABLE}_status   ON {INBOX_TABLE}(status);
CREATE INDEX IF NOT EXISTS ix_{INBOX_TABLE}_lastseen ON {INBOX_TABLE}(last_seen_utc);
CREATE INDEX IF NOT EXISTS ix_{INBOX_TABLE}_state    ON {INBOX_TABLE}(state);

CREATE TABLE IF NOT EXISTS {DOCS_TABLE} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_key        TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    filename        TEXT    NOT NULL,
    rel_path        TEXT    NOT NULL,
    abs_path        TEXT    NOT NULL,
    size_bytes      INTEGER NOT NULL,
    sha256          TEXT    NOT NULL,
    source_item_id  TEXT,
    source_modified TEXT,
    stored_at_utc   TEXT    NOT NULL,
    UNIQUE(deal_key, kind, filename)
);
CREATE INDEX IF NOT EXISTS ix_{DOCS_TABLE}_deal   ON {DOCS_TABLE}(deal_key);
CREATE INDEX IF NOT EXISTS ix_{DOCS_TABLE}_sha    ON {DOCS_TABLE}(sha256);

CREATE TABLE IF NOT EXISTS deal_sweep_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL UNIQUE,
    ran_at_utc    TEXT    NOT NULL,
    had_changes   INTEGER NOT NULL,
    summary       TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL,
    read_at_utc   TEXT
);
CREATE INDEX IF NOT EXISTS ix_deal_sweep_runs_ran ON deal_sweep_runs(ran_at_utc);
"""


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    # Session-scoped only. Deliberately NOT setting journal_mode or synchronous:
    # those are persistent and would change how Streamlit sees this database.
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
    log.info("Inbox table ready: %s in %s", INBOX_TABLE, DB_PATH)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Document storage
# --------------------------------------------------------------------------

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ &()+-]")


def safe_component(raw: str, fallback: str = "unnamed") -> str:
    """Reduce untrusted text to a single safe path component.

    Defends against traversal ('../'), absolute paths, NTFS alternate data
    streams (':'), reserved Windows device names, and unicode look-alikes.
    Never returns a value containing a path separator.
    """
    raw = unicodedata.normalize("NFKD", raw or "")
    raw = raw.replace("\\", "/").split("/")[-1]        # strip any directory part
    raw = _SAFE_CHARS.sub("_", raw).strip(". ")
    raw = re.sub(r"_{3,}", "__", raw)[:120]
    if not raw:
        return fallback
    stem = raw.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
                "LPT1", "LPT2", "LPT3"}:
        raw = f"_{raw}"
    return raw


def doc_dir(deal_key: str, kind: str) -> Path:
    """Resolve the on-disk folder for a deal's documents, and prove it stays
    inside DOCS_ROOT before any write happens."""
    target = (DOCS_ROOT / safe_component(deal_key, "unknown-deal")
              / safe_component(kind, "other")).resolve()
    root = DOCS_ROOT.resolve()
    if root != target and root not in target.parents:
        raise HTTPException(400, "Resolved path escapes the document root")
    return target


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def require_token(x_ingest_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_ingest_token, TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Ingest-Token",
        )


# --------------------------------------------------------------------------
# Payload models
# --------------------------------------------------------------------------

class SourceDoc(BaseModel):
    """One RR / T-12 / OM file found in the SharePoint deal folder."""

    kind: Literal["rentRoll", "t12", "om", "other"]
    name: str
    item_id: str | None = None
    modified_utc: str | None = None
    size_bytes: int | None = None
    folder_path: str | None = None


class Metrics(BaseModel):
    """Headline numbers extracted from the RR / T-12 / OM. All optional —
    the sweep sends whatever it could read with confidence and omits the rest."""

    units: int | None = None
    occupancy_pct: float | None = Field(default=None, ge=0, le=100)
    gross_potential_rent: float | None = None
    other_income: float | None = None
    t12_total_expenses: float | None = None
    t12_noi: float | None = None
    asking_price: float | None = None
    price_per_unit: float | None = None
    avg_in_place_rent: float | None = None
    avg_unit_sqft: float | None = None
    year_built: int | None = None
    implied_cap_rate: float | None = None
    as_of: str | None = None


class DealPayload(BaseModel):
    deal_key: str = Field(min_length=3, max_length=200,
                          description="SharePoint folder itemId — stable across renames")
    deal_name: str = Field(min_length=1, max_length=300)
    state: str | None = Field(default=None, max_length=2)
    city: str | None = None
    address: str | None = None
    sharepoint_url: str | None = None
    completeness: Literal["complete", "partial", "none"] = "none"
    doc_hash: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    documents: list[SourceDoc] = Field(default_factory=list)
    notes: str | None = None
    swept_at_utc: str | None = None

    @field_validator("state")
    @classmethod
    def upper_state(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class BatchPayload(BaseModel):
    deals: list[DealPayload] = Field(min_length=1, max_length=500)
    run_id: str | None = None


class DocumentUpload(BaseModel):
    """One source document, base64-encoded, pulled from SharePoint."""

    kind: Literal["rentRoll", "t12", "om", "other"]
    filename: str = Field(min_length=1, max_length=260)
    content_b64: str = Field(min_length=1)
    source_item_id: str | None = None
    source_modified: str | None = None


class DocumentRegistration(BaseModel):
    """Metadata for a document doc_sync.py has already copied into place.

    No bytes — the file is on disk already. This records it so the manifest is
    accurate and the next run knows not to copy it again.
    """

    kind: Literal["rentRoll", "t12", "om", "other"]
    filename: str = Field(min_length=1, max_length=260)
    rel_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    source_item_id: str | None = None
    source_modified: str | None = None


class DigestPayload(BaseModel):
    """A daily run report, kept so the Workbench can show a change feed."""

    run_id: str
    ran_at_utc: str
    had_changes: bool
    summary: str
    new_deals: list[str] = Field(default_factory=list)
    updated_deals: list[str] = Field(default_factory=list)
    documents_transferred: int = 0
    warnings: list[str] = Field(default_factory=list)
    detail_html: str | None = None


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


def include_ingest_routes(app: FastAPI) -> APIRouter:
    """Mount the deal-ingest routes onto an existing FastAPI app.

    In api_server.py, two lines:

        from ingest_routes import include_ingest_routes
        include_ingest_routes(app)

    Creates its own tables on first call and touches nothing else in
    workbench.db. Everything lands under /v1/ingest/.
    """
    if not TOKEN:
        log.error("EIGHT_ROCK_INGEST_TOKEN is not set - every ingest call "
                  "will return 401. Add it to .env and restart the API.")
    elif len(TOKEN) < 24:
        log.error("EIGHT_ROCK_INGEST_TOKEN is too short (%d chars, need 24+).",
                  len(TOKEN))
    ensure_schema()
    app.include_router(router)
    log.info("Deal ingest routes mounted at /v1/ingest (db=%s)", DB_PATH)
    return router


def _upsert(conn: sqlite3.Connection, deal: DealPayload) -> tuple[str, int]:
    """Insert a new pending row, or refresh an existing one.

    A deal already merged into the real tables is NOT reopened unless its
    documents actually changed (doc_hash differs) — that is what makes the
    daily push idempotent and quiet on unchanged deals.
    """
    now = utcnow()
    payload = deal.model_dump(mode="json")

    row = conn.execute(
        f"SELECT id, doc_hash, status, revision FROM {INBOX_TABLE} WHERE deal_key = ?",
        (deal.deal_key,),
    ).fetchone()

    if row is None:
        cur = conn.execute(
            f"""INSERT INTO {INBOX_TABLE}
                (deal_key, deal_name, state, city, units, sharepoint_url,
                 completeness, doc_hash, payload_json, first_seen_utc,
                 last_seen_utc, revision, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'pending')""",
            (deal.deal_key, deal.deal_name, deal.state, deal.city,
             deal.metrics.units, deal.sharepoint_url, deal.completeness,
             deal.doc_hash, json.dumps(payload, separators=(",", ":")),
             now, now),
        )
        return "created", int(cur.lastrowid or 0)

    unchanged = row["doc_hash"] == deal.doc_hash and deal.doc_hash is not None
    if unchanged:
        conn.execute(
            f"UPDATE {INBOX_TABLE} SET last_seen_utc = ? WHERE id = ?",
            (now, row["id"]),
        )
        return "unchanged", int(row["id"])

    new_status = "pending" if row["status"] == "merged" else row["status"]
    conn.execute(
        f"""UPDATE {INBOX_TABLE}
            SET deal_name=?, state=?, city=?, units=?, sharepoint_url=?,
                completeness=?, doc_hash=?, payload_json=?, last_seen_utc=?,
                revision=revision+1, status=?, merged_at_utc=NULL
            WHERE id=?""",
        (deal.deal_name, deal.state, deal.city, deal.metrics.units,
         deal.sharepoint_url, deal.completeness, deal.doc_hash,
         json.dumps(payload, separators=(",", ":")), now, new_status, row["id"]),
    )
    return "updated", int(row["id"])


@router.get("/health")
def healthz() -> dict[str, Any]:
    """Unauthenticated liveness probe — safe to expose through the tunnel.
    Reports counts only, never deal content."""
    try:
        with db() as conn:
            rows = conn.execute(
                f"SELECT status, COUNT(*) AS c FROM {INBOX_TABLE} GROUP BY status"
            ).fetchall()
        counts = {r["status"]: r["c"] for r in rows}
        return {"ok": True, "version": __version__, "db": str(DB_PATH), "inbox": counts}
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("healthz failed")
        return {"ok": False, "version": __version__, "error": str(exc)}


@router.post("/deal", dependencies=[Depends(require_token)])
def ingest_deal(deal: DealPayload) -> dict[str, Any]:
    with db() as conn:
        action, row_id = _upsert(conn, deal)
    log.info("deal %s: %s (%s)", action, deal.deal_name, deal.deal_key[:12])
    return {"ok": True, "action": action, "id": row_id, "deal_key": deal.deal_key}


def _auto_promote() -> dict[str, Any]:
    """Put the newly-pushed deals into the property list.

    Fast and idempotent, so it runs inline and the caller sees the result.
    """
    try:
        sys.path.insert(0, str(APP_DIR))
        import promote_deals
        return promote_deals.run_promote(go=True, quiet=True, db_path=DB_PATH)
    except Exception as exc:
        log.exception("auto-promote failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _auto_docsync() -> None:
    """Copy the deal documents onto this machine.

    Slow - it walks the whole 03-Deals tree - so this runs in the background
    after the response has already gone back.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(APP_DIR / "doc_sync.py")],
            cwd=str(APP_DIR), capture_output=True, text=True, timeout=1800)
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        log.info("auto doc-sync finished (rc=%s): %s", proc.returncode, " | ".join(tail))
    except Exception as exc:
        log.error("auto doc-sync failed: %s", exc)


@router.post("/batch", dependencies=[Depends(require_token)])
def ingest_batch(batch: BatchPayload,
                 background: BackgroundTasks) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with db() as conn:  # one transaction: all deals land or none do
        for deal in batch.deals:
            action, row_id = _upsert(conn, deal)
            results.append({"deal_key": deal.deal_key, "action": action, "id": row_id})
    summary = {
        a: sum(1 for r in results if r["action"] == a)
        for a in ("created", "updated", "unchanged")
    }
    log.info("batch run_id=%s %s", batch.run_id, summary)

    out: dict[str, Any] = {"ok": True, "run_id": batch.run_id,
                           "summary": summary, "results": results}

    # Only bother when something actually changed.
    touched = summary["created"] + summary["updated"]
    if touched and AUTO_PROMOTE:
        out["promoted"] = _auto_promote()
        log.info("auto-promote: %s", out["promoted"])
    if touched and AUTO_DOCSYNC and (APP_DIR / "doc_sync.py").exists():
        background.add_task(_auto_docsync)
        out["doc_sync"] = "started in the background"

    return out


@router.post("/deal/{deal_key}/document", dependencies=[Depends(require_token)])
def upload_document(deal_key: str, doc: DocumentUpload) -> dict[str, Any]:
    """Store one RR / T-12 / OM file on disk next to the Workbench.

    The sweep reads the bytes out of SharePoint and posts them here, so the
    documents are openable locally without a round trip to O365.
    """
    try:
        blob = base64.b64decode(doc.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "content_b64 is not valid base64")

    if not blob:
        raise HTTPException(400, "Empty document")
    if len(blob) > MAX_DOC_BYTES:
        raise HTTPException(413, f"Document exceeds {MAX_DOC_BYTES} bytes")

    fname = safe_component(doc.filename, "document.bin")
    ext = Path(fname).suffix.lower()
    if ext not in ALLOWED_DOC_EXT:
        raise HTTPException(
            415, f"Extension '{ext or '(none)'}' not allowed. "
                 f"Permitted: {', '.join(sorted(ALLOWED_DOC_EXT))}")

    digest = hashlib.sha256(blob).hexdigest()

    # Idempotency: identical bytes already on disk means nothing to do. This is
    # what keeps a daily sweep from re-sending the same 40 MB of scanned OMs.
    with db() as conn:
        prior = conn.execute(
            f"""SELECT id, sha256, abs_path FROM {DOCS_TABLE}
                WHERE deal_key=? AND kind=? AND filename=?""",
            (deal_key, doc.kind, fname),
        ).fetchone()
        if prior and prior["sha256"] == digest and Path(prior["abs_path"]).exists():
            return {"ok": True, "action": "unchanged", "id": prior["id"],
                    "filename": fname, "sha256": digest, "bytes": len(blob)}

    target_dir = doc_dir(deal_key, doc.kind)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / fname

    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(blob)
    tmp.replace(dest)  # atomic: the Workbench never sees a half-written file

    rel = dest.relative_to(DOCS_ROOT.resolve()).as_posix()
    now = utcnow()
    with db() as conn:
        conn.execute(
            f"""INSERT INTO {DOCS_TABLE}
                (deal_key, kind, filename, rel_path, abs_path, size_bytes,
                 sha256, source_item_id, source_modified, stored_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(deal_key, kind, filename) DO UPDATE SET
                    rel_path=excluded.rel_path, abs_path=excluded.abs_path,
                    size_bytes=excluded.size_bytes, sha256=excluded.sha256,
                    source_item_id=excluded.source_item_id,
                    source_modified=excluded.source_modified,
                    stored_at_utc=excluded.stored_at_utc""",
            (deal_key, doc.kind, fname, rel, str(dest), len(blob), digest,
             doc.source_item_id, doc.source_modified, now),
        )
        row = conn.execute(
            f"SELECT id FROM {DOCS_TABLE} WHERE deal_key=? AND kind=? AND filename=?",
            (deal_key, doc.kind, fname),
        ).fetchone()

    action = "replaced" if prior else "stored"
    log.info("doc %s: %s/%s/%s (%d bytes)", action, deal_key[:12], doc.kind,
             fname, len(blob))
    return {"ok": True, "action": action, "id": int(row["id"]), "filename": fname,
            "rel_path": rel, "sha256": digest, "bytes": len(blob)}


@router.post("/deal/{deal_key}/document/register",
          dependencies=[Depends(require_token)])
def register_document(deal_key: str, doc: DocumentRegistration) -> dict[str, Any]:
    """Record a document doc_sync.py copied in locally.

    Without this the docs index would never learn about locally-copied files,
    the manifest would stay empty, and every run would re-copy everything.
    """
    fname = safe_component(doc.filename, "document.bin")
    dest = doc_dir(deal_key, doc.kind) / fname
    if not dest.exists():
        raise HTTPException(
            404, f"No file at {dest} — copy it before registering it.")

    actual = dest.stat().st_size
    if actual != doc.size_bytes:
        raise HTTPException(
            409, f"Size mismatch: caller said {doc.size_bytes}, disk has {actual}. "
                 "The copy may be incomplete.")

    now = utcnow()
    with db() as conn:
        prior = conn.execute(
            f"SELECT id, sha256 FROM {DOCS_TABLE} "
            "WHERE deal_key=? AND kind=? AND filename=?",
            (deal_key, doc.kind, fname),
        ).fetchone()
        if prior and prior["sha256"] == doc.sha256:
            return {"ok": True, "action": "unchanged", "id": prior["id"],
                    "filename": fname}
        conn.execute(
            f"""INSERT INTO {DOCS_TABLE}
                (deal_key, kind, filename, rel_path, abs_path, size_bytes,
                 sha256, source_item_id, source_modified, stored_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(deal_key, kind, filename) DO UPDATE SET
                    rel_path=excluded.rel_path, abs_path=excluded.abs_path,
                    size_bytes=excluded.size_bytes, sha256=excluded.sha256,
                    source_item_id=excluded.source_item_id,
                    source_modified=excluded.source_modified,
                    stored_at_utc=excluded.stored_at_utc""",
            (deal_key, doc.kind, fname, doc.rel_path, str(dest), actual,
             doc.sha256, doc.source_item_id, doc.source_modified, now),
        )
        row = conn.execute(
            f"SELECT id FROM {DOCS_TABLE} WHERE deal_key=? AND kind=? AND filename=?",
            (deal_key, doc.kind, fname),
        ).fetchone()

    action = "replaced" if prior else "registered"
    log.info("doc %s: %s/%s/%s", action, deal_key[:12], doc.kind, fname)
    return {"ok": True, "action": action, "id": int(row["id"]), "filename": fname}


@router.get("/deal/{deal_key}/documents", dependencies=[Depends(require_token)])
def list_documents(deal_key: str) -> dict[str, Any]:
    """What the Workbench already holds for this deal.

    The sweep calls this FIRST each day and skips any file whose sha256 it
    already sent — so unchanged documents are never re-transferred.
    """
    with db() as conn:
        rows = conn.execute(
            f"""SELECT id, kind, filename, rel_path, size_bytes, sha256,
                       source_item_id, source_modified, stored_at_utc
                FROM {DOCS_TABLE} WHERE deal_key = ?
                ORDER BY kind, filename""",
            (deal_key,),
        ).fetchall()
    return {"ok": True, "deal_key": deal_key, "count": len(rows),
            "documents": [dict(r) for r in rows]}


@router.get("/documents/manifest", dependencies=[Depends(require_token)])
def documents_manifest() -> dict[str, Any]:
    """Every stored document hash, keyed by deal — one call, whole picture.

    Lets a sweep run decide what to transfer without N round trips.
    """
    with db() as conn:
        rows = conn.execute(
            f"SELECT deal_key, kind, filename, sha256, size_bytes FROM {DOCS_TABLE}"
        ).fetchall()
    manifest: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        manifest.setdefault(r["deal_key"], []).append(
            {"kind": r["kind"], "filename": r["filename"],
             "sha256": r["sha256"], "size_bytes": r["size_bytes"]})
    return {"ok": True, "deal_count": len(manifest),
            "document_count": len(rows), "manifest": manifest}


@router.get("/inbox", dependencies=[Depends(require_token)])
def read_inbox(
    status_filter: str = "pending",
    limit: int = 100,
    include_payload: bool = False,
) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    cols = ("id, deal_key, deal_name, state, city, units, sharepoint_url, "
            "completeness, revision, status, first_seen_utc, last_seen_utc")
    if include_payload:
        cols += ", payload_json"
    where, params = "", []
    if status_filter != "all":
        where, params = "WHERE status = ?", [status_filter]
    with db() as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM {INBOX_TABLE} {where} "
            f"ORDER BY last_seen_utc DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if include_payload and d.get("payload_json"):
            d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return {"ok": True, "count": len(out), "deals": out}


@router.post("/digest", dependencies=[Depends(require_token)])
def post_digest(digest: DigestPayload) -> dict[str, Any]:
    """Record one sweep run's report, for the Workbench's change feed."""
    with db() as conn:
        conn.execute(
            """INSERT INTO deal_sweep_runs
               (run_id, ran_at_utc, had_changes, summary, payload_json)
               VALUES (?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                 ran_at_utc=excluded.ran_at_utc, had_changes=excluded.had_changes,
                 summary=excluded.summary, payload_json=excluded.payload_json""",
            (digest.run_id, digest.ran_at_utc, int(digest.had_changes),
             digest.summary,
             json.dumps(digest.model_dump(mode="json"), separators=(",", ":"))),
        )
    log.info("digest %s changes=%s %s", digest.run_id, digest.had_changes,
             digest.summary[:120])
    return {"ok": True, "run_id": digest.run_id}


@router.get("/digest", dependencies=[Depends(require_token)])
def get_digests(limit: int = 30, only_changes: bool = False) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    where = "WHERE had_changes = 1" if only_changes else ""
    with db() as conn:
        rows = conn.execute(
            f"""SELECT run_id, ran_at_utc, had_changes, summary, payload_json
                FROM deal_sweep_runs {where}
                ORDER BY ran_at_utc DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    runs = []
    for r in rows:
        d = dict(r)
        d["had_changes"] = bool(d["had_changes"])
        d["payload"] = json.loads(d.pop("payload_json"))
        runs.append(d)
    return {"ok": True, "count": len(runs), "runs": runs}


@router.post("/inbox/{row_id}/status", dependencies=[Depends(require_token)])
def set_status(row_id: int, new_status: Literal["pending", "merged", "ignored"]) -> dict[str, Any]:
    merged_at = utcnow() if new_status == "merged" else None
    with db() as conn:
        cur = conn.execute(
            f"UPDATE {INBOX_TABLE} SET status = ?, merged_at_utc = ? WHERE id = ?",
            (new_status, merged_at, row_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, f"No inbox row with id {row_id}")
    return {"ok": True, "id": row_id, "status": new_status}


def _standalone() -> "FastAPI":
    """Run these routes alone, for testing away from api_server.py."""
    solo = FastAPI(title="Eight Rock Deal Ingest (standalone)",
                   version=__version__, docs_url="/docs")
    include_ingest_routes(solo)
    return solo


if __name__ == "__main__":
    _fail_fast()
    import uvicorn

    uvicorn.run(
        _standalone(),
        host=os.environ.get("EIGHT_ROCK_INGEST_HOST", "127.0.0.1"),
        port=int(os.environ.get("EIGHT_ROCK_INGEST_PORT", "8601")),
        log_level="info",
    )
