"""Helper called by setup-ingest.bat. Not meant to be run by hand.

Two jobs:
  env    - make sure .env has a password and the four settings
  patch  - switch the ingest routes on inside api_server.py

Both are safe to run more than once; they detect what is already done.
api_server.py is backed up before it is touched.
"""

from __future__ import annotations

import re
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"
API = HERE / "api_server.py"

MARKER = "# --- deal ingest (added by setup-ingest.bat) ---"
BLOCK = f"""

{MARKER}
from ingest_routes import include_ingest_routes
include_ingest_routes(app)
"""

SETTINGS = {
    "EIGHT_ROCK_DB_PATH": "data/workbench.db",
    "EIGHT_ROCK_DOCS_ROOT": "data/deal_docs",
    "EIGHT_ROCK_INGEST_URL": "http://127.0.0.1:8600",
}


def do_env() -> int:
    text = ENV.read_text(encoding="utf-8", errors="replace") if ENV.exists() else ""
    added = []

    if "EIGHT_ROCK_INGEST_TOKEN=" not in text:
        token = secrets.token_urlsafe(32)
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"EIGHT_ROCK_INGEST_TOKEN={token}\n"
        added.append("password")

    for key, value in SETTINGS.items():
        if f"{key}=" not in text:
            text += f"{key}={value}\n"
            added.append(key)

    if added:
        ENV.write_text(text, encoding="utf-8")
        print(f"        Added to .env: {', '.join(added)}")
    else:
        print("        Already set up. Nothing to change.")
    return 0


def do_patch() -> int:
    if not API.exists():
        print("        api_server.py is missing.")
        return 1

    source = API.read_text(encoding="utf-8", errors="replace")

    if MARKER in source:
        print("        Already switched on. Nothing to change.")
        return 0

    # Confirm there is an app object to attach to before touching anything.
    if not re.search(r"^\s*app\s*=\s*FastAPI\(", source, re.M):
        print("        I could not find the line that creates the API in")
        print("        api_server.py, so I did not change it.")
        print("        Tell Claude: 'no app = FastAPI line in api_server.py'.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = API.with_name(f"api_server.py.bak-{stamp}")
    shutil.copy2(API, backup)

    API.write_text(source.rstrip("\n") + "\n" + BLOCK, encoding="utf-8")

    # Prove the edited file still parses. Restore it if not.
    try:
        compile(API.read_text(encoding="utf-8"), str(API), "exec")
    except SyntaxError as exc:
        shutil.copy2(backup, API)
        print(f"        The edit broke the file, so I put it back. ({exc})")
        return 1

    print(f"        Switched on. Backup saved as {backup.name}")
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "env":
        raise SystemExit(do_env())
    if action == "patch":
        raise SystemExit(do_patch())
    print("Run setup-ingest.bat instead of this file.")
    raise SystemExit(1)
