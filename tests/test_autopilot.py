"""Autopilot: hands-free update -> pipeline -> publish (V5.9)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.autopilot_run import publish  # noqa: E402


def _sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _make_repo_pair(tmp_path):
    """A bare 'origin' plus a working clone with identity configured."""
    bare = tmp_path / "origin.git"
    _sh(["git", "init", "--bare", "-b", "main", str(bare)], tmp_path)
    work = tmp_path / "work"
    _sh(["git", "clone", str(bare), str(work)], tmp_path)
    for k, v in (("user.name", "Test Host"), ("user.email", "t@t.local")):
        _sh(["git", "config", k, v], work)
    (work / "seed.txt").write_text("seed")
    _sh(["git", "add", "seed.txt"], work)
    _sh(["git", "commit", "-m", "seed"], work)
    _sh(["git", "push", "origin", "main"], work)
    return bare, work


def test_publish_pushes_report_files(tmp_path):
    bare, work = _make_repo_pair(tmp_path)
    (work / "reports").mkdir()
    report = work / "reports" / "phase0-latest.txt"
    report.write_text("P0-1 GATE PASSED")
    assert publish([report], "2026-07-29", root=work) is True
    # The bare remote now has the report on main.
    shown = _sh(["git", "show", "main:reports/phase0-latest.txt"], bare)
    assert "P0-1 GATE PASSED" in shown.stdout


def test_publish_recovers_from_detached_head_and_stale_rebase(tmp_path):
    bare, work = _make_repo_pair(tmp_path)
    # Wedge the repo exactly like the pilot host: detached HEAD + a stale
    # rebase-merge directory.
    head = _sh(["git", "rev-parse", "HEAD"], work).stdout.strip()
    _sh(["git", "checkout", head], work)
    (work / ".git" / "rebase-merge").mkdir()
    (work / "reports").mkdir()
    report = work / "reports" / "pull-latest.txt"
    report.write_text("stale-state publish")
    assert publish([report], "wedged", root=work) is True
    shown = _sh(["git", "show", "main:reports/pull-latest.txt"], bare)
    assert "stale-state publish" in shown.stdout


def test_publish_is_idempotent_when_nothing_changed(tmp_path):
    bare, work = _make_repo_pair(tmp_path)
    (work / "reports").mkdir()
    report = work / "reports" / "discover-latest.txt"
    report.write_text("same content")
    assert publish([report], "run-1", root=work) is True
    assert publish([report], "run-2", root=work) is True  # "nothing new"


def test_publish_survives_remote_moving_ahead(tmp_path):
    """Another writer (Claude pushing code) landed a commit between runs -
    the rebase+retry path must still publish."""
    bare, work = _make_repo_pair(tmp_path)
    other = tmp_path / "other"
    _sh(["git", "clone", str(bare), str(other)], tmp_path)
    for k, v in (("user.name", "Other"), ("user.email", "o@o.local")):
        _sh(["git", "config", k, v], other)
    (other / "code.txt").write_text("new code")
    _sh(["git", "add", "code.txt"], other)
    _sh(["git", "commit", "-m", "code change"], other)
    _sh(["git", "push", "origin", "main"], other)

    (work / "reports").mkdir()
    report = work / "reports" / "phase0-latest.txt"
    report.write_text("report after remote moved")
    assert publish([report], "raced", root=work) is True
    shown = _sh(["git", "show", "main:reports/phase0-latest.txt"], bare)
    assert "report after remote moved" in shown.stdout


def test_stranded_commit_from_failed_push_is_published_next_run(tmp_path):
    """First cycle: commit succeeded but push failed (auth pending). The
    next run has nothing new to commit - it must STILL push the stranded
    commit instead of orphaning it behind "nothing new"."""
    bare, work = _make_repo_pair(tmp_path)
    (work / "reports").mkdir()
    report = work / "reports" / "phase0-latest.txt"
    report.write_text("stranded run")
    # Simulate the failed-push state: local commit exists, remote never saw it.
    _sh(["git", "add", "-f", str(report)], work)
    _sh(["git", "commit", "-m", "autopilot report: stranded"], work)
    # publish() with unchanged files -> "nothing new" path -> must push anyway.
    assert publish([report], "next-night", root=work) is True
    shown = _sh(["git", "show", "main:reports/phase0-latest.txt"], bare)
    assert "stranded run" in shown.stdout
