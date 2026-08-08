"""One-shot discovery probe: find the JSON API behind Virginia Beach's
property portal (owner ask 2026-08-08: free sale/deed history, no login).

https://propertysearch.virginiabeach.gov/#/property/14552807310000 is an SPA,
so the data comes from an API this probe tries to locate two ways:

1. Guess a battery of conventional endpoint shapes for a KNOWN parcel
   (14552807310000 = 1201 Edenham Ct; expected: instrument 202103057031,
   sale 2021-07-15, $12,300,000 — from the owner's screenshot).
2. Pull the SPA's JS bundle(s) and regex out every URL/path fragment that
   mentions api/property/sales/parcel — the bundle names its own endpoints.

Runs on the HOST via autopilot (the build container is firewalled from city
portals). Polite: ~a dozen requests, half-second gaps, honest UA. Idempotent:
once a prior run FOUND the API, later runs exit immediately — this is
scaffolding for building the real puller, removed once that ships.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "vb-sales-probe.txt"
BASE = "https://propertysearch.virginiabeach.gov"
PARCEL = "14552807310000"
# Values that prove we hit the real sales record (owner screenshot):
MARKERS = ("202103057031", "12300000", "12,300,000")

UA = {"User-Agent": "EightRockWorkbench/1.0 (research; contact bmccune@gmail.com)"}

GUESSES = [
    f"/api/property/{PARCEL}",
    f"/api/properties/{PARCEL}",
    f"/api/parcel/{PARCEL}",
    f"/api/parcels/{PARCEL}",
    f"/api/property/{PARCEL}/sales",
    f"/api/sales/{PARCEL}",
    f"/api/property?parcel={PARCEL}",
    f"/api/search?q={PARCEL}",
]


def fetch(path: str) -> tuple[int, str, str]:
    try:
        r = requests.get(BASE + path, headers=UA, timeout=20)
        ctype = r.headers.get("content-type", "")
        return r.status_code, ctype, r.text[:900]
    except Exception as exc:
        return 0, "", f"(request failed: {exc})"


def main() -> int:
    if REPORT.exists() and "PROBE COMPLETE - FOUND" in REPORT.read_text(
            encoding="utf-8", errors="ignore"):
        print("probe already succeeded on a previous cycle - skipping "
              "(delete reports/vb-sales-probe.txt to re-run)")
        return 0

    lines: list[str] = [f"VB sales-API probe, parcel {PARCEL}"]
    found: list[str] = []

    # --- 1. conventional endpoint guesses --------------------------------
    for path in GUESSES:
        code, ctype, body = fetch(path)
        hit = any(m in body.replace(",", "") or m in body for m in MARKERS)
        lines.append(f"\n== GET {path}\n   status={code} type={ctype[:40]}"
                     f"{'   <<< MARKER HIT' if hit else ''}")
        if code == 0:
            lines.append("   " + body[:300])      # the exception text - a
            # probe that hides WHY it failed wastes its whole cycle
        elif code == 200 and ("json" in ctype or body.lstrip()[:1] in "[{"):
            lines.append("   " + body[:600].replace("\n", " "))
        if hit:
            found.append(path)
        time.sleep(0.5)

    # --- 2. read the SPA bundle for its own endpoint strings -------------
    code, ctype, index = fetch("/")
    lines.append(f"\n== GET /   status={code}")
    if code == 0:
        lines.append("   " + index[:300])
        # WAFs often block non-browser agents outright - one retry with a
        # real browser UA tells us whether UA is the gate.
        UA["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36")
        code, ctype, index = fetch("/")
        lines.append(f"== GET / (browser UA)   status={code}")
        if code == 0:
            lines.append("   " + index[:300])
    bundles = re.findall(r'src="([^"]+\.js[^"]*)"', index)
    lines.append(f"   js bundles: {bundles[:6]}")
    api_frags: set[str] = set()
    for b in bundles[:4]:
        path = b if b.startswith("/") else "/" + b
        code, _, js = fetch(path)
        if code != 200:
            lines.append(f"   bundle {path}: status={code}")
            continue
        # full bundle, not the 900-char preview
        try:
            js = requests.get(BASE + path, headers=UA, timeout=30).text
        except Exception:
            pass
        for frag in re.findall(
                r'["\'](/?[A-Za-z0-9_./-]*(?:api|Api|API)[A-Za-z0-9_./{}$-]*)["\']', js):
            if len(frag) < 120:
                api_frags.add(frag)
        for frag in re.findall(
                r'["\'](/[A-Za-z0-9_./-]*(?:sales|Sales|property|Property|parcel|Parcel)[A-Za-z0-9_./{}$-]*)["\']', js):
            if len(frag) < 120:
                api_frags.add(frag)
        time.sleep(0.5)
    lines.append(f"\n== endpoint fragments mined from bundles "
                 f"({len(api_frags)}):")
    for frag in sorted(api_frags):
        lines.append(f"   {frag}")

    # --- 3. try mined fragments that look parcel-shaped ------------------
    for frag in sorted(api_frags):
        if "api" not in frag.lower():
            continue
        candidate = frag.replace("{id}", PARCEL).replace("${id}", PARCEL)
        if PARCEL not in candidate:
            candidate = candidate.rstrip("/") + "/" + PARCEL
        code, ctype, body = fetch(candidate)
        hit = any(m in body.replace(",", "") or m in body for m in MARKERS)
        if code == 200 or hit:
            lines.append(f"\n== GET {candidate}\n   status={code} "
                         f"type={ctype[:40]}{'   <<< MARKER HIT' if hit else ''}")
            lines.append("   " + body[:600].replace("\n", " "))
            if hit:
                found.append(candidate)
        time.sleep(0.5)

    verdict = ("PROBE COMPLETE - FOUND: " + "; ".join(found)) if found else \
        "PROBE COMPLETE - no marker hit; endpoints above need a human/next-round look"
    lines.append("\n" + verdict)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(verdict)
    print(f"full detail -> {REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
