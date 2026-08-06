"""Send a test Workbench email — run ON THE HOST, where .env holds the SMTP
credentials.

    uv run python scripts/send_test_email.py                 # sends to SMTP_USER
    uv run python scripts/send_test_email.py you@example.com  # explicit recipient

Prints exactly what happened and why: configured or not, the From identity in
use, and the send result. Read-only apart from the one outbound message.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import mailer  # noqa: E402  (loads .env itself)


def main() -> int:
    to = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SMTP_USER", "")
    print(f"configured : {mailer.is_configured()}")
    print(f"smtp host  : {os.environ.get('SMTP_HOST', '(unset)')}")
    print(f"smtp user  : {os.environ.get('SMTP_USER', '(unset)')}")
    print(f"mail from  : {mailer._from_addr() or '(unset)'}")
    print(f"sending to : {to or '(no recipient!)'}")
    ok, reason = mailer.send_signup_email(to, "Test Recipient")
    print(f"result     : sent={ok}  ({reason})")
    if ok:
        print("\nCheck the inbox (and spam folder). The From line should read "
              "the MAIL_FROM identity above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
