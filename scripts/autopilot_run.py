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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

STEPS = (
    ("discover", ["scripts/discover_feeds.py"], "discover-latest.txt"),
    ("pull", ["etl_munidata.py", "--hr"], "pull-latest.txt"),
    ("phase0", ["scripts/run_phase0.py"], "phase0-latest.txt"),
)


def git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root,
                          capture_output=True, text=True)


def run_step(name: str, args: list[str], out_name: str) -> Path:
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / out_name
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"autopilot {name} @ {stamp}\n\n")
        fh.flush()
        proc = subprocess.run([sys.executable, "-u", *args], cwd=ROOT,
                              stdout=fh, stderr=subprocess.STDOUT, text=True)
    print(f"[{name}] exit {proc.returncode} -> {out.name}", flush=True)
    return out


def publish(files: list[Path], label: str, root: Path = ROOT) -> bool:
    """Commit + push the run's artifacts. Repairs wedged state first."""
    for cmd in (("rebase", "--abort"), ("cherry-pick", "--abort"),
                ("merge", "--abort")):
        git(*cmd, root=root)
    symref = git("symbolic-ref", "-q", "HEAD", root=root)
    if symref.returncode != 0:
        git("checkout", "-B", "main", root=root)
    for f in files:
        git("add", "-f", str(f), root=root)
    committed = git("commit", "-m", f"autopilot report: {label}", root=root)
    if committed.returncode != 0:
        print("[publish] nothing new to publish")
        return True
    git("pull", "--rebase", "--autostash", "origin", "main", root=root)
    pushed = git("push", "origin", "main", root=root)
    if pushed.returncode != 0:
        git("pull", "--rebase", "--autostash", "origin", "main", root=root)
        pushed = git("push", "origin", "main", root=root)
    ok = pushed.returncode == 0
    print("[publish]", "pushed to GitHub" if ok
          else f"PUSH FAILED: {pushed.stderr.strip()[:400]}")
    return ok


def main() -> int:
    outputs: list[Path] = []
    for name, args, out_name in STEPS:
        outputs.append(run_step(name, args, out_name))
    extras = [ROOT / "data" / "feeds_extra.json", REPORTS / "autopilot.log"]
    files = [f for f in outputs + extras if f.exists()]
    ok = publish(files, datetime.date.today().isoformat())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
