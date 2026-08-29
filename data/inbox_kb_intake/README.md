# KB intake lane (git-delivered)

Cloud Claude sessions COMMIT one JSON per deal email here. The autopilot's
`update` step pulls them and the `inboxsync` step ingests them via
`kb_drop.ingest_git_intake()` — no folder grant, no zip, no owner click.

Shape per file (same as the `data/inbox_kb/` drop folder):

    {"external_id": "...", "from_email": "...", "from_name": "...",
     "subject": "...", "received_at": "2026-08-29T14:00:00+00:00",
     "body": "...plain text...",
     "fields": {"name","address","city","state","units",
                "asking_price","cap_rate"},   # any subset
     "confidence": 0.9}

Formats: `units` int, `asking_price` plain float dollars, `cap_rate` a
DECIMAL FRACTION (6.7% -> 0.067, never 6.7).

Files here are TRACKED and are never moved or deleted by the sweep — the
updater's force-checkout would resurrect them. Idempotency is a content-hash
ledger at `data/inbox_kb/.intake_seen.json` (untracked), so re-pulling
identical files is a no-op and an edited file re-ingests.
