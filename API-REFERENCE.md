# Workbench Ingest API — Reference

Base URL: `http://127.0.0.1:8601` (or whatever the tunnel exposes).
Interactive docs, live: `/ingest/docs`. Raw spec: `/ingest/openapi.json`.

Auth: header `X-Ingest-Token`, on every endpoint except `/healthz`. Compared in
constant time. Wrong or missing → `401`.

Content type: `application/json` throughout. Validation failures → `422` with a
per-field detail list. Max body 80 MB.

---

## Quickest path: use the client

`ingest_client.py` wraps all of this. Import it from any Workbench module
rather than hand-rolling requests.

```python
from ingest_client import IngestClient

api = IngestClient.from_env()          # reads EIGHT_ROCK_INGEST_URL / _TOKEN

if not api.is_up():
    st.warning("Ingest service is down")
    st.stop()

for deal in api.underwritable_deals():
    st.write(deal["deal_name"], deal.metric("units"), deal.metric("t12_noi", "—"))
    for doc in deal.documents:
        st.write(doc["kind"], doc["name"])
```

In Streamlit, cache the client and the reads:

```python
@st.cache_resource
def _api():
    return IngestClient.from_env()

@st.cache_data(ttl=60)
def _pending():
    return _api().pending_deals()
```

### Client methods

| Method | Returns |
|---|---|
| `health()` / `is_up()` | Service status; `is_up()` never raises |
| `push_deal(deal)` | `{action: created\|updated\|unchanged, id}` |
| `push_deals(deals, run_id=None)` | Batch summary + per-deal results |
| `deals(status="pending", limit=100, include_payload=True)` | `list[DealRecord]` |
| `pending_deals(limit=100)` | Deals awaiting review |
| `underwritable_deals(limit=200)` | Deals with RR + T-12 + OM all present |
| `deal(deal_key)` | One `DealRecord`, or `None` |
| `set_status(row_id, status)` / `mark_merged(row_id)` | Confirmation |
| `documents(deal_key)` | Stored files for a deal |
| `document_path(deal_key, filename)` | Absolute `Path`, or `None` |
| `manifest()` | Every stored hash, keyed by deal |
| `upload_document(deal_key, path, kind)` | Sends bytes |
| `register_document(deal_key, path, kind, rel_path)` | Records a local copy |
| `push_digest(digest)` / `digests(limit, only_changes)` / `last_run()` | Run reports |

`DealRecord` is a plain dict plus `.metrics`, `.documents`, `.is_underwritable`,
and `.metric(name, default)`. Use `.metric()` — a metric the sweep could not
establish comes back as your default, never as `0`.

Errors raise `IngestError` with `.status_code`, `.detail`, `.path`.

---

## Endpoints

### `GET /healthz` — no auth

```json
{"ok": true, "version": "1.0.0", "db": "data/workbench.db",
 "inbox": {"pending": 12, "merged": 3}}
```

Counts only, never deal content. Safe to expose through the tunnel and to poll.

---

### `POST /ingest/deal`

Upsert one deal. Body: **DealPayload**.

```jsonc
{
  "deal_key": "016NAR27PFCB...",   // REQUIRED. SharePoint folder itemId — stable across renames
  "deal_name": "River's Edge-56u-Elizabeth City",  // REQUIRED
  "state": "NC",                   // 2 chars, upper-cased server-side
  "city": "Elizabeth City",
  "address": null,
  "sharepoint_url": "https://...",
  "completeness": "complete",      // complete | partial | none (default none)
  "doc_hash": "abc123def456",      // drives idempotency — see below
  "metrics": { ... },              // see Metrics
  "documents": [ ... ],            // see SourceDoc
  "notes": null,
  "swept_at_utc": "2026-08-28T16:00:00Z"
}
```

Response:

```json
{"ok": true, "action": "created", "id": 41, "deal_key": "016NAR27PFCB..."}
```

`action` is `created`, `updated`, or `unchanged`.

**Idempotency.** `deal_key` is unique. On re-push, `doc_hash` decides:

- hash matches → `unchanged`; only `last_seen_utc` moves. A deal you already
  marked merged stays merged.
- hash differs → `updated`; `revision` increments, and a merged deal reopens as
  `pending` because its documents actually changed.

That is what makes the daily push safe to run forever.

---

### `POST /ingest/batch`

Up to 500 deals in one transaction — all land or none do.

```json
{"run_id": "sweep-2026-08-28", "deals": [ /* DealPayload */ ]}
```

```json
{"ok": true, "run_id": "sweep-2026-08-28",
 "summary": {"created": 2, "updated": 1, "unchanged": 64},
 "results": [{"deal_key": "016...", "action": "created", "id": 41}]}
```

---

### `GET /ingest/inbox`

| Query | Default | Notes |
|---|---|---|
| `status_filter` | `pending` | `pending` \| `merged` \| `ignored` \| `all` |
| `limit` | 100 | clamped to 500 |
| `include_payload` | `false` | adds `payload` with metrics + documents |

```json
{"ok": true, "count": 2, "deals": [
  {"id": 41, "deal_key": "016...", "deal_name": "River's Edge-56u-Elizabeth City",
   "state": "NC", "city": "Elizabeth City", "units": 56,
   "sharepoint_url": "https://...", "completeness": "complete",
   "revision": 1, "status": "pending",
   "first_seen_utc": "2026-08-28T16:00:04Z", "last_seen_utc": "2026-08-28T16:00:04Z",
   "payload": { /* the full DealPayload as pushed */ }}
]}
```

Ordered by `last_seen_utc` descending.

---

### `POST /ingest/inbox/{row_id}/status`

Query param `new_status`: `pending` | `merged` | `ignored`.
Call this once you've imported a deal into a property record — it stops the
deal reappearing in the pending list. `merged` stamps `merged_at_utc`.
Unknown `row_id` → `404`.

---

### `POST /ingest/deal/{deal_key}/document`

Store a document by sending its bytes.

```json
{"kind": "rentRoll",              // rentRoll | t12 | om | other
 "filename": "RR 6-11-26.xlsx",
 "content_b64": "UEsDBBQA...",    // REQUIRED, standard base64
 "source_item_id": "016NAR27MPUG...",
 "source_modified": "2026-06-11T12:30:01Z"}
```

```json
{"ok": true, "action": "stored", "id": 7, "filename": "RR 6-11-26.xlsx",
 "rel_path": "016.../rentRoll/RR 6-11-26.xlsx",
 "sha256": "9f86d0...", "bytes": 39171}
```

`action`: `stored` | `replaced` | `unchanged` (identical SHA-256 already on
disk, so nothing transfers).

Writes to `{EIGHT_ROCK_DOCS_ROOT}/{deal_key}/{kind}/{filename}`, atomically —
a partial file is never visible. Filenames are sanitised and the resolved path
is proven inside the docs root before any write.

| Failure | Code |
|---|---|
| `content_b64` isn't valid base64, or empty | 400 |
| Over 75 MB (`EIGHT_ROCK_MAX_DOC_BYTES`) | 413 |
| Extension outside xlsx, xls, xlsm, csv, pdf, docx, doc, txt, json | 415 |

---

### `POST /ingest/deal/{deal_key}/document/register`

Record a file you already copied into the docs root yourself — no bytes over
the wire. This is what `doc_sync.py` calls after each local copy.

```json
{"kind": "rentRoll", "filename": "RR 6-11-26.xlsx",
 "rel_path": "016.../rentRoll/RR 6-11-26.xlsx",
 "size_bytes": 39171, "sha256": "9f86d0...",   // all REQUIRED, sha256 exactly 64 hex
 "source_item_id": null, "source_modified": null}
```

`action`: `registered` | `replaced` | `unchanged`.

The API verifies before accepting: no file at that path → `404`; size on disk
differs from `size_bytes` → `409`, which catches an interrupted copy instead of
indexing a truncated file.

---

### `GET /ingest/deal/{deal_key}/documents`

```json
{"ok": true, "deal_key": "016...", "count": 3, "documents": [
  {"id": 7, "kind": "rentRoll", "filename": "RR 6-11-26.xlsx",
   "rel_path": "016.../rentRoll/RR 6-11-26.xlsx", "size_bytes": 39171,
   "sha256": "9f86d0...", "source_item_id": "016NAR27MPUG...",
   "source_modified": "2026-06-11T12:30:01Z", "stored_at_utc": "2026-08-28T16:02:11Z"}
]}
```

Join `rel_path` to `EIGHT_ROCK_DOCS_ROOT` to open the file, or use
`api.document_path(deal_key, filename)`.

---

### `GET /ingest/documents/manifest`

Every stored hash in one call — use it to decide what needs transferring
without N round trips.

```json
{"ok": true, "deal_count": 21, "document_count": 58,
 "manifest": {"016...": [{"kind": "rentRoll", "filename": "RR 6-11-26.xlsx",
                          "sha256": "9f86d0...", "size_bytes": 39171}]}}
```

---

### `POST /ingest/digest` · `GET /ingest/digest`

Sweep run reports, for a change feed in the app.

```jsonc
{"run_id": "sweep-2026-08-28",        // REQUIRED, unique — re-posting updates in place
 "ran_at_utc": "2026-08-28T16:00:00Z", // REQUIRED
 "had_changes": true,                  // REQUIRED
 "summary": "2 new deals, 5 documents",// REQUIRED
 "new_deals": ["River's Edge-56u"], "updated_deals": [],
 "documents_transferred": 5, "warnings": [], "detail_html": null}
```

`GET` takes `limit` (default 30, max 200) and `only_changes` (skip quiet days),
newest first, each run carrying its full `payload`.

---

## Metrics

Every field optional. **A field the sweep could not establish is omitted, never
sent as `0`.** Treat missing as "not established" in any calculation.

| Field | Type |
|---|---|
| `units` | int |
| `occupancy_pct` | float, 0–100 |
| `gross_potential_rent` | float |
| `other_income` | float |
| `t12_total_expenses` | float |
| `t12_noi` | float |
| `asking_price` | float |
| `price_per_unit` | float |
| `avg_in_place_rent` | float |
| `avg_unit_sqft` | float |
| `year_built` | int |
| `implied_cap_rate` | float |
| `as_of` | string |

## SourceDoc

`kind` (`rentRoll` \| `t12` \| `om` \| `other`) and `name` are required;
`item_id`, `modified_utc`, `size_bytes`, `folder_path` optional.

---

## Tables

Three, all created by this service. Nothing else in `workbench.db` is read or
written. Query them directly if that's easier than HTTP.

**`deal_sweep_inbox`** — `id`, `deal_key` (unique), `deal_name`, `state`,
`city`, `units`, `sharepoint_url`, `completeness`, `doc_hash`, `payload_json`,
`source`, `first_seen_utc`, `last_seen_utc`, `revision`, `status`,
`merged_at_utc`. Indexed on `status`, `last_seen_utc`, `state`.

**`deal_sweep_docs`** — `id`, `deal_key`, `kind`, `filename`, `rel_path`,
`abs_path`, `size_bytes`, `sha256`, `source_item_id`, `source_modified`,
`stored_at_utc`. Unique on `(deal_key, kind, filename)`; indexed on `deal_key`,
`sha256`.

**`deal_sweep_runs`** — `id`, `run_id` (unique), `ran_at_utc`, `had_changes`,
`summary`, `payload_json`, `read_at_utc`. Indexed on `ran_at_utc`.

```sql
-- deals ready to underwrite, with their document count
SELECT i.deal_name, i.state, i.units, COUNT(d.id) AS docs
FROM   deal_sweep_inbox i
LEFT   JOIN deal_sweep_docs d ON d.deal_key = i.deal_key
WHERE  i.completeness = 'complete'
GROUP  BY i.id
ORDER  BY i.last_seen_utc DESC;

-- one deal's metrics straight out of the payload
SELECT json_extract(payload_json, '$.metrics.units')  AS units,
       json_extract(payload_json, '$.metrics.t12_noi') AS noi
FROM   deal_sweep_inbox WHERE deal_key = ?;
```

---

## Status codes

| Code | Meaning |
|---|---|
| 200 | OK |
| 400 | Malformed base64, empty document, path escapes the docs root |
| 401 | Bad or missing `X-Ingest-Token` |
| 404 | Unknown inbox row, or register called with no file on disk |
| 409 | Registered size doesn't match the file on disk |
| 413 | Body over 80 MB, or document over 75 MB |
| 415 | Document extension not allowed |
| 422 | Payload failed validation — detail lists the offending fields |

## Environment

| Variable | Default | Used by |
|---|---|---|
| `EIGHT_ROCK_INGEST_TOKEN` | — required, ≥24 chars | service + clients |
| `EIGHT_ROCK_INGEST_URL` | `http://127.0.0.1:8601` | clients |
| `EIGHT_ROCK_DB_PATH` | `data/workbench.db` | service |
| `EIGHT_ROCK_DOCS_ROOT` | `data/deal_docs` | service, `doc_sync.py` |
| `EIGHT_ROCK_DEALS_LOCAL_ROOT` | — | `doc_sync.py` |
| `EIGHT_ROCK_INGEST_HOST` / `_PORT` | `127.0.0.1` / `8601` | service |
| `EIGHT_ROCK_MAX_DOC_BYTES` | 78643200 | service |

The service exits at startup if the token is missing or under 24 characters.
