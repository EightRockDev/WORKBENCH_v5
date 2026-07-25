"""Where the Hampton Roads public-data ETL database lives.

One resolver, used by every reader (`core/market_data.py`, `core/calibration.py`)
so the location is defined in exactly one place.

Why this exists: the path used to be hard-coded as three levels above the module
(`<root>/python_workbench/core/` -> `<root>/hampton-roads-etl/`), which was
correct in the v2.4.1 repo layout. In v5 `core/` sits directly under the app
root, so that expression pointed a level too high (on the pilot host, at
`C:\\hampton-roads-etl\\`). The database was therefore never found even when it
was present, and the UI reported "ETL database not loaded" with no way to fix it.

Resolution order (first file that exists wins):
  1. ``$ER_ETL_DB``                      - explicit override, full path to the file
  2. ``<app root>/data/hampton_roads.db``     - preferred home in v5
  3. ``<app root>/hampton-roads-etl/hampton_roads.db``
  4. ``<app root>/../hampton-roads-etl/hampton_roads.db`` - legacy v2.4.1 sibling

Nothing found -> :func:`resolve_etl_db` returns ``None`` and
:func:`preferred_location` names where to put the file, so the UI can give an
instruction that actually works instead of naming a script that is not deployed.
"""

from __future__ import annotations

import os
from pathlib import Path

DB_FILENAME = "hampton_roads.db"

APP_ROOT = Path(__file__).resolve().parent.parent


def candidates() -> list[Path]:
    """Every location searched, in priority order."""
    found: list[Path] = []
    override = os.environ.get("ER_ETL_DB", "").strip()
    if override:
        found.append(Path(override).expanduser())
    found.extend([
        APP_ROOT / "data" / DB_FILENAME,
        APP_ROOT / "hampton-roads-etl" / DB_FILENAME,
        APP_ROOT.parent / "hampton-roads-etl" / DB_FILENAME,
    ])
    return found


def resolve_etl_db() -> Path | None:
    """The ETL database file, or None when it isn't present anywhere."""
    for path in candidates():
        if path.is_file():
            return path
    return None


def preferred_location() -> Path:
    """Where to drop the file when there isn't one yet (used in UI copy)."""
    return APP_ROOT / "data" / DB_FILENAME
