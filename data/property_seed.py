"""Put the deal folders where the app reads them, without anyone running anything.

The bug this closes (2026-08-15, found from a screenshot of Grand Hampton at
Langley showing "No sales loaded for Hampton yet"):

`PROPERTIES_ROOT` was pinned to one fixed directory inside the app folder -
the right call, and the owner's - but the deal folders themselves were still
sitting in the OneDrive/SharePoint sync root where they have always lived.
The app was pointed at an empty directory, found no folder for the property,
and fell through to county records. The card then blamed a nightly data pull
for something that was really "your `sales.json` is 200 feet to the left".

The move existed as a script the owner had to run. That is the part that was
wrong. A fix that requires the owner to go find and double-click a batch file
is not a fix - it is a to-do item handed back to him, and this one had been
handed back three times.

So the app does it itself, on startup:

  * the destination NEVER moves - it is the one fixed folder the owner chose,
    and every write still goes there;
  * the source is only ever READ, and only to seed a destination that has
    nothing in it. Nothing is moved, renamed, or deleted;
  * a folder already at the destination is never overwritten - the
    destination always wins, so a later hand edit cannot be clobbered by a
    stale copy;
  * it runs once. A marker file records what was seeded from where, so
    normal startups do no filesystem walking at all.

Deliberately narrow: this only ever fires into an EMPTY destination. Once
deal folders live at the destination, this module does nothing, forever -
including if the owner later deletes a folder on purpose. Re-seeding a
destination the owner has curated would be the app second-guessing him,
which is the failure mode the fixed location was chosen to end.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_MARKER = "_seeded_from.txt"

# Where deal folders are known to live on this owner's machine. Ordered most
# specific first. The OneDrive/SharePoint pattern is the real one:
#   C:\Users\<user>\<Org>\<Org> - Documents\Properties
_SOURCE_GLOBS = (
    "*/* - Documents/Properties",
    "OneDrive*/*/Properties",
    "OneDrive*/Properties",
    "*/Properties",
    "Documents/Properties",
)


def _deal_folder_count(root: Path) -> tuple[int, int]:
    """(deal folders, folders carrying sales.json). (0, 0) if unreadable."""
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
        return (0, 0)
    return (deals, withs)


def candidate_sources(target: Path) -> list[Path]:
    """Readable folders that could hold the deal folders, best first.

    Ranked by how much hand-verified sale history each holds, because
    `sales.json` is the file this whole exercise is about. A decoy folder
    with empty subfolders therefore loses to the real one even if it sorts
    first alphabetically.
    """
    home = Path.home()
    seen: set[Path] = set()
    scored: list[tuple[int, int, Path]] = []

    cands: list[Path] = []
    for pattern in _SOURCE_GLOBS:
        try:
            cands.extend(sorted(home.glob(pattern)))
        except OSError:
            continue
    # The classic sibling-of-the-app layout, from before the fixed location.
    cands.append(target.parent.parent / "Properties")

    for cand in cands:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved in seen or resolved == target.resolve():
            continue
        seen.add(resolved)
        deals, withs = _deal_folder_count(cand)
        if deals:
            scored.append((withs, deals, cand))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [c for _, _, c in scored]


def _copy_folder(src: Path, dst: Path) -> bool:
    """Copy one deal folder. False on any failure, without raising."""
    try:
        shutil.copytree(src, dst, dirs_exist_ok=False)
        return True
    except (OSError, shutil.Error):
        # A half-copied folder is worse than none: it would read as present
        # and suppress any later attempt.
        try:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
        except OSError:
            pass
        return False


def seed_if_empty(target: Path) -> tuple[int, Path | None]:
    """Fill an empty `target` from wherever the deal folders actually are.

    Returns ``(folders_copied, source)``; ``(0, None)`` when there was
    nothing to do, which is the normal case on every run after the first.

    Never raises. This runs on the app's startup path, and a data-location
    convenience must not be able to stop the app from loading.
    """
    try:
        target = Path(target)
        if (target / _MARKER).is_file():
            return (0, None)

        existing, _ = _deal_folder_count(target)
        if existing:
            # Already populated - the destination is the truth from here on.
            _write_marker(target, None, 0, note="already populated")
            return (0, None)

        sources = candidate_sources(target)
        if not sources:
            return (0, None)          # nothing found; try again next start
        source = sources[0]

        copied = 0
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            return (0, None)

        try:
            children = sorted(source.iterdir())
        except OSError:
            return (0, None)

        for child in children:
            try:
                if child.is_dir():
                    if child.name.startswith("."):
                        continue
                    dst = target / child.name
                    if dst.exists():
                        continue      # destination wins, always
                    if _copy_folder(child, dst):
                        copied += 1
                elif child.is_file() and child.name.startswith("_"):
                    # Root-level config: _custom_props.json, _favorites.json,
                    # _saved_searches.json. Small, and the app reads them
                    # from the same root.
                    dst = target / child.name
                    if not dst.exists():
                        shutil.copy2(child, dst)
            except (OSError, shutil.Error):
                continue              # one bad folder must not stop the rest

        if copied:
            _write_marker(target, source, copied)
        return (copied, source if copied else None)
    except Exception:
        return (0, None)


def _write_marker(target: Path, source: Path | None, copied: int,
                  note: str = "") -> None:
    """Record that seeding is done, so later startups skip the walk entirely."""
    try:
        body = [
            "Deal folders are read from and written to THIS folder.",
            "",
            f"seeded_folders: {copied}",
            f"seeded_from: {source if source else '(nothing - ' + (note or 'n/a') + ')'}",
            "",
            "The source above was only READ. Nothing there was moved or",
            "deleted; it is still exactly as it was. Delete it yourself once",
            "you are satisfied everything came across.",
            "",
            "Delete this marker file to let the app seed this folder again",
            "(it only ever seeds a folder that has no deal folders in it).",
        ]
        (target / _MARKER).write_text("\n".join(body) + "\n", encoding="utf-8")
    except OSError:
        pass
