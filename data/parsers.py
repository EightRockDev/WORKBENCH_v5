"""Parsers for documents uploaded to a property folder.

The workbench renders structured data out of `sources.json`; it does **not**
read raw spreadsheets. So when a rent roll or T-12 is uploaded it has to be
parsed into the `sources.json` blocks the UI consumes (`rentRoll`, `t12_*`,
`t12_income`, `totalOpex`, `fixedCharges`) or it never shows up.

`parse_uploaded_document()` is the entry point — called by the Documents
uploader in `ui/property_detail.py` for every file saved to a property
folder. It returns a `ParseResult` describing what was extracted.

Supported formats
-----------------
- **Rent roll** — the tabular "RENT ROLL DETAIL" export (RealPage / OneSite /
  Franklin-style), `.xls` or `.xlsx`.
- **T-12** — the "12 Month Income Statement" operating statement, `.xls`
  or `.xlsx`.

Detection is signature-based (header / label tokens). An unrecognized layout
returns `kind="unrecognized"` rather than guessing — a wrong parse is worse
than no parse. OMs, tax cards and market reports have no structured-data
home in the app, so they are saved as-is and reported as skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """Outcome of parsing one uploaded file.

    `blocks` holds the top-level `sources.json` keys to merge in (empty when
    nothing was extracted). `kind` is one of: rent_roll, t12, unrecognized,
    skipped, error. `message` is a one-line human summary for the UI.
    """

    kind: str
    blocks: dict[str, Any] = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Spreadsheet reading (.xls + .xlsx → uniform list-of-rows)
# ---------------------------------------------------------------------------

_XLSX_EXTS = {".xlsx", ".xlsm"}
_XLS_EXTS = {".xls"}
_SPREADSHEET_EXTS = _XLSX_EXTS | _XLS_EXTS


def _workbook_rows(path: Path) -> dict[str, list[list[Any]]]:
    """Return ``{sheet_name: [[cell, ...], ...]}`` for an .xls or .xlsx file.

    `.xlsx` is read with openpyxl (cached values via data_only); `.xls` with
    xlrd. Any other extension yields an empty dict.
    """
    ext = path.suffix.lower()
    out: dict[str, list[list[Any]]] = {}
    if ext in _XLSX_EXTS:
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            for ws in wb.worksheets:
                out[ws.title] = [list(r) for r in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    elif ext in _XLS_EXTS:
        import xlrd

        wb = xlrd.open_workbook(str(path))
        for sh in wb.sheets():
            out[sh.name] = [
                [sh.cell_value(r, c) for c in range(sh.ncols)]
                for r in range(sh.nrows)
            ]
    return out


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------


def _norm(v: Any) -> str:
    """Lowercase, collapse whitespace/newlines — for header/label matching."""
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().lower()


def _text(v: Any) -> str:
    """Trimmed string of a cell, or '' for blanks."""
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _num(v: Any) -> float | None:
    """Best-effort numeric parse. Handles 1,234 / (123) / $ / blanks."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def _cell(row: list[Any], idx: int | None) -> Any:
    """Value at column `idx` in `row`, or None when the column is missing
    (idx None) or the row is shorter than the header."""
    if idx is None or idx >= len(row):
        return None
    return row[idx]


_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


def _find_date(cells: list[Any]) -> date | None:
    """First MM/DD/YYYY date found in a list of cells, or None."""
    for c in cells:
        m = _DATE_RE.search(str(c) if c is not None else "")
        if m:
            mo, dy, yr = (int(g) for g in m.groups())
            if yr < 100:
                yr += 2000
            try:
                return date(yr, mo, dy)
            except ValueError:
                continue
    return None


# ===========================================================================
# Rent roll parser
# ===========================================================================

# A header row must contain at least 3 of these normalized tokens.
_RR_SIGNATURE = {
    "bldg/unit", "floorplan", "unit/lease status", "market + addl.",
    "lease end", "total billing",
}

# field -> ordered list of acceptable normalized header names
_RR_COLUMNS: dict[str, list[str]] = {
    "unit": ["bldg/unit", "unit"],
    "unitType": ["floorplan", "floor plan"],
    "sqft": ["sqft", "sq ft", "square feet"],
    "status": ["unit/lease status", "lease status", "status"],
    "tenant": ["name", "resident", "tenant"],
    "moveIn": ["move-in", "move in"],
    "moveOut": ["move-out", "move out"],
    "leaseExp": ["lease end", "lease expiration", "lease exp"],
    # Prefer a base "market"/"market rent" column; "market + addl." bundles
    # non-market recurring charges and overstates scheduled rent.
    "marketRent": ["market rent", "market", "market + addl.", "market + addl"],
    "actualRent": ["rent"],          # exact match only — avoids "lease rent"
    "totalCharges": ["total billing", "total charges"],
    "mtom": ["mtom", "mtm"],
}

_RR_SECONDARY = ("former", "applicant", "pending")  # rows that aren't the live lease


def _classify_status(raw: str) -> str:
    """Map a raw rent-roll status to current / notice / vacant / skip."""
    s = raw.strip().lower()
    if not s:
        return "skip"
    if "former" in s or s == "applicant" or "pending" in s:
        return "skip"
    if "occupied" in s:
        return "notice" if ("ntv" in s or "notice" in s) else "current"
    if any(k in s for k in ("vacant", "down", "admin", "model")):
        return "vacant"
    return "current"  # unknown but live — count as occupied


def _match_columns(header: list[Any]) -> dict[str, int]:
    """Map our field names to column indices using the header row."""
    norm = [_norm(c) for c in header]
    col: dict[str, int] = {}
    for field_name, candidates in _RR_COLUMNS.items():
        for cand in candidates:
            if cand in norm:                       # exact match preferred
                col[field_name] = norm.index(cand)
                break
        else:
            if field_name == "actualRent":
                continue                           # exact-only — don't fuzzy-match
            for cand in candidates:                # fall back to substring
                hit = next((i for i, h in enumerate(norm) if cand in h), None)
                if hit is not None:
                    col[field_name] = hit
                    break
    return col


def parse_rent_roll(path: Path) -> ParseResult | None:
    """Parse a rent roll into ``{"rentRoll": {...}}`` or return None if the
    file is not a recognizable rent roll."""
    sheets = _workbook_rows(path)
    for rows in sheets.values():
        header_idx = None
        for i, row in enumerate(rows[:20]):
            norm = {_norm(c) for c in row}
            if len(_RR_SIGNATURE & norm) >= 3:
                header_idx = i
                break
        if header_idx is None:
            continue

        col = _match_columns(rows[header_idx])
        if not {"unit", "status"}.issubset(col):
            continue

        # Effective-date: first date in the rows above the header.
        eff: date | None = None
        for row in rows[:header_idx]:
            eff = _find_date(row) or eff
            if eff:
                break

        seen: set[str] = set()
        units: list[dict[str, Any]] = []
        for row in rows[header_idx + 1:]:
            unit = _text(row[col["unit"]]) if col["unit"] < len(row) else ""
            if not unit or unit.lower().startswith("total"):
                continue
            raw_status = _text(row[col["status"]]) if col["status"] < len(row) else ""
            status = _classify_status(raw_status)
            if status == "skip":
                continue
            if unit in seen:                       # keep first live lease only
                continue
            seen.add(unit)

            units.append({
                "unit": unit,
                "unitType": _text(_cell(row, col.get("unitType"))),
                "status": status,
                "tenant": _text(_cell(row, col.get("tenant"))),
                "sqft": int(_num(_cell(row, col.get("sqft"))) or 0),
                "marketRent": round(_num(_cell(row, col.get("marketRent"))) or 0),
                "actualRent": round(_num(_cell(row, col.get("actualRent"))) or 0),
                "totalCharges": round(_num(_cell(row, col.get("totalCharges"))) or 0),
                "isMTM": (_num(_cell(row, col.get("mtom"))) or 0) > 0,
                "moveIn": _text(_cell(row, col.get("moveIn"))),
                "leaseExp": _text(_cell(row, col.get("leaseExp"))),
                "moveOut": _text(_cell(row, col.get("moveOut"))),
                "rawStatus": raw_status,
            })

        if not units:
            continue

        total = len(units)
        n_current = sum(1 for u in units if u["status"] == "current")
        n_notice = sum(1 for u in units if u["status"] == "notice")
        n_vacant = sum(1 for u in units if u["status"] == "vacant")
        block = {
            "date": eff.isoformat() if eff else date.today().isoformat(),
            "file": path.name,
            "summary": {
                "totalUnits": total,
                "occupied": n_current,
                "notice": n_notice,
                "vacant": n_vacant,
                "occupancyPct": round((total - n_vacant) / total * 100, 1),
                "totalMarketRent": sum(u["marketRent"] for u in units),
                "totalActualRent": sum(
                    u["actualRent"] for u in units
                    if u["status"] in ("current", "notice")
                ),
                "avgSqft": round(sum(u["sqft"] for u in units) / total),
            },
            "units": units,
        }
        return ParseResult(
            kind="rent_roll",
            blocks={"rentRoll": block},
            message=(
                f"rent roll parsed — {total} units "
                f"({n_current} occupied, {n_notice} notice, {n_vacant} vacant)"
            ),
        )
    return None


# ===========================================================================
# T-12 parser
# ===========================================================================

# Column-A subtotal labels (normalized) -> target field.
_T12_LABELS = {
    "net operating income": "noi",
    "total income": "egi",
    "gross potential rent": "gpr",
    "total non-rental income": "other_income",
    "net rental income": "net_rental",
    "total taxes & insurance": "taxes_insurance",
    "total operating expenses": "total_opex",
}


def parse_t12(path: Path) -> ParseResult | None:
    """Parse a 12-month income statement into the `t12_*` / `totalOpex` /
    `fixedCharges` blocks, or return None if not a recognizable T-12."""
    sheets = _workbook_rows(path)
    for rows in sheets.values():
        # Detection: an "income statement" / "operating statement" title near
        # the top AND a "net operating income" label somewhere in column A.
        head_blob = " ".join(_norm(c) for r in rows[:8] for c in r)
        if "income statement" not in head_blob and "operating statement" not in head_blob:
            continue
        col_a = [_norm(r[0]) if r else "" for r in rows]
        if not any("net operating income" in a for a in col_a):
            continue

        # Locate the "Total" column from the header row that carries it.
        total_col: int | None = None
        for row in rows[:15]:
            for ci, c in enumerate(row):
                if _norm(c) in ("total", "ytd", "total ytd"):
                    total_col = ci
                    break
            if total_col is not None:
                break
        if total_col is None:
            continue

        # Collect every (row, value) per label; values come from total_col.
        found: dict[str, list[float]] = {}
        for row in rows:
            label = _norm(row[0]) if row else ""
            target = _T12_LABELS.get(label)
            if not target:
                continue
            val = _num(row[total_col]) if total_col < len(row) else None
            if val is not None:
                found.setdefault(target, []).append(val)

        noi = found["noi"][0] if found.get("noi") else None
        egi = found["egi"][0] if found.get("egi") else None
        if noi is None or egi is None:
            continue  # not enough to trust this as a T-12

        # "TOTAL OPERATING EXPENSES" appears twice (before/after extraordinary
        # items) — the LAST one ties out to NOI.
        total_opex = found["total_opex"][-1] if found.get("total_opex") else None
        taxes_ins = found["taxes_insurance"][0] if found.get("taxes_insurance") else None
        gpr = found["gpr"][0] if found.get("gpr") else None
        net_rental = found["net_rental"][-1] if found.get("net_rental") else None
        other_income = found["other_income"][0] if found.get("other_income") else None

        # Period — derived from an "as of" date in the top rows.
        as_of: date | None = None
        for row in rows[:10]:
            joined = " ".join(_text(c) for c in row)
            if "as of" in joined.lower():
                as_of = _find_date(row)
                break
        if as_of is None:
            for row in rows[:12]:                  # else first date anywhere
                as_of = _find_date(row)
                if as_of:
                    break

        blocks: dict[str, Any] = {
            "t12_netOperatingIncome": {
                "value": round(noi),
                "source": f"T-12 income statement ({path.name})",
            },
        }
        if as_of is not None:
            start = (as_of.replace(day=1) - timedelta(days=1)).replace(day=1)
            # 12-month trailing window ending in the as-of month
            start_year = as_of.year - 1
            start_month = as_of.month + 1
            if start_month > 12:
                start_month -= 12
                start_year += 1
            start = date(start_year, start_month, 1)
            blocks["t12_period"] = {
                "value": f"{start:%b %Y} - {as_of:%b %Y}",
            }

        income: dict[str, Any] = {"source": f"T-12 income statement ({path.name})"}
        if gpr is not None:
            income["grossPotentialRent"] = round(gpr)
        if net_rental is not None:
            income["netRentalIncome"] = round(net_rental)
        if other_income is not None:
            income["otherIncome"] = {"totalOtherIncome": round(other_income)}
        income["effectiveGrossIncome"] = round(egi)
        blocks["t12_income"] = income

        if total_opex is not None and taxes_ins is not None:
            blocks["totalOpex"] = {
                "value": round(total_opex - taxes_ins),
                "source": (
                    "T-12 operating expenses excluding taxes & insurance"
                ),
            }
            blocks["fixedCharges"] = {
                "value": round(taxes_ins),
                "source": "T-12 real estate taxes and insurance",
            }

        return ParseResult(
            kind="t12",
            blocks=blocks,
            message=(
                f"T-12 parsed — NOI ${round(noi):,}, EGI ${round(egi):,}"
            ),
        )
    return None


# ===========================================================================
# Dispatcher
# ===========================================================================


def combine_blocks(results: list[ParseResult]) -> dict[str, Any]:
    """Merge blocks from several parsed files into one dict for `sources.json`.

    When more than one file yields a `rentRoll`, the one with the newest
    effective `date` wins, so a folder holding several rent-roll snapshots
    keeps the freshest. All other block keys are last-wins.
    """
    combined: dict[str, Any] = {}
    for r in results:
        for key, block in r.blocks.items():
            if (
                key == "rentRoll"
                and key in combined
                and str(block.get("date", "")) < str(combined[key].get("date", ""))
            ):
                continue
            combined[key] = block
    return combined


def parse_uploaded_document(path: Path) -> ParseResult:
    """Detect and parse one uploaded file. Never raises — failures are
    reported as `kind="error"` so the upload itself always succeeds."""
    ext = path.suffix.lower()
    if ext not in _SPREADSHEET_EXTS:
        return ParseResult(
            kind="skipped",
            message="saved (not a spreadsheet — no auto-parse for this type)",
        )
    try:
        result = parse_t12(path) or parse_rent_roll(path)
    except Exception as exc:  # noqa: BLE001 — upload must not fail on a bad file
        return ParseResult(
            kind="error",
            message="saved — but couldn't read the data inside this file.",
        )
    if result is not None:
        return result
    return ParseResult(
        kind="unrecognized",
        message=(
            "saved — but the file layout wasn't auto-recognized, so "
            "the data fields aren't filled in yet. Open the document, "
            "then enter the key values in the property card if needed."
        ),
    )
