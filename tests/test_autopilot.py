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


def test_schedule_hourly_until_clean_then_nightly():
    from scripts.autopilot_run import schedule_args, TASK_NAME
    hourly = schedule_args(False, r"C:\WORKBENCH_V5\autopilot.bat")
    assert "/SC" in hourly and "HOURLY" in hourly and TASK_NAME in hourly
    nightly = schedule_args(True, r"C:\WORKBENCH_V5\autopilot.bat")
    assert "DAILY" in nightly and "03:00" in nightly


def test_schedule_command_wakes_and_catches_up():
    """The first nightly run was silently skipped: the host slept through
    3 AM and the task had neither wake nor catch-up. Both are mandatory."""
    from scripts.autopilot_run import schedule_command
    for clean in (True, False):
        cmd = schedule_command(clean, r"C:\WORKBENCH_V5\autopilot.bat")
        assert "-WakeToRun" in cmd
        assert "-StartWhenAvailable" in cmd
    assert "-Daily -At 3am" in schedule_command(True, "x")
    assert "RepetitionInterval" in schedule_command(False, "x")


def test_live_log_is_never_tracked_and_a_copy_ships_instead(tmp_path):
    """2026-07-30: reports/autopilot.log was git-tracked while the running
    .bat held it open - Windows locks it, every rebase/checkout that had
    to rewrite it failed, and ALL publishes bounced non-fast-forward.
    publish() must untrack the live log (self-heal for old clones) while
    still delivering the report files."""
    bare, work = _make_repo_pair(tmp_path)
    (work / "reports").mkdir()
    live = work / "reports" / "autopilot.log"
    live.write_text("=== cycle 1 ===\n")
    # Old-clone state: the live log tracked and pushed.
    _sh(["git", "add", "-f", str(live)], work)
    _sh(["git", "commit", "-m", "old design: live log tracked"], work)
    _sh(["git", "push", "origin", "main"], work)
    live.write_text("=== cycle 1 ===\n=== cycle 2 (dirty) ===\n")

    report = work / "reports" / "phase0-latest.txt"
    report.write_text("gate data")
    assert publish([report], "log-fix", root=work) is True
    # The live log is untracked afterward - and still on disk, untouched.
    tracked = _sh(["git", "ls-files", "reports/autopilot.log"], work)
    assert tracked.stdout.strip() == ""
    assert "cycle 2" in live.read_text()
    # The report made it to the remote; the remote tree dropped the log.
    shown = _sh(["git", "show", "main:reports/phase0-latest.txt"], bare)
    assert "gate data" in shown.stdout
    gone = _sh(["git", "show", "main:reports/autopilot.log"], bare)
    assert gone.returncode != 0
