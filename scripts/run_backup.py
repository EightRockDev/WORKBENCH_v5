"""Autopilot step: nightly Postgres dump — the backup that never existed.

A server task audit (2026-08-08) found the documented "schedule backup.ps1"
step was never performed: the pilot database (users, orgs, deals, POC
records, audit log) had NO backups, ever. Per CLAUDE.md lesson 9b the fix is
to ride the loop that already runs instead of a second human-registered
schedule: this step runs every autopilot cycle and self-gates to one dump
per day.

Freshness is keyed to the artifact, not a clock stamp: if the newest dump is
under 24h old we skip; a FAILED attempt writes no dump and therefore cannot
mark itself fresh (the 2026-08-02 freshness lesson). Retention prunes dumps
older than ER_BACKUP_RETAIN_DAYS (default 30).

Optional off-site copy: set ER_BACKUP_ONEDRIVE_DIR in .env to a synced
folder (one plain line — no schtasks quoting). Only the CLOSED dump file is
copied; the live DB never touches a synced folder (spec §9.2).
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")          # headless step: load creds ourselves
except Exception:
    pass

FRESH_HOURS = int(os.environ.get("ER_BACKUP_FRESH_HOURS", "24"))
RETAIN_DAYS = int(os.environ.get("ER_BACKUP_RETAIN_DAYS", "30"))


def backup_dir() -> Path:
    override = os.environ.get("ER_BACKUP_DIR", "")
    if override:
        return Path(override)
    if os.name == "nt" and Path("D:\\").exists():
        return Path("D:\\Backup\\8rw")
    if os.name == "nt":
        return Path("C:\\Backup\\8rw")
    return ROOT / "data" / "backups"


def find_pg_dump() -> str | None:
    hit = shutil.which("pg_dump")
    if hit:
        return hit
    for ver in ("17", "16", "15"):
        cand = Path(f"C:\\Program Files\\PostgreSQL\\{ver}\\bin\\pg_dump.exe")
        if cand.exists():
            return str(cand)
    return None


def newest_dump(dest: Path) -> Path | None:
    dumps = sorted(dest.glob("workbench-*.dump"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return dumps[0] if dumps else None


def is_fresh(dest: Path, now: float) -> bool:
    latest = newest_dump(dest)
    if latest is None:
        return False
    return (now - latest.stat().st_mtime) < FRESH_HOURS * 3600


def prune(dest: Path, now: float) -> int:
    cutoff = now - RETAIN_DAYS * 86400
    n = 0
    for p in dest.glob("workbench-*.dump"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            n += 1
    return n


def run_dump(pg_dump: str, url: str, out: Path) -> int:
    # pg_dump accepts the connection URI directly - no credential parsing,
    # no PGPASSWORD in the process environment or a shell history.
    proc = subprocess.run([pg_dump, "-Fc", "-f", str(out), url],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr.strip()[:800])
        out.unlink(missing_ok=True)      # a partial dump must not read fresh
    return proc.returncode


def main() -> int:
    # FORCE ROW LEVEL SECURITY blocks pg_dump for the app's own role (found
    # on the first-ever dump attempt, 2026-08-08: "query would be affected by
    # row-level security policy for table campaigns"). Dumps need the
    # dedicated read-only BYPASSRLS role — see CLAUDE.md for the one-time
    # psql command that creates it.
    url = (os.environ.get("ER_BACKUP_DATABASE_URL", "").strip()
           or os.environ.get("DATABASE_URL", "").strip())
    if os.environ.get("DATABASE_URL") and not os.environ.get("ER_BACKUP_DATABASE_URL"):
        print("[backup] NOTE: using the app's DATABASE_URL - this FAILS under "
              "row-level security; set ER_BACKUP_DATABASE_URL to the "
              "backup_reader role (see CLAUDE.md)")
    if not url:
        print("[backup] no DATABASE_URL visible - nothing to back up "
              "(set it in .env; on dev boxes without Postgres this is normal)")
        return 0
    pg_dump = find_pg_dump()
    if pg_dump is None:
        print("[backup] pg_dump not found on this machine - install the "
              "PostgreSQL client tools; SKIPPING (reason: no pg_dump)")
        return 0

    dest = backup_dir()
    dest.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().timestamp()
    if is_fresh(dest, now):
        latest = newest_dump(dest)
        print(f"[backup] fresh (newest dump {latest.name} is under "
              f"{FRESH_HOURS}h old) - skipping")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest / f"workbench-{stamp}.dump"
    print(f"[backup] dumping to {out}")
    code = run_dump(pg_dump, url, out)
    if code != 0:
        print(f"[backup] FAILED (pg_dump exit {code}) - no dump written, "
              "next cycle retries")
        return 1
    size_mb = out.stat().st_size / 1_000_000
    print(f"[backup] wrote {out.name} ({size_mb:.1f} MB)")

    onedrive = os.environ.get("ER_BACKUP_ONEDRIVE_DIR", "").strip()
    if onedrive:
        try:
            Path(onedrive).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, Path(onedrive) / out.name)
            print(f"[backup] off-site copy -> {onedrive}")
        except OSError as exc:
            print(f"[backup] off-site copy FAILED ({exc}) - local dump kept")
    else:
        print("[backup] no ER_BACKUP_ONEDRIVE_DIR set - local-only "
              "(add the line to .env for an off-site copy)")

    n = prune(dest, now)
    if n:
        print(f"[backup] pruned {n} dump(s) older than {RETAIN_DAYS}d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
