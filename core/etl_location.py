"""Resolve where the Hampton Roads ETL code and its generated DB live.

History: the ETL was a SEPARATE repo (eightrockdev/granite:hampton-roads-etl)
that the app imported as a SIBLING folder. As of 2026-08-09 the ETL code was
folded INTO this repo (``<repo>/hampton-roads-etl/``) so GRANITE could be
archived without losing it. This resolver checks every real layout so nothing
breaks in transition:

  ETL dir : in-repo ``<repo>/hampton-roads-etl`` -> sibling ``../hampton-roads-etl``
  ETL DB  : ``<repo>/data/hampton_roads.db`` (where the public-data ETL writes
            on the host) -> in-repo ETL dir -> sibling ETL dir

Every consumer (seller_floor, comps, distress radar, pull_chesapeake) goes
through here so the layout is defined in ONE place.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def etl_dir() -> Path:
    """The hampton-roads-etl directory: in-repo first, then legacy sibling."""
    in_repo = _REPO / "hampton-roads-etl"
    if in_repo.is_dir():
        return in_repo
    return _REPO.parent / "hampton-roads-etl"


def etl_db() -> Path:
    """Path to hampton_roads.db — first existing of the known locations, else
    the canonical in-repo data path (so callers can show a 'not built yet'
    notice against a stable path)."""
    candidates = [
        _REPO / "data" / "hampton_roads.db",            # host public-data ETL
        _REPO / "hampton-roads-etl" / "hampton_roads.db",
        _REPO.parent / "hampton-roads-etl" / "hampton_roads.db",  # legacy sibling
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]
