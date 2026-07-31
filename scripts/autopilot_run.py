"""Autopilot stage 2: the full data cycle, hands-free.

Runs on freshly-updated code (stage 1 = scripts/autopilot.py):
  discover feeds -> pull Hampton Roads -> rebuild the backbone -> publish
  every report to GitHub.

WAL mode on workbench.db means the app/service may stay up throughout.
Each step's complete output is captured to reports/*.txt; a failed step
never stops the later ones (their reports say what happened).
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

STEPS = (
    ("discover", ["scripts/discover_feeds.py"], "discover-latest.txt"),
    ("pull", ["etl_munidata.py", "--hr"], "pull-latest.txt"),
    ("publicdata", ["scripts/run_public_data.py"], "public-data-latest.txt"),
    ("listings", ["scripts/run_listings.py"], "listings-latest.txt"),
    ("phase0", ["scripts/run_phase0.py"], "phase0-latest.txt"),
    ("alerts", ["scripts/run_alerts.py"], "alerts-latest.txt"),
    ("preflight", ["scripts/preflight_cutover.py"], "cutover-preflight.txt"),
)


def git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root,
                          capture_output=True, text=True)


def run_step(name: str, args: list[str], out_name: str) -> tuple[Path, int]:
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / out_name
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"autopilot {name} @ {stamp}\n\n")
        fh.flush()
        proc = subprocess.run([sys.executable, "-u", *args], cwd=ROOT,
                              stdout=fh, stderr=subprocess.STDOUT, text=True)
    print(f"[{name}] exit {proc.returncode} -> {out.name}", flush=True)
    return out, proc.returncode


def publish(files: list[Path], label: str, root: Path = ROOT) -> bool:
    """Commit + push the run's artifacts. Repairs wedged state first.

    The LIVE log (reports/autopilot.log) must never be in these files:
    the .bat holds it open for append, Windows locks it, and any git
    operation that rewrites the worktree (rebase, checkout, stash pop)
    then fails - which is how an entire cycle's publishes got rejected
    non-fast-forward on 2026-07-30. It stays untracked (.gitignore);
    a COPY (autopilot-run.log) is what ships.
    """
    for cmd in (("rebase", "--abort"), ("cherry-pick", "--abort"),
                ("merge", "--abort")):
        git(*cmd, root=root)
    # Self-heal clones that still track the live log from before the fix.
    git("rm", "--cached", "--ignore-unmatch", "-q",
        "reports/autopilot.log", root=root)
    symref = git("symbolic-ref", "-q", "HEAD", root=root)
    if symref.returncode != 0:
        git("checkout", "-B", "main", root=root)
    for f in files:
        git("add", "-f", str(f), root=root)
    committed = git("commit", "-m", f"autopilot report: {label}", root=root)
    if committed.returncode != 0:
        print("[publish] nothing new to commit this run")
    # ALWAYS sync + push: a failed push on a previous run (e.g. the very
    # first cycle, waiting on the one-time GitHub authorize) leaves a
    # stranded local commit that "nothing new" would otherwise orphan
    # forever. Pushing an already-pushed branch is a harmless no-op.
    git("pull", "--rebase", "--autostash", "origin", "main", root=root)
    pushed = git("push", "origin", "main", root=root)
    if pushed.returncode != 0:
        git("pull", "--rebase", "--autostash", "origin", "main", root=root)
        pushed = git("push", "origin", "main", root=root)
    ok = pushed.returncode == 0
    print("[publish]", "pushed to GitHub" if ok
          else f"PUSH FAILED: {pushed.stderr.strip()[:400]}")
    return ok


TASK_NAME = "EightRockWorkbenchAutopilot"


def schedule_args(clean: bool, task_path: str) -> list[str]:
    """schtasks fallback (no wake/catch-up support - see schedule_command)."""
    base = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", task_path]
    if clean:
        return base + ["/SC", "DAILY", "/ST", "03:00"]
    return base + ["/SC", "HOURLY", "/MO", "1"]


def schedule_command(clean: bool, task_path: str) -> str:
    """PowerShell registration with the two settings a sleeping host NEEDS:
    -WakeToRun (the 3 AM run happens even if the machine sleeps) and
    -StartWhenAvailable (a missed run fires as soon as the machine wakes).
    The first nightly run never happened because neither was set - the
    "always-on" host was asleep at 3 AM and the task silently skipped."""
    if clean:
        trig = "New-ScheduledTaskTrigger -Daily -At 3am"
    else:
        # HOURLY dev cadence (owner directive 2026-07-31), anchored to the
        # TOP OF THE HOUR rather than "now + N". reschedule() runs at the end
        # of every cycle, so a relative anchor was recomputed each time and
        # the schedule never survived: "+2 minutes" re-armed to +2 minutes
        # again on every finish, which is why cycles ran back-to-back all day
        # and the console window kept reappearing. A wall-clock anchor is
        # idempotent - re-registering mid-cycle yields the same next run.
        trig = ("New-ScheduledTaskTrigger -Once "
                "-At (Get-Date).Date.AddHours((Get-Date).Hour + 1) "
                "-RepetitionInterval (New-TimeSpan -Hours 1)")
    return (
        f"$a = New-ScheduledTaskAction -Execute '{task_path}'; "
        f"$t = {trig}; "
        "$s = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable "
        "-MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 3); "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a "
        "-Trigger $t -Settings $s -Force"
    )


def reschedule(clean: bool) -> None:
    if os.name != "nt":
        return
    task_path = str(ROOT / "autopilot.bat")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         schedule_command(clean, task_path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        # Fall back to plain schtasks (no wake support, but a schedule).
        proc = subprocess.run(schedule_args(clean, task_path),
                              capture_output=True, text=True)
    mode = "nightly 3:00 AM (stable)" if clean else \
        "HOURLY - next cycle at the top of the hour (dev cadence)"
    print(f"[schedule] {mode} (wake+catchup, exit {proc.returncode})",
          flush=True)


def main() -> int:
    """Publish EACH step's report the moment it finishes - the owner (and
    Claude) see progress near-live instead of waiting out the whole cycle.
    A final sweep publish catches anything left (incl. the full log)."""
    day = datetime.date.today().isoformat()
    outputs: list[Path] = []
    codes: list[int] = []

    def extras_now() -> list[Path]:
        files = [ROOT / "data" / "feeds_extra.json",
                 REPORTS / "phase0-gates.json"]
        live = REPORTS / "autopilot.log"
        pub = REPORTS / "autopilot-run.log"
        try:  # ship a COPY of the live log - never the locked file itself
            shutil.copyfile(live, pub)
            files.append(pub)
        except OSError:
            pass
        return [f for f in files if f.exists()]

    for name, args, out_name in STEPS:
        out, code = run_step(name, args, out_name)
        outputs.append(out)
        codes.append(code)
        step_files = [f for f in [out] if f.exists()] + extras_now()
        publish(step_files, f"{day} {name}")
    files = [f for f in outputs if f.exists()] + extras_now()
    ok = publish(files, f"{day} final")
    clean = ok and all(c == 0 for c in codes)
    # DEV CADENCE (owner directive 2026-07-31): the system is under active
    # build - cycles run HOURLY, on the hour, regardless of cleanliness so
    # data and code flow all day. Flip DEV_MODE to False when the owner
    # declares the build stable; nightly 3 AM resumes automatically.
    DEV_MODE = True
    if DEV_MODE:
        clean_sched = False
    else:
        clean_sched = clean
    print(f"[cycle] {'CLEAN' if clean else 'not clean'} "
          f"(steps {codes}, published={ok})", flush=True)
    reschedule(clean_sched)
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
