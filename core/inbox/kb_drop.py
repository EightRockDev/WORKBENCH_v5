"""KB drop-folder ingestion — the Cowork outlook-connector hand-off
(owner 2026-08-11: "Here's how you pull from inbox into the workbench").

The owner's Cowork scheduled task already reads the O365 inbox and writes
structured knowledgebase records (state+log, per-message JSON). The
workbench does NOT need its own Graph OAuth loop for that mail - it needs a
place to receive what the connector produces. This module is that place:

    C:\\WORKBENCH_V5\\data\\inbox_kb\\*.json     (ER_INBOX_KB_DIR overrides)

Each file is one record (or a list of records). Accepted shape - every key
optional except one of subject/body/fields:

    {"external_id": "...",            # stable id; else derived from content
     "from_email": "...", "from_name": "...",
     "subject": "...", "body": "...", "received_at": "ISO-8601",
     "attachments": [{"filename": "..."}],
     "fields": {"address": ..., "city": ..., "state": ..., "units": ...,
                "asking_price": ..., "cap_rate": ..., "name": ...},
     "confidence": 0.9}

With "fields" present the record is treated as ALREADY-EXTRACTED (the
connector's agent curated it - default confidence 0.9, clearing the §6.2
gate); otherwise the deterministic extractor runs on subject/body. Every
record flows through the same pipeline as mailbox sync: Pg deals when
Postgres is up, and property-data ingestion (muni_records
kind='assessor-email', spine-merged) always. Processed files move to
processed/ so a re-run never double-ingests; a file that fails parsing
moves to failed/ with the error alongside - never silently dropped, never
blocking the rest.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def kb_dir(root: Path | None = None) -> Path:
    env = os.environ.get("ER_INBOX_KB_DIR", "").strip()
    if env:
        return Path(env)
    base = root or Path(__file__).resolve().parent.parent.parent
    return base / "data" / "inbox_kb"


@dataclass
class KbResult:
    files: int = 0
    records: int = 0
    ingested: int = 0
    linked: int = 0          # records that landed property data
    failed: int = 0
    notes: list = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


def _records_in(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        # {"records": [...]} envelope or a single record
        inner = payload.get("records")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
        return [payload]
    return []


def _external_id(rec: dict, path: Path, idx: int) -> str:
    for k in ("external_id", "message_id", "id"):
        v = rec.get(k)
        if v:
            return str(v)
    seed = json.dumps(rec, sort_keys=True, default=str)
    return f"kb-{hashlib.sha256(seed.encode()).hexdigest()[:16]}-{idx}"


def _extraction_for(rec: dict):
    """Pre-extracted fields when the connector supplied them, else the
    deterministic extractor over subject/body."""
    from core.inbox import extract as ex
    fields = rec.get("fields")
    if isinstance(fields, dict) and any(v not in (None, "")
                                        for v in fields.values()):
        conf = rec.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.9
        except (TypeError, ValueError):
            conf = 0.9
        clean = {k: v for k, v in fields.items() if v not in (None, "")}
        return ex.Extraction(
            fields=clean,
            confidences={k: conf for k in clean},
            evidence=[f"kb record ({rec.get('source', 'cowork-connector')})"])
    return ex.extract_deal(subject=rec.get("subject"),
                           body=rec.get("body"),
                           attachments=rec.get("attachments"))


def _resolve_org_and_owner() -> tuple[str | None, str | None]:
    """Single-org pilot: the org row and its owner account. Env overrides
    (ER_KB_ORG_ID / ER_KB_OWNER_EMAIL) for multi-org later."""
    from data import pg
    org = os.environ.get("ER_KB_ORG_ID", "").strip() or None
    email = (os.environ.get("ER_KB_OWNER_EMAIL", "").strip()
             or "bmccune@gmail.com")
    if not pg.is_reachable():
        return None, None
    try:
        with pg.connection() as conn, conn.cursor() as cur:
            if org is None:
                cur.execute("SELECT id FROM organizations ORDER BY "
                            "created_at LIMIT 1")
                row = cur.fetchone()
                org = str(row["id"]) if row else None
            cur.execute("SELECT id FROM users WHERE lower(email)=lower(%s) "
                        "LIMIT 1", (email,))
            row = cur.fetchone()
            user = str(row["id"]) if row else None
            if user is None:
                cur.execute("SELECT id FROM users ORDER BY created_at LIMIT 1")
                row = cur.fetchone()
                user = str(row["id"]) if row else None
        return org, user
    except Exception:
        return None, None


def ingest_dir(directory: Path | None = None) -> KbResult:
    """Sweep the drop folder once. Idempotent: processed files are moved
    aside; re-dropped duplicates dedupe on external_id in the engine."""
    d = directory or kb_dir()
    res = KbResult()
    if not d.is_dir():
        res.notes.append(f"drop dir absent: {d} (connector not writing yet)")
        return res
    files = sorted(p for p in d.iterdir()
                   if p.suffix.lower() == ".json" and p.is_file())
    if not files:
        return res
    (d / "processed").mkdir(exist_ok=True)
    (d / "failed").mkdir(exist_ok=True)

    from data import pg
    use_pg = pg.is_reachable()
    org = owner = None
    if use_pg:
        org, owner = _resolve_org_and_owner()
        use_pg = bool(org)
        if not use_pg:
            res.notes.append("Postgres up but no organization row - deals "
                             "skipped, property data still ingested")

    for path in files:
        res.files += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            recs = _records_in(payload)
            if not recs:
                raise ValueError("no records in file")
        except Exception as exc:
            res.failed += 1
            shutil.move(str(path), d / "failed" / path.name)
            (d / "failed" / (path.name + ".error.txt")).write_text(repr(exc))
            res.notes.append(f"{path.name}: unparseable ({exc})")
            continue
        for i, rec in enumerate(recs):
            res.records += 1
            try:
                if _ingest_record(rec, path, i, use_pg, org, owner, res):
                    res.ingested += 1
            except Exception as exc:      # one record never sinks the sweep
                res.failed += 1
                res.notes.append(f"{path.name}[{i}]: {exc!r}")
        shutil.move(str(path), d / "processed" / path.name)
    return res


def _ingest_record(rec, path, idx, use_pg, org, owner, res) -> bool:
    ext_id = _external_id(rec, path, idx)
    msg = {"provider": "kb", "external_id": ext_id,
           "from_email": rec.get("from_email"),
           "from_name": rec.get("from_name"),
           "subject": rec.get("subject"), "body": rec.get("body"),
           "received_at": rec.get("received_at") or
           dt.datetime.now(dt.timezone.utc).isoformat(),
           "attachments": rec.get("attachments") or []}
    if use_pg and (rec.get("subject") or rec.get("body")):
        # Full pipeline: classify/extract/gate -> deals + property link.
        from core.inbox import engine
        r = engine.ingest_message(org, msg, owner_user_id=owner)
        if r.deal_id:
            res.linked += 1
        return r.status in ("auto_applied", "queued", "new")
    # Pg-free (or body-less pre-extracted record): property data only.
    from core.inbox import property_link
    e = _extraction_for(rec)
    pid = property_link.link_message(org or "-", ext_id, msg, e)
    if pid:
        res.linked += 1
    return bool(pid) or bool(e.fields)
