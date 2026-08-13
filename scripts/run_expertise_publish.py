"""Autopilot step: publish generated markdown into the SharePoint library.

Owner directive 2026-08-13: "I'd like to have them written to a new spot ...
on SharePoint, not OneDrive" -> the 06-Expertise folder of the Eight Rock
Capital Partners document library. That library is already synced to this
host by OneDrive, so a plain file copy into the synced path IS the delivery
mechanism: no Graph write scope, no device bridge, works on every scheduled
cycle. (Graph write was denied - the M365 MCP Server app is consented
read-only; see reports/expertise-latest.txt if that ever changes.)

Drop markdown into data/expertise_out/<bucket>/ where bucket is one of
knowledge-base | deal-analysis | daily-digests | working-notes; loose files
at the root default to working-notes. Published files move to
data/expertise_out/published/ so a re-run never re-copies. Same drop-folder
pattern as core/inbox/kb_drop.py - any lane that can land a file on this
host (zip handoff, git pull, a local script) feeds it for free.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DROP = pathlib.Path(os.environ.get("ER_EXPERTISE_DROP",
                                   ROOT / "data" / "expertise_out"))
DEST = pathlib.Path(os.environ.get(
    "ER_EXPERTISE_SHAREPOINT",
    pathlib.Path.home() / "Eight Rock Capital Partners"
    / "Eight Rock Capital Partners - Documents" / "06-Expertise"))
BUCKETS = {"knowledge-base": "Knowledge-Base",
           "deal-analysis": "Deal-Analysis",
           "daily-digests": "Daily-Digests",
           "working-notes": "Working-Notes"}
DEFAULT_BUCKET = "Working-Notes"


def main() -> int:
    if not DEST.exists():
        print(f"[expertise] sync folder missing: {DEST} - nothing published "
              "(is OneDrive signed in and the library synced?)")
        return 0
    DROP.mkdir(parents=True, exist_ok=True)
    done = DROP / "published"
    done.mkdir(exist_ok=True)

    published = unchanged = 0
    for src in sorted(DROP.rglob("*.md")):
        if done in src.parents or not src.is_file():
            continue
        rel = src.relative_to(DROP)
        bucket = (BUCKETS.get(rel.parts[0].lower(), DEFAULT_BUCKET)
                  if len(rel.parts) > 1 else DEFAULT_BUCKET)
        out_dir = DEST / bucket
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / src.name
        if target.exists() and target.read_bytes() == src.read_bytes():
            unchanged += 1
        else:
            shutil.copy2(src, target)
            published += 1
            print(f"[expertise] -> {bucket}\\{src.name}")
        archived = done / rel
        archived.parent.mkdir(parents=True, exist_ok=True)
        if archived.exists():
            archived.unlink()
        shutil.move(str(src), str(archived))

    print(f"[expertise] {DEST}: {published} published, {unchanged} unchanged, "
          f"drop={DROP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
