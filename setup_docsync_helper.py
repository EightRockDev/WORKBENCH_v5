"""Finds the synced 03-Deals folder and writes it into .env.

Called by setup-docsync.bat. Not meant to be run by hand.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"
KEY = "EIGHT_ROCK_DEALS_LOCAL_ROOT"

# The state folders are the fingerprint of the real 03-Deals tree.
STATE_HINTS = ("@-VA", "@ - NORTH CAROLINA", "@ - GEORGIA", "@ - TEXAS")


def looks_right(path: Path) -> bool:
    try:
        names = {c.name for c in path.iterdir() if c.is_dir()}
    except OSError:
        return False
    return any(h in names for h in STATE_HINTS)


def score(path: Path) -> tuple[int, int]:
    """Prefer a folder with more of the expected state subfolders."""
    try:
        names = {c.name for c in path.iterdir() if c.is_dir()}
    except OSError:
        return (0, 0)
    return (sum(1 for h in STATE_HINTS if h in names), len(names))


def candidate_roots() -> list[Path]:
    roots = []
    home = Path.home()
    for base in (home, Path("C:/Users"), Path("C:/")):
        if base.exists():
            roots.append(base)
    return roots


def find() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    # OneDrive/SharePoint sync folders live directly under the profile.
    home = Path.home()
    quick = []
    try:
        for entry in home.iterdir():
            if entry.is_dir() and ("eight rock" in entry.name.lower()
                                   or "onedrive" in entry.name.lower()
                                   or "sharepoint" in entry.name.lower()):
                quick.append(entry)
    except OSError:
        pass

    for base in quick:
        for depth in ("03-Deals", "*/03-Deals", "*/*/03-Deals"):
            for hit in base.glob(depth):
                if hit.is_dir() and str(hit).lower() not in seen and looks_right(hit):
                    seen.add(str(hit).lower())
                    found.append(hit)

    if found:
        return found

    # Wider sweep, bounded so it cannot crawl the whole disk.
    for base in candidate_roots():
        try:
            for hit in base.glob("*/03-Deals"):
                if hit.is_dir() and str(hit).lower() not in seen and looks_right(hit):
                    seen.add(str(hit).lower()); found.append(hit)
            for hit in base.glob("*/*/03-Deals"):
                if hit.is_dir() and str(hit).lower() not in seen and looks_right(hit):
                    seen.add(str(hit).lower()); found.append(hit)
            for hit in base.glob("*/*/*/03-Deals"):
                if hit.is_dir() and str(hit).lower() not in seen and looks_right(hit):
                    seen.add(str(hit).lower()); found.append(hit)
        except OSError:
            continue
    return found


def write_env(path: Path) -> None:
    text = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(f"{KEY}=")]
    lines.append(f"{KEY}={path}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    existing = os.environ.get(KEY, "").strip().strip('"')
    if existing and Path(existing).exists() and looks_right(Path(existing)):
        print(f"        Already set, and it looks right:")
        print(f"          {existing}")
        return 0

    print("        Searching for your synced 03-Deals folder...")
    hits = find()

    if not hits:
        print()
        print("        COULD NOT FIND IT.")
        print()
        print("        The 03-Deals folder from SharePoint does not appear to be")
        print("        synced to this machine. To fix that:")
        print("          1. Open the Eight Rock document library in SharePoint")
        print("          2. Click Sync")
        print("          3. Wait for the folders to appear on this PC")
        print("          4. Run this again")
        print()
        print("        If it IS synced and I just missed it, tell Claude the")
        print("        folder path and it will set it directly.")
        return 1

    hits.sort(key=score, reverse=True)
    best = hits[0]

    if len(hits) > 1:
        print(f"        Found {len(hits)}. Using the one with the most deal folders:")
        for h in hits:
            n, total = score(h)
            mark = "->" if h == best else "  "
            print(f"          {mark} {h}   ({n} state folders, {total} subfolders)")
    else:
        print(f"        Found it:")
        print(f"          {best}")

    try:
        deal_count = sum(1 for state in best.iterdir() if state.is_dir()
                         for _ in state.iterdir())
        print(f"        Contains roughly {deal_count} deal folders.")
    except OSError:
        pass

    write_env(best)
    print(f"        Saved to .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
