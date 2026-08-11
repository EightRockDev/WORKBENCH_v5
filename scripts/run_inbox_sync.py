"""Autopilot step: sync every connected O365/Gmail mailbox (owner directive
2026-08-11: "you should be reading emails from O365 ... and populating
property details from them").

Module D only ran when a user clicked "Sync inbox" - mail sat unread between
visits. This step polls each connected mailbox every cycle through the SAME
user-scoped path the button uses (per-user OAuth token, RLS-private raw
mail), so classification, deal upserts and property-intel writes happen
hands-free. Idempotent by design: re-ingesting a seen message updates in
place (ON CONFLICT on (org, owner, provider, external_id)).

Enumeration comes from the connected_mailboxes() SECURITY DEFINER function
(metadata only - no tokens); a host whose schema predates it reports that
one migration is needed instead of failing.

Fail-silent per mailbox: one expired token never blocks the other users'
sync. dotenv is loaded so the encryption key + app id reach the token layer
exactly as they do under the app service.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

from data import pg  # noqa: E402


def main() -> int:
    # KB drop folder FIRST (owner 2026-08-11: the Cowork outlook-connector
    # is the mail source - "here's how you pull from inbox into the
    # workbench"). Works with or without Postgres; property data always
    # lands. Mailbox OAuth sync below stays as the secondary path.
    from core.inbox import kb_drop
    kb = kb_drop.ingest_dir()
    if kb.files or kb.notes:
        print(f"[inbox-sync] KB drop ({kb_drop.kb_dir()}): {kb.files} "
              f"file(s), {kb.records} record(s), {kb.ingested} ingested, "
              f"{kb.linked} linked to deals/properties, {kb.failed} failed")
        for n in kb.notes[:10]:
            print(f"[inbox-sync]   note: {n}")
    else:
        print(f"[inbox-sync] KB drop ({kb_drop.kb_dir()}): empty - "
              "point the Cowork outlook-connector at this folder "
              "(one JSON per ingested email; see core/inbox/kb_drop.py "
              "docstring for the accepted shape)")

    if not pg.is_reachable():
        print("inbox-sync: Postgres not reachable - mailbox sync skipped")
        return 0
    try:
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM connected_mailboxes()")
            boxes = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        print("inbox-sync: connected_mailboxes() unavailable - run "
              f"deploy\\windows\\migrate-db.ps1 once to add it ({exc})")
        return 0
    if not boxes:
        print("inbox-sync: no connected mailboxes (connect one in the app: "
              "Inbox tab -> Connect Microsoft 365)")
        return 0

    from core import inbox
    total_new = total_applied = total_queued = 0
    for b in boxes:
        org, user = str(b["org_id"]), str(b["user_id"])
        try:
            results = inbox.sync_inbox(org, user, limit=50)
        except Exception as exc:
            print(f"[inbox-sync] {user[:8]}… ({b.get('provider')}): "
                  f"sync failed ({exc})")
            continue
        applied = sum(1 for r in results if r.status == "auto_applied")
        queued = sum(1 for r in results if r.status == "queued")
        total_new += len(results)
        total_applied += applied
        total_queued += queued
        print(f"[inbox-sync] {user[:8]}… ({b.get('provider')}): "
              f"{len(results)} message(s), {applied} auto-applied, "
              f"{queued} queued for confirm")
    print(f"[inbox-sync] done: {len(boxes)} mailbox(es), {total_new} "
          f"message(s), {total_applied} applied, {total_queued} queued")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
