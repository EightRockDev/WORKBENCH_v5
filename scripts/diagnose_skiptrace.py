"""Skip-trace live-provider diagnostic — run ON THE SERVER (real internet + keys).

The build/CI environment is firewalled from vendor hosts, so live adapters
(esp. the VA SCC CIS scraper) must be verified where the app actually runs. This
prints the RAW vendor response + the parsed result for each configured provider,
so we can tune field mappings against reality in one round.

Usage (on the box, from C:\\WORKBENCH_V5):
    uv run python scripts/diagnose_skiptrace.py --entity "Cleghorn Capital LLC"
    uv run python scripts/diagnose_skiptrace.py --name "Robert Cleghorn" --address "1200 Ballentine Blvd, Norfolk VA"

Reads the same env as the app (ER_SKIPTRACE_PROVIDERS + vendor keys from .env).
Nothing is persisted; this only calls the vendors and prints. Real calls may cost
money (BatchData/Trestle) — SOS/VA-SCC is free.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

from core.skiptrace import providers as prov  # noqa: E402


def _dump(title, obj):
    print(f"\n=== {title} ===")
    if hasattr(obj, "__dict__"):
        obj = obj.__dict__
    print(json.dumps(obj, indent=2, default=str))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", help="LLC/entity name to resolve via SOS (VA SCC)")
    ap.add_argument("--name", help="person name to skip-trace")
    ap.add_argument("--address", help="mailing/property address hint")
    ap.add_argument("--state", default="VA")
    args = ap.parse_args()

    mode = os.environ.get("ER_SKIPTRACE_PROVIDERS", "mock")
    reg = prov.get_registry()
    print(f"ER_SKIPTRACE_PROVIDERS={mode}")
    print("Provider status:", json.dumps(reg.status, indent=2))
    if mode != "live":
        print("\n[!] Not in live mode — set ER_SKIPTRACE_PROVIDERS=live in .env to hit real vendors.")

    if args.entity:
        print(f"\n### SOS / entity piercing: {args.entity!r} ({reg.status.get('sos')})")
        try:
            res = reg.sos.resolve_entity(args.entity, args.state)
            _dump("SOSResult", res if res else "(no result / None)")
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")

    trace_name = args.name or (args.entity or "")
    if trace_name:
        print(f"\n### Skip trace: {trace_name!r} @ {args.address!r} ({reg.status.get('skiptrace')})")
        for tier in reg.trace_waterfall:
            try:
                cand = tier.trace_person(trace_name, args.address, args.state)
                _dump(f"{getattr(tier,'name','?')} (tier {getattr(tier,'tier','?')})",
                      cand if cand else "(no match / None)")
                if cand and getattr(cand, "phones", None):
                    v = reg.validation.validate_phone(cand.phones[0], trace_name)
                    _dump(f"validation of {cand.phones[0]} ({reg.status.get('validation')})", v)
                    break
            except Exception as e:
                print(f"[ERROR] {getattr(tier,'name','?')}: {type(e).__name__}: {e}")

    if not args.entity and not args.name:
        print("\nNothing to do. Pass --entity and/or --name (+ --address). See --help.")


if __name__ == "__main__":
    main()
