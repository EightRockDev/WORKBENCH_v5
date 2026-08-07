"""The autopilot backup step — gating, failure honesty, and the URI contract.

No Postgres or pg_dump needed: subprocess is stubbed. What matters is the
logic that decides whether to dump, and that a failed dump can never mark
itself fresh or leave a partial file behind.
"""

from __future__ import annotations

import os
import time

import scripts.run_backup as rb


def _dump(dest, name, age_hours):
    p = dest / name
    p.write_bytes(b"x")
    old = time.time() - age_hours * 3600
    os.utime(p, (old, old))
    return p


def test_freshness_is_keyed_to_the_newest_dump(tmp_path):
    now = time.time()
    assert rb.is_fresh(tmp_path, now) is False          # no dumps ever
    _dump(tmp_path, "workbench-a.dump", age_hours=30)
    assert rb.is_fresh(tmp_path, now) is False          # stale
    _dump(tmp_path, "workbench-b.dump", age_hours=2)
    assert rb.is_fresh(tmp_path, now) is True           # fresh


def test_failed_dump_removes_partial_and_cannot_read_fresh(tmp_path, monkeypatch):
    out = tmp_path / "workbench-x.dump"

    class Boom:
        returncode = 1
        stderr = "connection refused"

    def fake_run(cmd, capture_output, text):
        out.write_bytes(b"partial")
        return Boom()

    monkeypatch.setattr(rb.subprocess, "run", fake_run)
    assert rb.run_dump("pg_dump", "postgresql://u:p@h/db", out) == 1
    assert not out.exists(), "a partial dump must be deleted"
    assert rb.is_fresh(tmp_path, time.time()) is False


def test_dump_passes_the_url_directly_no_credential_parsing(tmp_path, monkeypatch):
    seen = {}

    class Ok:
        returncode = 0
        stderr = ""

    def fake_run(cmd, capture_output, text):
        seen["cmd"] = cmd
        return Ok()

    monkeypatch.setattr(rb.subprocess, "run", fake_run)
    url = "postgresql://workbench:s3cret@localhost:5432/workbench"
    out = tmp_path / "workbench-y.dump"
    assert rb.run_dump("/usr/bin/pg_dump", url, out) == 0
    assert seen["cmd"] == ["/usr/bin/pg_dump", "-Fc", "-f", str(out), url]


def test_retention_prunes_only_old_workbench_dumps(tmp_path):
    now = time.time()
    old = _dump(tmp_path, "workbench-old.dump", age_hours=31 * 24)
    keep = _dump(tmp_path, "workbench-new.dump", age_hours=1)
    other = tmp_path / "unrelated.dump"
    other.write_bytes(b"x")
    os.utime(other, (now - 40 * 86400, now - 40 * 86400))
    assert rb.prune(tmp_path, now) == 1
    assert not old.exists() and keep.exists() and other.exists()


def test_no_database_url_is_a_notice_not_a_failure(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert rb.main() == 0
    assert "nothing to back up" in capsys.readouterr().out
