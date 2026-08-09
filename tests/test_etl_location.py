"""ETL location resolver — the ETL folded into this repo (2026-08-09) must be
found in-repo, with the legacy sibling layout still honored as a fallback so
the host doesn't break during transition."""

from __future__ import annotations

from pathlib import Path

from core import etl_location as el


def test_etl_dir_prefers_in_repo():
    # The code was copied into <repo>/hampton-roads-etl/, so it must resolve
    # there (this repo now ships the ETL).
    d = el.etl_dir()
    assert d.name == "hampton-roads-etl"
    assert (d / "hampton_roads_etl.py").is_file()


def test_etl_db_returns_a_path_even_when_absent():
    # No db in the container; resolver still yields the canonical data/ path
    # so callers can render a "not built yet" notice rather than crash.
    p = el.etl_db()
    assert isinstance(p, Path) and p.name == "hampton_roads.db"


def test_in_repo_etl_actually_shipped():
    # Guards the archive: GRANITE can be archived only because the ETL now
    # lives here. If someone deletes it, this fails loudly.
    repo = Path(el.__file__).resolve().parent.parent
    assert (repo / "hampton-roads-etl" / "hampton_roads_etl.py").is_file()
    assert (repo / "hampton-roads-etl" / "db.py").is_file()
