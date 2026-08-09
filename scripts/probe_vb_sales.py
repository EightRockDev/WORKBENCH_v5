"""Discovery probe: confirm the Spatialest sales endpoint behind Virginia
Beach's property portal (owner ask 2026-08-08; vendor identified 2026-08-09
from the portal's Network tab).

Confirmed pattern (from the live site):
  https://api.spatialest.com/v1/va/virginiabeach/<resource>/<GPIN>
Working siblings seen returning 200: annual-taxes, annual-assessment,
buildings, schools, images-api; plus
  https://propertysearch.virginiabeach.gov/api/v1/recordcard/<GPIN>
The SALES data (instrument / deed book+page / sale date / price) loads only
when the Sales tab is opened, so this probe tries the likely sales resource
names for the KNOWN parcel and confirms via markers from the owner's
screenshot ($12,300,000 / instrument 202103057031 / 2021 sale).

Runs on the HOST via autopilot (build env is firewalled from these hosts).
The VB portal serves an incomplete TLS chain, so requests retry insecurely
for DISCOVERY ONLY (labeled). Idempotent: stops probing once FOUND.
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "vb-sales-probe.txt"
GPIN = "14552807310000"
MARKERS = ("202103057031", "12300000", "12,300,000", "2021-07-15", "07/15/2021")

UA = {"User-Agent": "EightRockWorkbench/1.0 (research; contact bmccune@gmail.com)",
      "Accept": "application/json"}

SPATIALEST = "https://api.spatialest.com/v1/va/virginiabeach"
PORTAL = "https://propertysearch.virginiabeach.gov/api/v1"

# Candidate resource names for the sales/deed record, most likely first.
CANDIDATES = [
    f"{SPATIALEST}/sales/{GPIN}",
    f"{SPATIALEST}/sales-history/{GPIN}",
    f"{SPATIALEST}/saleshistory/{GPIN}",
    f"{SPATIALEST}/deeds/{GPIN}",
    f"{SPATIALEST}/transfers/{GPIN}",
    f"{SPATIALEST}/sales-information/{GPIN}",
    f"{PORTAL}/recordcard/{GPIN}",            # full card - likely embeds sales
    f"{SPATIALEST}/recordcard/{GPIN}",
    f"{SPATIALEST}/annual-assessment/{GPIN}",  # known-good, proves reachability
]


def fetch(url: str) -> tuple[int, str, str]:
    for verify in (True, False):
        try:
            r = requests.get(url, headers=UA, timeout=25, verify=verify)
            body = r.text[:1500]
            if not verify:
                body = "[INSECURE-TLS] " + body[:1400]
            return r.status_code, r.headers.get("content-type", ""), body
        except requests.exceptions.SSLError:
            try:
                import urllib3
                urllib3.disable_warnings()
            except Exception:
                pass
            continue                          # retry once with verify=False
        except Exception as exc:
            return 0, "", f"(request failed: {exc})"
    return 0, "", "(failed even with verify=False)"


def main() -> int:
    if REPORT.exists() and "PROBE COMPLETE - FOUND" in REPORT.read_text(
            encoding="utf-8", errors="ignore"):
        print("probe already found the endpoint - skipping "
              "(delete reports/vb-sales-probe.txt to re-run)")
        return 0

    lines = [f"VB Spatialest sales probe, GPIN {GPIN}"]
    found = []
    for url in CANDIDATES:
        code, ctype, body = fetch(url)
        has_sale = any(m in body.replace(",", "") or m in body for m in MARKERS)
        looks_json = "json" in ctype or body.lstrip()[:1] in "[{" or \
                     body.startswith("[INSECURE-TLS] [") or \
                     body.startswith("[INSECURE-TLS] {")
        tag = "   <<< SALES MARKER" if has_sale else ""
        lines.append(f"\n== GET {url}\n   status={code} type={ctype[:40]}{tag}")
        if code == 200 and looks_json:
            lines.append("   " + body[:700].replace("\n", " "))
        elif code != 200:
            lines.append("   " + body[:200])
        if has_sale:
            found.append(url)
        time.sleep(0.6)

    verdict = ("PROBE COMPLETE - FOUND: " + "; ".join(found)) if found else \
        ("PROBE COMPLETE - reached the API but no sales marker; the sales "
         "resource name is still unconfirmed (owner can click the Sales tab "
         "with Network open and paste that one URL).")
    lines.append("\n" + verdict)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
