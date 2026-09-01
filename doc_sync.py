"""
Eight Rock Workbench — copy deal documents onto the server.

Copies the real rent roll / T-12 / OM files out of the synced SharePoint
03-Deals folder into data\\deal_docs\\<deal>\\, so they open from the property
record without going back to SharePoint.

Run it from run-docsync.bat. Nothing to type.

    run-docsync.bat            copy anything new
    run-docsync.bat /preview   show what would be copied, change nothing

Settings come from .env:
    EIGHT_ROCK_INGEST_URL        the API (default http://127.0.0.1:8600)
    EIGHT_ROCK_INGEST_TOKEN      the password
    EIGHT_ROCK_DOCS_ROOT         where copies land (default data/deal_docs)
    EIGHT_ROCK_DEALS_LOCAL_ROOT  the synced 03-Deals folder on this machine

setup-docsync.bat finds that last one for you and writes it in.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from ingest_client import IngestClient, IngestError  # also loads .env
except ImportError as exc:
    sys.exit(f"Cannot load ingest_client.py from {HERE}: {exc}")

DOCS_ROOT = Path(os.environ.get("EIGHT_ROCK_DOCS_ROOT", str(HERE / "data" / "deal_docs")))
if not DOCS_ROOT.is_absolute():
    DOCS_ROOT = HERE / DOCS_ROOT
DEALS_ROOT = os.environ.get("EIGHT_ROCK_DEALS_LOCAL_ROOT", "").strip().strip('"')

KIND_DIR = {"rentRoll": "rentRoll", "t12": "t12", "om": "om", "other": "other"}
KIND_LABEL = {"rentRoll": "rent roll", "t12": "T-12", "om": "OM", "other": "other"}


def die(msg: str) -> None:
    print(f"\n  STOPPED: {msg}\n")
    sys.exit(1)


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def build_index(root: Path) -> dict[str, list[Path]]:
    """One pass over the deals tree, filenames mapped to where they live.

    Walking 03-Deals once and reusing the index beats searching per file —
    that tree holds tens of thousands of files.
    """
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            index.setdefault(path.name.lower(), []).append(path)
    return index


def pick(candidates: list[Path], deal_name: str) -> Path | None:
    """Choose the copy that sits under this deal's own folder.

    Two properties can hold files with identical names, so never take a match
    from a different deal's folder.
    """
    if not candidates:
        return None
    want = deal_name.lower()
    for p in candidates:
        if any(part.lower() == want for part in p.parts):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--preview", "--dry-run", action="store_true", dest="preview")
    ap.add_argument("--deal", metavar="TEXT")
    args, _ = ap.parse_known_args()

    print("\n  Eight Rock - copying deal documents\n")

    if not os.environ.get("EIGHT_ROCK_INGEST_TOKEN", "").strip():
        die("no password found. Run setup-ingest.bat first.")
    if not DEALS_ROOT:
        die("I do not know where your 03-Deals folder is.\n"
            "           Run setup-docsync.bat first - it finds it for you.")

    root = Path(DEALS_ROOT)
    if not root.exists():
        die(f"this folder does not exist:\n           {root}\n"
            "           Run setup-docsync.bat again to point at the right one.")

    api = IngestClient.from_env()
    if not api.is_up():
        die("the Workbench API is not answering.\n"
            "           Double-click run-api.bat, then try again.")

    print(f"  Deals folder : {root}")
    print(f"  Copies go to : {DOCS_ROOT}")
    print("\n  Reading the deal list...")
    deals = api.deals("all", limit=500)
    if args.deal:
        deals = [d for d in deals if args.deal.lower() in d["deal_name"].lower()]
    wanted = [(d, doc) for d in deals for doc in d.documents if doc.get("name")]
    print(f"  {len(deals)} deals, {len(wanted)} documents to account for.")

    print("  Checking what is already copied...")
    have = {(k, doc["filename"]): doc["sha256"]
            for k, docs in api.manifest().items() for doc in docs}

    print(f"  Indexing {root.name}... (one pass, may take a minute)")
    index = build_index(root)
    print(f"  {len(index):,} distinct filenames found.\n")

    copied = skipped = missing = failed = 0
    misses: list[str] = []

    for deal, doc in wanted:
        name, kind = doc["name"], doc.get("kind", "other")
        src = pick(index.get(name.lower(), []), deal["deal_name"])
        if src is None:
            missing += 1
            misses.append(f"{deal['deal_name']}  ::  {name}")
            continue

        try:
            digest = sha256_of(src)
        except OSError as exc:
            failed += 1
            print(f"  could not read  {name}  ({exc})")
            continue

        if have.get((deal["deal_key"], name)) == digest:
            skipped += 1
            continue

        rel = f"{deal['deal_key']}/{KIND_DIR.get(kind, 'other')}/{name}"
        dest = DOCS_ROOT / rel

        if args.preview:
            print(f"  would copy   {deal['deal_name'][:38]:<38} {KIND_LABEL.get(kind, kind)}")
            copied += 1
            continue

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            shutil.copy2(src, tmp)
            tmp.replace(dest)          # atomic - never a half-written file
            api.register_document(deal["deal_key"], dest, kind, rel)
        except (OSError, IngestError) as exc:
            failed += 1
            print(f"  FAILED       {name}  ({exc})")
            continue

        copied += 1
        print(f"  copied       {deal['deal_name'][:38]:<38} "
              f"{KIND_LABEL.get(kind, kind):<10} {dest.stat().st_size:>12,} bytes")

    verb = "Would copy" if args.preview else "Copied"
    print(f"\n  {verb}: {copied}    already up to date: {skipped}    "
          f"not found: {missing}    failed: {failed}")

    if misses:
        print("\n  Not found on disk. Either the file was renamed, or that part")
        print("  of the SharePoint folder is not synced to this machine:")
        for m in misses[:20]:
            print(f"    - {m}")
        if len(misses) > 20:
            print(f"    ... and {len(misses) - 20} more")

    print(f"\n  Documents live in: {DOCS_ROOT}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
