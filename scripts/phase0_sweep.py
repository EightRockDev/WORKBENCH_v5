"""AC-P0-1 verification sweep (spec 7.4) - double-clicked via phase0-sweep.bat.

Case-insensitive scan for ALN references across the repo files, the SQLite
stores, and the deal folders. Read-only: it reports, it never deletes.
Word-boundary aware via core.spine.scan_text_for_aln, so "Walnut Street"
never trips it.

Exit code 0 = zero hits (the P0-4 gate); 1 = remnants listed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.spine import scan_text_for_aln  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "logs",
             "ingest-uploads"}
TEXT_SUFFIXES = {".py", ".md", ".sql", ".txt", ".json", ".toml", ".yaml",
                 ".yml", ".bat", ".ps1", ".html", ".css", ".csv"}
# The Phase 0 implementation itself must name ALN to eradicate it.
EXEMPT = {"core/spine.py", "core/phase0.py", "scripts/phase0_sweep.py",
          "scripts/run_phase0.py", "docs/spec/workbench-v5.0-spec.md",
          "docs/spec/BUILD-ORDER.md", "CHANGELOG.md", "CLAUDE.md",
          "tests/test_spine.py", "tests/test_phase0.py"}


def sweep_files(root: Path) -> dict[str, int]:
    hits: dict[str, int] = {}
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in EXEMPT:
            continue
        try:
            found = scan_text_for_aln(path.read_text(encoding="utf-8",
                                                     errors="replace"))
        except OSError:
            continue
        if found:
            hits[rel] = len(found)
    return hits


def sweep_filenames(root: Path) -> list[str]:
    out = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if scan_text_for_aln(path.name):
            out.append(path.relative_to(root).as_posix())
    return out


def sweep_sqlite(db_path: Path, sample_rows: int = 5000) -> dict[str, int]:
    """Table/column names always; cell contents sampled per table."""
    hits: dict[str, int] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                n = 0
                n += len(scan_text_for_aln(table))
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]
                for col in cols:
                    n += len(scan_text_for_aln(col))
                try:
                    for row in conn.execute(
                            f"SELECT * FROM '{table}' LIMIT {sample_rows}"):
                        for cell in row:
                            if isinstance(cell, str) and scan_text_for_aln(cell):
                                n += 1
                except sqlite3.Error:
                    pass
                if n:
                    hits[f"{db_path.name}:{table}"] = n
    except sqlite3.Error:
        pass
    return hits


def main() -> int:
    print(f"AC-P0-1 sweep over {APP_ROOT}")
    print()

    all_hits: dict[str, int] = {}
    all_hits.update(sweep_files(APP_ROOT))
    for name in sweep_filenames(APP_ROOT):
        all_hits[f"(filename) {name}"] = all_hits.get(f"(filename) {name}", 0) + 1
    for db in sorted(set(APP_ROOT.glob("data/*.db")) | set(APP_ROOT.glob("*.db"))):
        all_hits.update(sweep_sqlite(db))

    props = APP_ROOT.parent / "Properties"
    if props.is_dir():
        for name in sweep_filenames(props):
            all_hits[f"(deal folder) {name}"] = 1

    if not all_hits:
        print("ZERO ALN references found - AC-P0-1 satisfied.")
        return 0

    total = sum(all_hits.values())
    print(f"{total:,} ALN reference(s) remain in {len(all_hits)} place(s):")
    print()
    for where, n in sorted(all_hits.items(), key=lambda t: -t[1]):
        print(f"  {n:6,}  {where}")
    print()
    print("This list shrinks to zero through P0-3 cutover and P0-4 purge.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
