"""Move the deal folders into the application folder, and pin them there.

Owner decision, 2026-08-15: deal folders belong in `C:\\WORKBENCH_V5`
alongside the rest of the application data. I argued for leaving them in the
OneDrive/SharePoint sync root; the owner reaffirmed, so this implements the
move properly rather than half-way.

What "properly" means here:
  * COPY, never move. The source stays untouched until the owner has seen the
    verification counts and deleted it deliberately. A move that half-fails
    takes the only copy of hand-verified sale history with it.
  * VERIFY by counting. Deal folders and `sales.json` files are counted on
    both sides afterwards and compared; a mismatch is reported as a failure,
    not a warning buried in output.
  * PIN the result, so the app stops inferring the location from its own
    install path - the exact assumption that broke this in the first place.

Two consequences of living inside the app folder, stated plainly because they
are real and the owner should know them rather than discover them:
  * No more sync or version history. SharePoint was providing backup for
    free; a local folder has none. Back it up somewhere.
  * `.gitignore` must exclude it, or deal data (owners, financials, notes)
    would be committed to the GitHub repo on the next publish. This script
    adds that entry.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

APP_ROOT = Path(__file__).resolve().parent.parent
TARGET = APP_ROOT / "Properties"


def _count(root: Path) -> tuple[int, int]:
    """(deal folders, folders carrying sales.json)."""
    if not root.is_dir():
        return (0, 0)
    deals = withs = 0
    try:
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            deals += 1
            if (child / "sales.json").is_file():
                withs += 1
    except OSError:
        pass
    return (deals, withs)


def _find_source() -> Path | None:
    """The candidate holding the most saved sale history - by evidence."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pin_deal_folder import _candidates
    best: tuple[int, int, Path] | None = None
    for cand in _candidates():
        if cand.resolve() == TARGET.resolve():
            continue
        deals, withs = _count(cand)
        if deals and (best is None or withs > best[1]
                      or (withs == best[1] and deals > best[0])):
            best = (deals, withs, cand)
    return best[2] if best else None


def _ensure_gitignored() -> bool:
    gi = APP_ROOT / ".gitignore"
    try:
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    except OSError:
        return False
    if any(l.strip() in ("/Properties/", "Properties/", "/Properties")
           for l in text.splitlines()):
        return True
    try:
        with gi.open("a", encoding="utf-8") as fh:
            fh.write("\n# Deal folders live here now (owner, 2026-08-15).\n"
                     "# Never commit them: they hold owner names, financials\n"
                     "# and notes, and this repo is pushed to GitHub.\n"
                     "/Properties/\n")
        return True
    except OSError:
        return False


def main() -> int:
    print("=" * 64)
    print("MOVE DEAL FOLDERS INTO THE APPLICATION FOLDER")
    print("=" * 64)

    src = _find_source()
    if src is None:
        already = _count(TARGET)
        if already[0]:
            print(f"\n  Deal folders are already here: {TARGET}")
            print(f"  {already[0]} deals, {already[1]} with saved sale history")
        else:
            print("\n  Could not find any folder holding deal folders.")
            print("  Nothing copied.")
            return 1
    else:
        s_deals, s_withs = _count(src)
        print(f"\n  From: {src}")
        print(f"        {s_deals} deals, {s_withs} with saved sale history")
        print(f"    To: {TARGET}")

        try:
            TARGET.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, TARGET, dirs_exist_ok=True)
        except (OSError, shutil.Error) as e:
            print(f"\n  COPY FAILED: {e}")
            print("  Nothing was removed - the original is untouched.")
            return 1

        t_deals, t_withs = _count(TARGET)
        print(f"\n  Copied. Now at destination: {t_deals} deals, "
              f"{t_withs} with saved sale history")
        if t_deals < s_deals or t_withs < s_withs:
            print("\n  !! FEWER items arrived than were sent. Do NOT delete")
            print("     the original. Re-run, or copy the remainder by hand.")
            return 1
        print("\n  Counts match. The ORIGINAL IS STILL THERE and untouched -")
        print("  delete it yourself once you have seen the app working.")

    if _ensure_gitignored():
        print("\n  .gitignore updated - deal data will not be committed.")
    else:
        print("\n  !! Could not update .gitignore. Add a line '/Properties/'")
        print("     before the next push, or deal data goes to GitHub.")

    if os.name == "nt":
        try:
            subprocess.run(["setx", "ER_PROPERTIES_ROOT", str(TARGET)],
                           check=True, capture_output=True, text=True)
            print(f"\n  Pinned ER_PROPERTIES_ROOT to {TARGET}")
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"\n  Could not pin automatically ({e}).")
            print(f"  Set ER_PROPERTIES_ROOT to: {TARGET}")
    else:
        print(f"\n  (non-Windows) set ER_PROPERTIES_ROOT={TARGET}")

    print("\n  Close the app completely and start it again.")
    print("\n  NOTE: this folder no longer syncs or backs up - SharePoint was")
    print("  doing that for free. Arrange a backup for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
