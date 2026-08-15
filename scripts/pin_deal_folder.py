"""Find the Properties folder that actually holds the deals, and pin it.

Owner, 2026-08-15. The deal folders live in the OneDrive/SharePoint sync
root - `C:\\Users\\<user>\\<Org>\\<Org> - Documents\\Properties` - which no
rule based on the app's own install location can reach. Auto-discovery now
looks there, but a pinned setting is proof against the org folder being
renamed, a second sync copy appearing, or the app moving again.

Picks by EVIDENCE, not by guessing: whichever candidate holds the most
`sales.json` files wins, because that file is the hand-verified sale history
this whole exercise is about. Reports what it found before changing anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MAX_FOLDERS_SCANNED = 4000


def _candidates() -> list[Path]:
    home = Path.home()
    app_root = Path(__file__).resolve().parent.parent
    out: list[Path] = [
        app_root.parent / "Properties",
        app_root / "Properties",
    ]
    for pat in ("*/* - Documents/Properties", "OneDrive*/Properties",
                "*/Properties", "*/*/Properties"):
        try:
            out += sorted(home.glob(pat))
        except OSError:
            pass
    out.append(home / "Properties")
    seen: set[Path] = set()
    uniq = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _score(root: Path) -> tuple[int, int]:
    """(folders with sales.json, total deal folders) for one candidate."""
    if not root.is_dir():
        return (0, 0)
    deals = withs = 0
    try:
        for i, child in enumerate(root.iterdir()):
            if i > MAX_FOLDERS_SCANNED:
                break
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            deals += 1
            if (child / "sales.json").is_file():
                withs += 1
    except OSError:
        return (0, 0)
    return (withs, deals)


def main() -> int:
    print("=" * 64)
    print("FIND THE FOLDER THAT HOLDS YOUR SAVED SALE HISTORY")
    print("=" * 64)

    best: tuple[int, int, Path] | None = None
    print()
    for cand in _candidates():
        withs, deals = _score(cand)
        if deals:
            print(f"  {withs:>4} with sale history / {deals:>4} deals   {cand}")
        if deals and (best is None or withs > best[0]
                      or (withs == best[0] and deals > best[1])):
            best = (withs, deals, cand)

    if best is None:
        print("\n  Found no Properties folder with deal folders in it.")
        print("  Search the machine for sales.json and set")
        print("  ER_PROPERTIES_ROOT to the folder ABOVE the property folders.")
        return 1

    withs, deals, root = best
    print(f"\n  Best: {root}")
    print(f"        {deals} deal folders, {withs} with saved sale history")
    if not withs:
        print("\n  WARNING: none of these carry a sales.json, so the sale")
        print("  card will still fall back to county records. Pinning anyway")
        print("  so the rest of the deal data resolves.")

    current = os.environ.get("ER_PROPERTIES_ROOT", "").strip()
    if current == str(root):
        print("\n  Already pinned to this folder - nothing to change.")
        return 0

    if os.name != "nt":
        print(f"\n  (non-Windows) set ER_PROPERTIES_ROOT={root}")
        return 0
    try:
        subprocess.run(["setx", "ER_PROPERTIES_ROOT", str(root)],
                       check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"\n  Could not pin it automatically ({e}).")
        print(f"  Set ER_PROPERTIES_ROOT to: {root}")
        return 1

    print("\n  PINNED. ER_PROPERTIES_ROOT is now set for your account.")
    print("  Close the app completely and start it again - a running app")
    print("  keeps the old value, and so does any window open right now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
