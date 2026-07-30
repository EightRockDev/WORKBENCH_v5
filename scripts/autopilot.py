"""Autopilot stage 1: bring the host's code to origin/main.

STABLE FILE - keep it boring. It runs BEFORE the update, so a bug here
can't be fixed by pushing a fix. All the evolving pipeline logic lives in
scripts/autopilot_run.py, which executes AFTER this update completes.

Also repairs every kind of wedged git state seen on the pilot host
(stale rebase, abandoned cherry-pick, detached HEAD, missing identity).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "https://github.com/EightRockDev/WORKBENCH_v5.git"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT,
                          capture_output=True, text=True)


def main() -> int:
    git("config", "--global", "--add", "safe.directory", ROOT.as_posix())
    git("config", "user.name", "Workbench Host")
    git("config", "user.email", "host@eight-rock.local")
    git("config", "credential.helper", "manager")
    git("remote", "set-url", "origin", REMOTE)
    for cmd in (("rebase", "--abort"), ("cherry-pick", "--abort"),
                ("merge", "--abort")):
        git(*cmd)
    fetched = git("fetch", "origin")
    if fetched.returncode != 0:
        print("autopilot update: fetch failed:", fetched.stderr.strip())
        return 1
    result = git("checkout", "-B", "main", "origin/main")
    print((result.stdout + result.stderr).strip())
    print("autopilot update: code at origin/main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
