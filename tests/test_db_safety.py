"""The suite must never be able to empty a live database again.

On 2026-08-18 `uv run pytest` on the owner's server truncated production:
5 user accounts, both organizations, 3 deals, 37 CRM contacts, 41 paid
skip-trace records, 81 inbox messages, 53 activity rows, 17 audit entries.
Cause: `conftest.py` loads `.env` so the pilot suites can find Postgres, and
on that machine `.env` is the LIVE connection — while six suites open each
test with `TRUNCATE users, organizations, audit_log ... CASCADE`.

Every test below asserts a property of the SUITE, not of the app, because
the defect was in the suite. Each one fails against the pre-fix tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pgguard import (
    assert_scratch_db,
    database_name,
    is_scratch_db,
    scratch_db_refusal,
)

TESTS_DIR = Path(__file__).resolve().parent
DESTRUCTIVE = re.compile(r"\bTRUNCATE\b|\bDELETE\s+FROM\s+users\b", re.I)


def _destructive_lines(src: str) -> list[tuple[int, str]]:
    """SQL that empties tables — not prose that happens to say 'truncate'.

    Requires the keyword AND a cursor call on the same line, so a docstring
    ("would truncate it to zero bytes") is not mistaken for a wipe.
    """
    hits = []
    for i, ln in enumerate(src.splitlines(), start=1):
        if DESTRUCTIVE.search(ln) and "execute" in ln:
            hits.append((i, ln.strip()))
    return hits

LIVE_URL = "postgresql://workbench:pw@127.0.0.1:5432/workbench"


# ---------------------------------------------------------------------------
# 1. The guard itself
# ---------------------------------------------------------------------------

def test_the_production_database_is_not_scratch():
    """`workbench` is the name in the owner's .env. This is the exact
    string that had to be rejected and wasn't."""
    assert not is_scratch_db(LIVE_URL)
    assert database_name(LIVE_URL) == "workbench"


@pytest.mark.parametrize("name", ["wb_test", "test", "testdb", "pilot_test",
                                  "scratch1", "wb_recover"])
def test_disposable_names_are_allowed(name):
    assert is_scratch_db(f"postgresql://u:p@localhost:5432/{name}")


@pytest.mark.parametrize("url", [None, "", "postgresql://u:p@localhost:5432/"])
def test_no_database_is_never_scratch(url):
    """Absent/!malformed URL must fail CLOSED, not open."""
    assert not is_scratch_db(url)


def test_the_refusal_message_names_the_database_and_the_way_out():
    msg = scratch_db_refusal(LIVE_URL)
    assert "workbench" in msg
    assert "wb_test" in msg, "the message must show how to fix it"


def test_the_override_is_explicit_and_opt_in(monkeypatch):
    monkeypatch.setenv("ER_ALLOW_LIVE_DB_TESTS", "1")
    assert is_scratch_db(LIVE_URL), "documented override must work"
    monkeypatch.setenv("ER_ALLOW_LIVE_DB_TESTS", "true")
    assert not is_scratch_db(LIVE_URL), "only an exact '1' may disarm it"


def test_assert_scratch_db_raises_against_a_live_url(monkeypatch):
    """The fixtures' last line of defence, exercised the way it fires."""
    from data import pg

    monkeypatch.setattr(pg, "database_url", lambda: LIVE_URL)
    with pytest.raises(RuntimeError, match="REFUSING"):
        assert_scratch_db()


def test_assert_scratch_db_passes_against_a_scratch_url(monkeypatch):
    from data import pg

    monkeypatch.setattr(
        pg, "database_url", lambda: "postgresql://u:p@localhost:5432/wb_test")
    assert_scratch_db()          # must not raise


# ---------------------------------------------------------------------------
# 2. conftest neutralizes a live URL for the whole session
# ---------------------------------------------------------------------------

def test_conftest_clears_a_live_database_url(monkeypatch):
    """The mechanism that makes every pg suite skip: with a live URL in the
    environment, DATABASE_URL is emptied before any fixture can read it."""
    import tests.conftest as ct

    monkeypatch.setenv("DATABASE_URL", LIVE_URL)
    monkeypatch.setenv("ER_BACKUP_DATABASE_URL", LIVE_URL)
    ct._neutralize_live_database_url()

    import os
    assert os.environ["DATABASE_URL"] == "", (
        "a live DATABASE_URL survived collection — the destructive suites "
        "would run against it")
    assert os.environ["ER_BACKUP_DATABASE_URL"] == ""


def test_the_warning_survives_the_q_flag():
    """pyproject sets addopts = "-q", which suppresses pytest's report
    header. A guard announced only there is invisible in every real run —
    the same silence that let this go unnoticed for nine days. Assert the
    warning reaches stderr in an actual subprocess run."""
    import subprocess
    import sys as _sys

    env = {**__import__("os").environ,
           "DATABASE_URL": LIVE_URL,
           "ER_ALLOW_LIVE_DB_TESTS": ""}
    proc = subprocess.run(
        [_sys.executable, "-m", "pytest", "--collect-only",
         "tests/test_pilot_admin.py"],
        cwd=str(TESTS_DIR.parent), env=env,
        capture_output=True, text=True, timeout=300)

    shown = proc.stdout + proc.stderr
    assert "[db-guard]" in shown, (
        "the live-database warning never reached the user:\n"
        f"{shown[-500:]}")
    assert "workbench" in shown


def test_conftest_leaves_a_scratch_database_url_alone(monkeypatch):
    import os

    import tests.conftest as ct

    scratch = "postgresql://u:p@localhost:5432/wb_test"
    monkeypatch.setenv("DATABASE_URL", scratch)
    ct._neutralize_live_database_url()
    assert os.environ["DATABASE_URL"] == scratch, (
        "the guard must not break legitimate Postgres testing")


# ---------------------------------------------------------------------------
# 3. No future test file can reintroduce the hole
# ---------------------------------------------------------------------------

def test_every_destructive_test_file_calls_the_guard():
    """A source scan, because the next TRUNCATE will be written by someone
    who never read this file. Any test module that truncates or deletes
    users must call assert_scratch_db() itself — conftest's neutralization
    is the first line, not the only one."""
    offenders = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        # Ignore this file's own regex/parametrize literals.
        if path.name == "test_db_safety.py":
            continue
        statements = _destructive_lines(src)
        if statements and "assert_scratch_db()" not in src:
            offenders.append(f"{path.name}:{statements[0][0]}: "
                             f"{statements[0][1][:60]}")
    assert not offenders, (
        "these test files can empty a database without checking which one "
        "it is:\n  " + "\n  ".join(offenders))


def test_the_truncating_fixtures_guard_before_they_truncate():
    """Order matters: a guard AFTER the TRUNCATE is decoration."""
    bad = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == "test_db_safety.py":
            continue
        src = path.read_text(encoding="utf-8")
        guards = [i for i, ln in enumerate(src.splitlines(), start=1)
                  if "assert_scratch_db()" in ln]
        for lineno, stmt in _destructive_lines(src):
            prior = [g for g in guards if g < lineno]
            if not prior or lineno - prior[-1] > 8:
                bad.append(f"{path.name}:{lineno}: {stmt[:60]}")
    assert not bad, ("destructive statements with no guard immediately "
                     "above them:\n  " + "\n  ".join(bad))
