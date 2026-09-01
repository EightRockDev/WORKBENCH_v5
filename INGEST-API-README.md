# Eight Rock Workbench — Deal Ingest

Puts your SharePoint `03-Deals` pipeline into the Workbench. A sweep runs at
noon ET, finds what's new or changed, and pushes deal records in. A second
script copies the rent rolls, T-12s and OMs onto disk so they open from the
property record.

**Status:** built and tested — 33 tests plus two live end-to-end runs. Not yet
deployed; that's the four steps below.

---

## How it works

| Step | Runs where | What it does |
|---|---|---|
| Sweep | Cloud, noon ET | Scans 03-Deals, finds new/changed deals, reads the numbers out of the RR / T-12 / OM, pushes deal records through the API. Reports to you in Claude each day. |
| `ingest_api.py` | Your machine, port 8601 | Receives everything. The only thing that writes to the Workbench. |
| `doc_sync.py` | Your machine | Copies the actual document files into `data\deal_docs\<deal>\`. |

The sweep reads SharePoint and writes nothing there. Every write goes through
the API.

**Why documents are copied locally:** the cloud sweep can read what's *inside*
your files, but it can't hand you the file itself. Your machine already has the
originals through OneDrive sync, so `doc_sync.py` copies them across on your
side — exact, instant, no size limit. Each copy is then recorded through the
API so the next run skips it.

---

## The safety contract

`workbench.db` is ~6.5 GB of live data, so this service is **additive only**:

| Guarantee | How it's enforced |
|---|---|
| Creates only its own tables (`deal_sweep_inbox`, `deal_sweep_docs`, `deal_sweep_runs`) | `CREATE TABLE IF NOT EXISTS`, nothing else |
| Never reads, writes, alters or drops an existing table | No SQL names any other table — grep it |
| Never changes `journal_mode` or `synchronous` | Only session-scoped `busy_timeout` and `foreign_keys` |
| Never blocks Streamlit | 5s `busy_timeout`, short transactions |
| Documents can't escape their folder | Path components sanitised, resolved path proven inside the docs root |
| Nothing auto-merges into your real tables | Rows land `pending`; you promote them |

Six tests exist purely to prove those rows, including four path-traversal
attacks and a check that a half-finished copy is rejected.

---

## Deploy — four steps

**1. Copy five files** into `...\8-ROCK-WORKBNCH\python_workbench\`:

```
ingest_api.py       test_ingest_api.py       doc_sync.py
ingest_api.bat      INGEST-API-README.md
```

**2. Generate a token and fill in `.env`:**

```bat
uv run python -c "import secrets;print(secrets.token_urlsafe(32))"
```

Append to `python_workbench\.env`:

```
EIGHT_ROCK_INGEST_TOKEN=<the value you just generated>
EIGHT_ROCK_DB_PATH=data/workbench.db
EIGHT_ROCK_DOCS_ROOT=data/deal_docs
EIGHT_ROCK_INGEST_URL=http://127.0.0.1:8601
EIGHT_ROCK_DEALS_LOCAL_ROOT=C:\Users\bmccu\Eight Rock Capital Partners\Eight Rock Capital Partners - Documents\03-Deals
```

That last path is your synced `03-Deals` folder — where `doc_sync.py` copies
from. If it isn't synced to this machine, open the library in SharePoint, click
**Sync**, and wait for the folders to appear.

The service refuses to start if the token is missing or under 24 characters.

**3. Start it:**

```bat
uv add fastapi uvicorn httpx
ingest_api.bat
```

Check <http://127.0.0.1:8601/healthz> — expect `{"ok": true, ...}`.

**4. Send me the URL and token.** Point your `workbench.eight-rock.com` tunnel
at port 8601 for `/ingest` and `/healthz`, or give it its own hostname. Once I
have those the noon sweep pushes live.

Until then the sweep still runs and reports what it found in Claude each day.

---

## Copying documents

```bat
uv run python doc_sync.py --dry-run     :: see what would move
uv run python doc_sync.py               :: copy it
uv run python doc_sync.py --deal River  :: one deal only
```

Files land at:

```
data\deal_docs\<deal_key>\rentRoll\Rivers Edge RR 6-11-26.xlsx
data\deal_docs\<deal_key>\t12\...
data\deal_docs\<deal_key>\om\...
```

Matches the deal folder first, so two properties with same-named files never
mix. Copies are atomic. Unchanged files are skipped on later runs.

**Schedule it** after the sweep via Task Scheduler:

```
Program:   cmd.exe
Arguments: /c cd /d "<path>\python_workbench" && uv run python doc_sync.py
Trigger:   Daily 12:15 PM
```

---

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/healthz` | none | Liveness + counts. Returns no deal content. |
| `POST` | `/ingest/deal` | token | Upsert one deal |
| `POST` | `/ingest/batch` | token | Upsert up to 500 deals, one transaction |
| `GET` | `/ingest/inbox` | token | Queued deals |
| `POST` | `/ingest/inbox/{id}/status` | token | Mark `pending` / `merged` / `ignored` |
| `POST` | `/ingest/deal/{key}/document` | token | Store a document sent as bytes |
| `POST` | `/ingest/deal/{key}/document/register` | token | Record one `doc_sync.py` copied in |
| `GET` | `/ingest/deal/{key}/documents` | token | What's stored for a deal |
| `GET` | `/ingest/documents/manifest` | token | Every stored hash, one call |
| `POST` / `GET` | `/ingest/digest` | token | Run reports, for a change feed in the app |
| `GET` | `/ingest/docs` | none | Interactive API docs |

Auth is the header `X-Ingest-Token`, compared in constant time.

### Deal payload

```jsonc
{
  "deal_key": "016NAR27PFCB...",        // SharePoint folder id — survives renames
  "deal_name": "River's Edge-56u-Elizabeth City",
  "state": "NC", "city": "Elizabeth City",
  "completeness": "complete",            // complete | partial | none
  "doc_hash": "abc123def456",            // drives idempotency
  "metrics": {
    "units": 56, "occupancy_pct": 100.0,
    "gross_potential_rent": 696648, "avg_unit_sqft": 1122
  },
  "documents": [
    {"kind": "rentRoll", "name": "Rivers Edge RR 6-11-26.xlsx",
     "item_id": "016NAR27MPUG...", "modified_utc": "2026-06-11T12:30:01Z"}
  ]
}
```

Every `metrics` field is optional. The sweep sends what it read with confidence
and leaves the rest out — **a missing field means "not established", never zero.**

### Idempotency

`deal_key` is unique; `doc_hash` decides what happens on re-push:

- **hash matches** → `unchanged`, only the last-seen timestamp moves. A deal you
  already merged stays merged.
- **hash differs** → `updated`, revision increments, and a merged deal reopens
  as `pending` because its documents actually changed.

Documents work the same way on SHA-256, so a daily run never re-copies the same
files.

---

## Reviewing what arrives

```sql
SELECT deal_name, state, units, completeness, revision, last_seen_utc
FROM   deal_sweep_inbox WHERE status = 'pending'
ORDER  BY last_seen_utc DESC;

SELECT deal_key, kind, filename, size_bytes, rel_path
FROM   deal_sweep_docs ORDER BY stored_at_utc DESC LIMIT 20;
```

Promote a row once you've imported it:

```sql
UPDATE deal_sweep_inbox SET status='merged', merged_at_utc=datetime('now')
WHERE id = ?;
```

Next build if you want it: a Workbench tab showing pending deals, their attached
documents, and an "Import into property" button. Needs a look at your property
schema.

---

## Tests

```bat
uv run python -m pytest test_ingest_api.py -v
```

33 tests: the safety contract, auth on every path, idempotent replay, batch
atomicity, payload round-trip, validation, document round-trip with byte
verification, four path-traversal attacks, incomplete-copy detection, and the
change feed.

---

## Operational notes

- **Port 8601.** Streamlit stays on 8501.
- **Loopback by default.** The tunnel is the only public surface.
- **Rotating the token:** change `.env`, restart, tell me the new value.
- **If the API is down at noon**, the sweep reports in Claude and the next run
  catches up.
