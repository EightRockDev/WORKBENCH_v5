"""Find an existing `hampton_roads.db` elsewhere on the machine, and adopt it.

The operator upgraded from the v2.4.1 workbench, so the ETL database usually
already exists somewhere on the host - just not where v5 looks. Rather than ask
them to hunt for it and hand-edit `.env`, the app searches the likely roots and
copies the file into place itself.

Deliberately bounded: a fixed set of roots, a shallow depth, and a skip-list for
directories that are large and never hold this file. A full drive walk on a
Windows server would take minutes and is not worth it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from core import etl_db

MAX_DEPTH = 4

# Directories that are large, slow, or plainly wrong to walk.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages",
    "AppData", "Windows", "$Recycle.Bin", "Program Files", "Program Files (x86)",
    "ProgramData", "OneDriveTemp", "System Volume Information", ".cache",
}


def search_roots() -> list[Path]:
    """Where an upgraded install plausibly keeps the old workbench."""
    roots: list[Path] = [etl_db.APP_ROOT.parent]
    home = Path.home()
    roots.extend([home, home / "Documents", home / "Desktop", home / "Downloads"])
    # The pilot host keeps everything on the local C: drive (spec 9.2).
    for drive in ("C:/", "D:/"):
        p = Path(drive)
        if p.is_dir():
            roots.append(p)
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        try:
            resolved = r.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_existing_db(roots: list[Path] | None = None) -> list[Path]:
    """Every `hampton_roads.db` found under the search roots, largest first.

    Largest first because the real ETL output is tens of MB; an empty or
    half-written file from an aborted run is tiny.
    """
    hits: list[Path] = []
    for root in (roots if roots is not None else search_roots()):
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            here = Path(dirpath)
            if len(here.parts) - root_depth >= MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                           and not d.startswith(".")]
            if etl_db.DB_FILENAME in filenames:
                hits.append(here / etl_db.DB_FILENAME)
    unique = {p.resolve(): p for p in hits}
    return sorted(unique.values(),
                  key=lambda p: p.stat().st_size if p.is_file() else 0,
                  reverse=True)


def adopt(source: Path) -> Path:
    """Copy `source` into the location v5 reads, and return that path.

    A copy, not a move: the previous workbench keeps working. Refuses to copy a
    file onto itself, which would truncate it.
    """
    target = etl_db.preferred_location()
    if source.resolve() == target.resolve():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target
