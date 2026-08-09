"""DoD Basic Allowance for Housing (BAH) — military housing rates by ZIP.

For Eight Rock Norfolk: BAH is a floor on what military tenants can pay.
If in-place rents at a Norfolk Class C are 20% below E-5-with-deps BAH for
the ZIP, there's organic pricing power on turnover that the seller's pro
forma probably doesn't price in.

Source: DoD Travel & Transportation site publishes annual BAH rate tables
plus a ZIP-to-MHA crosswalk. URLs shift annually and DoD blocks automated
fetches with 403 — manual download is the reliable path.
  https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/

Drop the four files DoD publishes into `hampton-roads-etl/Data/bah/`:
  - bahw{YY}.txt        — with-dependents rates (comma-delimited, no header)
  - bahwo{YY}.txt       — without-dependents rates (comma-delimited, no header)
  - mhanames{YY}.txt    — MHA code → name lookup (semicolon-delimited)
  - sorted_zipmha{YY}.txt — ZIP → MHA crosswalk (space-delimited)

Rate file column order (27 paygrades after MHA code):
  E1, E2, E3, E4, E5, E6, E7, E8, E9,
  W1, W2, W3, W4, W5,
  O1E, O2E, O3E,
  O1, O2, O3, O4, O5, O6, O7, O8, O9, O10

Two output tables:
  - bah_rates: paygrade × MHA × dependent-status × year combinations
  - bah_zip_mha: ZIP → MHA crosswalk
"""

from __future__ import annotations

import datetime as dt
import io
import re
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# Best-effort URL pattern. DoD reorganizes these annually — if 404s, the
# `pull_bah` function logs and returns empty DataFrames so the rest of the
# pipeline isn't blocked.
BAH_BASE = "https://www.travel.dod.mil"
BAH_URL_PATTERNS = (
    # Annual comma-delimited rates (with-deps and without-deps separately)
    "{base}/Portals/119/Documents/BAH/{year}/bahw{yy}.txt",
    "{base}/Portals/119/Documents/BAH/{year}/bahwo{yy}.txt",
)
BAH_ZIP_MHA_PATTERNS = (
    "{base}/Portals/119/Documents/BAH/{year}/sorted_zipmha{yy}.txt",
    "{base}/Portals/119/Documents/BAH/{year}/zipmha{yy}.txt",
)
BAH_MHA_NAME_PATTERNS = (
    "{base}/Portals/119/Documents/BAH/{year}/mhanames{yy}.txt",
)

# Official DoD column order for rate files (per ASCII-FILE-FORMAT.pdf,
# extended to include modern O8/O9/O10 paygrades that ship in the actual file).
PAYGRADE_COLS = (
    "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9",
    "W1", "W2", "W3", "W4", "W5",
    "O1E", "O2E", "O3E",
    "O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8", "O9", "O10",
)


def _try_fetch(url: str) -> str | None:
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.text
    except requests.RequestException:
        pass
    return None


def _parse_rate_table(
    text: str,
    year: int,
    with_deps: int,
    mha_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parse a comma-delimited, header-less BAH rate file.

    Format (one row per MHA):
        MHA_CODE,E1,E2,E3,...,O10
    """
    rows: list[dict[str, Any]] = []
    mha_names = mha_names or {}
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=",",
            header=None,
            low_memory=False,
            dtype=str,
        )
        if df.empty or df.shape[1] < 2:
            return rows

        # First column is MHA code; remaining columns are paygrades in the
        # canonical PAYGRADE_COLS order. If the file has more or fewer
        # columns than expected, fall back to generic E1, E2, ... naming
        # for any extras so we don't silently drop rates.
        n_paygrades = df.shape[1] - 1
        if n_paygrades >= len(PAYGRADE_COLS):
            colnames = ["MHA_CODE", *PAYGRADE_COLS]
            extras = [f"X{i}" for i in range(n_paygrades - len(PAYGRADE_COLS))]
            colnames.extend(extras)
        else:
            colnames = ["MHA_CODE", *PAYGRADE_COLS[:n_paygrades]]
        df.columns = colnames[: df.shape[1]]

        for _, row in df.iterrows():
            mha_code = str(row["MHA_CODE"]).strip()
            if not mha_code or mha_code.upper() == "MHA":
                continue
            mha_name = mha_names.get(mha_code, "")
            for pg in PAYGRADE_COLS:
                if pg not in df.columns:
                    continue
                rate = pd.to_numeric(row.get(pg), errors="coerce")
                if pd.isna(rate):
                    continue
                rows.append({
                    "mha_code": mha_code,
                    "mha_name": mha_name,
                    "paygrade": pg,
                    "with_dependents": with_deps,
                    "monthly_rate": int(rate),
                    "effective_year": year,
                })
    except (pd.errors.ParserError, KeyError, ValueError) as e:
        print(f"  [bah] rate parse failed for year={year} with_deps={with_deps}: {e}")
    return rows


def _parse_mha_names(text: str) -> dict[str, str]:
    """Parse semicolon-delimited MHA name file: `MHA;NAME, STATE`."""
    names: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ";" not in line:
            continue
        code, _, name = line.partition(";")
        names[code.strip()] = name.strip()
    return names


def _parse_zip_mha(text: str, year: int) -> list[dict[str, Any]]:
    """Parse space-delimited ZIP→MHA crosswalk: `ZIP MHA`."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # The published file is single-space-delimited, but tolerate any
        # whitespace in case DoD ever reformats.
        parts = re.split(r"\s+", line)
        if len(parts) < 2:
            continue
        zip5 = parts[0].zfill(5)
        mha = parts[1].strip()
        if not zip5.isdigit() or not mha:
            continue
        rows.append({
            "zip_code": zip5,
            "mha_code": mha,
            "effective_year": year,
        })
    return rows


def _year_tokens(year: int) -> tuple[str, str]:
    """Return (4-digit, 2-digit) tokens used in DoD filenames."""
    return f"{year:04d}", f"{year % 100:02d}"


def _local_bah_files(year: int, kind: str) -> list[Path]:
    """Find any locally-dropped BAH files for the given year and kind.

    Brian (or anyone) can manually download BAH files from DoD and drop them
    into `Data/bah/`. We accept DoD's native filename conventions
    (case-insensitive), in either 2-digit or 4-digit year tokens:

      kind="with-deps"     → bahw{YY}.txt, bahw_{YY}.txt
      kind="without-deps"  → bahwo{YY}.txt, bahwo_{YY}.txt
      kind="zip-mha"       → sorted_zipmha{YY}.txt, zipmha{YY}.txt
      kind="mha-names"     → mhanames{YY}.txt

    Skips .dat (binary) and .pdf files that DoD also publishes.
    """
    data_dir = Path(__file__).resolve().parent.parent / "Data" / "bah"
    if not data_dir.is_dir():
        return []
    yyyy, yy = _year_tokens(year)
    out: list[Path] = []
    for f in data_dir.iterdir():
        if not f.is_file():
            continue
        # Only accept .txt files — skip .dat (binary) and .pdf (docs)
        if f.suffix.lower() != ".txt":
            continue
        name = f.stem.lower()
        if yyyy not in name and yy not in name:
            continue

        if kind == "with-deps":
            # bahw26, bahw_26, bahw2026 — but NOT bahwo
            if re.match(r"^bahw_?\d{2,4}$", name):
                out.append(f)
        elif kind == "without-deps":
            if re.match(r"^bahwo_?\d{2,4}$", name):
                out.append(f)
        elif kind == "zip-mha":
            if "zipmha" in name or "zip_mha" in name:
                out.append(f)
        elif kind == "mha-names":
            if name.startswith("mhanames") or name.startswith("mha_names"):
                out.append(f)
    return out


def pull_bah() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull last 2 years of BAH rates + ZIP-MHA crosswalk.

    Strategy:
      1. Try the legacy URL patterns at travel.dod.mil (often blocked or 404)
      2. Fall back to LOCAL files in `Data/bah/` if you've manually
         downloaded the rate files from DoD's BAH portal.

    DoD blocks automated fetches with 403 errors — manual download is the
    reliable path. From <https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/>
    download the year's rate tables (bahw{YY}.txt, bahwo{YY}.txt,
    sorted_zipmha{YY}.txt, mhanames{YY}.txt) and save them as plain .txt files
    in `hampton-roads-etl/Data/bah/`, then re-run this puller.
    """
    today = dt.date.today()
    rates: list[dict[str, Any]] = []
    zips: list[dict[str, Any]] = []

    for year in (today.year - 1, today.year):
        yyyy, yy = _year_tokens(year)

        # ---- Try direct URL fetch (almost always blocked) ----
        # First grab MHA names if available, so rate parser can join them
        mha_names: dict[str, str] = {}
        for url_pat in BAH_MHA_NAME_PATTERNS:
            url = url_pat.format(base=BAH_BASE, year=yyyy, yy=yy)
            text = _try_fetch(url)
            if text:
                mha_names = _parse_mha_names(text)
                break

        for idx, url_pat in enumerate(BAH_URL_PATTERNS):
            with_deps = 1 if idx == 0 else 0  # bahw=with, bahwo=without
            url = url_pat.format(base=BAH_BASE, year=yyyy, yy=yy)
            text = _try_fetch(url)
            if text:
                rates.extend(_parse_rate_table(text, year, with_deps, mha_names))

        for url_pat in BAH_ZIP_MHA_PATTERNS:
            url = url_pat.format(base=BAH_BASE, year=yyyy, yy=yy)
            text = _try_fetch(url)
            if text:
                zips.extend(_parse_zip_mha(text, year))
                break

        # ---- Fall back to locally-dropped files ----
        # Always check local even if we got something from URL — they may be
        # complementary (e.g., URL gave rates but no names file)
        local_names = _local_bah_files(year, "mha-names")
        if local_names and not mha_names:
            for f in local_names:
                print(f"  [bah] loading local file: {f.name}")
                mha_names = _parse_mha_names(f.read_text(encoding="utf-8", errors="replace"))
                break

        # Track whether we've already parsed rates for this year — if URL
        # fetch returned data we don't want to double-load from local files.
        year_has_with_deps = any(r["effective_year"] == year and r["with_dependents"] == 1 for r in rates)
        year_has_without_deps = any(r["effective_year"] == year and r["with_dependents"] == 0 for r in rates)

        if not year_has_with_deps:
            for f in _local_bah_files(year, "with-deps"):
                print(f"  [bah] loading local file: {f.name}")
                rates.extend(_parse_rate_table(
                    f.read_text(encoding="utf-8", errors="replace"),
                    year, 1, mha_names,
                ))
        if not year_has_without_deps:
            for f in _local_bah_files(year, "without-deps"):
                print(f"  [bah] loading local file: {f.name}")
                rates.extend(_parse_rate_table(
                    f.read_text(encoding="utf-8", errors="replace"),
                    year, 0, mha_names,
                ))

        year_has_zips = any(z["effective_year"] == year for z in zips)
        if not year_has_zips:
            for f in _local_bah_files(year, "zip-mha"):
                print(f"  [bah] loading local file: {f.name}")
                zips.extend(_parse_zip_mha(
                    f.read_text(encoding="utf-8", errors="replace"),
                    year,
                ))

    if not rates and not zips:
        print(
            "  [bah] no rates fetched (DoD blocks automated downloads with 403).\n"
            "        To populate this table:\n"
            "        1. Visit https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/\n"
            "        2. Download these four files for the current year:\n"
            "             bahw{YY}.txt           (with-dependents rates)\n"
            "             bahwo{YY}.txt          (without-dependents rates)\n"
            "             mhanames{YY}.txt       (MHA code → name lookup)\n"
            "             sorted_zipmha{YY}.txt  (ZIP → MHA crosswalk)\n"
            "        3. Save them in: hampton-roads-etl/Data/bah/\n"
            "        4. Re-run: python hampton_roads_etl.py --only=bah"
        )
    return pd.DataFrame(rates), pd.DataFrame(zips)
