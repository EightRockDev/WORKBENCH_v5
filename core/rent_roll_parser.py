"""Deterministic XLSX/CSV parsing for rent rolls and T-12s (spec 11).

An Excel rent roll is structured data - running it through a language model
adds cost, latency, an API-key requirement, and a failure mode, for a task a
parser does perfectly. This module is the LLM-free path `ingest_document`
tries FIRST for tabular files; the model remains the fallback for PDFs and
layouts the parser can't recognize.

Design constraints:
  * **Never guess.** If the header row can't be identified, or a T-12 label is
    ambiguous, return nothing and let the caller fall back to AI (or tell the
    user). A wrong number silently written into sources.json is far worse than
    "could not parse".
  * Emit exactly the shapes the workbench already reads: the `rentRoll` block
    (`ui/rent_roll.py`) and the flat T-12 keys (`core/extraction_qa.py`).
  * Everything gets `confidence` 0.98 - deterministic, but a rent roll can
    still contain typos, and the extraction-QA gate should keep running.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

CONFIDENCE = 0.98

# ---------------------------------------------------------------------------
# Header recognition - maps many real-world column spellings to our schema.
# Matched on a normalized form: lowercase, alphanumerics only.
# ---------------------------------------------------------------------------

_RR_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "unit":        ("unit", "unitno", "unitnumber", "unitid", "apt", "aptno"),
    "unitType":    ("unittype", "floorplan", "type", "plan", "bedbath", "brba"),
    "status":      ("status", "occupancy", "occstatus", "unitstatus", "leasestatus"),
    "tenant":      ("tenant", "resident", "residentname", "tenantname", "name",
                    "lease", "leaseholder"),
    "sqft":        ("sqft", "sf", "squarefeet", "squarefootage", "area", "size",
                    "unitsqft"),
    "marketRent":  ("marketrent", "market", "marketrate", "askingrent",
                    "scheduledrent", "grosspotential", "potentialrent"),
    "actualRent":  ("actualrent", "currentrent", "leaserent", "rent",
                    "rentamount", "monthlyrent", "contractrent", "inplacerent"),
    "totalCharges": ("totalcharges", "totalbilling", "totalmonthly", "total",
                     "totalcharge", "grosscharges"),
    "moveIn":      ("movein", "moveindate", "mvin"),
    "leaseExp":    ("leaseexp", "leaseexpiration", "leaseend", "leaseenddate",
                    "expiration", "leaseto", "leexpiration"),
    "moveOut":     ("moveout", "moveoutdate", "noticedate", "mvout"),
}

_MIN_HEADER_HITS = 3          # a row is "the header" when >=3 columns match
_STATUS_OCCUPIED = ("occupied", "current", "o")
_STATUS_VACANT = ("vacant", "vacantunrented", "vacantrented", "v", "empty",
                  "available", "vacantready", "vacantnotready")
_STATUS_NOTICE = ("notice", "onnotice", "noticeunrented", "noticerented", "n")


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value is not None else ""


def _to_number(value: Any) -> float | None:
    """Parse $1,234.56 / (123) / 1234 -> float. None when not a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s or s in ("-", "--"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def _to_date_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d-%b-%Y",
                "%b %d, %Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _load_rows(path: Path) -> list[list[Any]]:
    """All rows of the first data-bearing sheet (xlsx) or the csv."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        import csv
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            return [row for row in csv.reader(fh)]
    if suffix in (".xlsx", ".xlsm", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        best: list[list[Any]] = []
        for sheet in wb.worksheets:
            rows = [list(r) for r in sheet.iter_rows(values_only=True)]
            if _find_header(rows) is not None:
                return rows          # first sheet with a recognizable header
            if len(rows) > len(best):
                best = rows
        return best
    return []


def _find_header(rows: list[list[Any]]) -> tuple[int, dict[int, str]] | None:
    """Locate the header row; return (row_index, {column_index: schema_key})."""
    for idx, row in enumerate(rows[:40]):
        mapping: dict[int, str] = {}
        for col, cell in enumerate(row):
            token = _norm(cell)
            if not token:
                continue
            for key, aliases in _RR_HEADER_ALIASES.items():
                if token in aliases and key not in mapping.values():
                    mapping[col] = key
                    break
        # `unit` plus at least two others = a credible rent-roll header.
        if len(mapping) >= _MIN_HEADER_HITS and "unit" in mapping.values():
            return idx, mapping
    return None


def _classify_status(raw: Any, tenant: Any) -> str:
    token = _norm(raw)
    if token:
        if token in _STATUS_NOTICE or token.startswith("notice"):
            return "Notice"
        if token in _STATUS_VACANT or token.startswith("vacant"):
            return "Vacant"
        if token in _STATUS_OCCUPIED or token.startswith(("occupied", "current")):
            return "Occupied"
    # No status column: infer from tenant presence.
    tenant_s = str(tenant).strip() if tenant is not None else ""
    if tenant_s and _norm(tenant_s) not in ("vacant", ""):
        return "Occupied"
    return "Vacant"


def parse_rent_roll(path: Path) -> dict[str, Any] | None:
    """Parse a tabular rent roll into the workbench's `rentRoll` block.

    Returns None when the file doesn't look like a rent roll (no recognizable
    header row) - the caller decides whether to fall back to AI.
    """
    rows = _load_rows(path)
    found = _find_header(rows)
    if found is None:
        return None
    header_idx, colmap = found

    units: list[dict[str, Any]] = []
    for row in rows[header_idx + 1:]:
        record: dict[str, Any] = {}
        for col, key in colmap.items():
            if col < len(row):
                record[key] = row[col]
        unit_label = str(record.get("unit") or "").strip()
        # Stop conditions: total/summary rows, blank tails.
        if not unit_label or _norm(unit_label) in ("total", "totals", "grandtotal",
                                                   "summary", "average", "averages"):
            continue
        # A "unit" that parses as a big number with no other data is a stray
        # total row; a real unit label is short.
        has_data = any(record.get(k) is not None
                       for k in ("marketRent", "actualRent", "sqft", "tenant",
                                 "status"))
        if not has_data:
            continue
        status = _classify_status(record.get("status"), record.get("tenant"))
        units.append({
            "unit": unit_label,
            "unitType": (str(record["unitType"]).strip()
                         if record.get("unitType") is not None else None),
            "status": status,
            "tenant": (str(record["tenant"]).strip()
                       if record.get("tenant") is not None else None),
            "sqft": _to_number(record.get("sqft")),
            "marketRent": _to_number(record.get("marketRent")),
            "actualRent": _to_number(record.get("actualRent")),
            "totalCharges": _to_number(record.get("totalCharges")),
            "isMTM": False,
            "moveIn": _to_date_str(record.get("moveIn")),
            "leaseExp": _to_date_str(record.get("leaseExp")),
            "moveOut": _to_date_str(record.get("moveOut")),
        })

    if not units:
        return None

    occupied = sum(1 for u in units if u["status"] == "Occupied")
    notice = sum(1 for u in units if u["status"] == "Notice")
    vacant = sum(1 for u in units if u["status"] == "Vacant")
    total = len(units)
    market_total = sum(u["marketRent"] or 0.0 for u in units)
    actual_total = sum(u["actualRent"] or 0.0
                       for u in units if u["status"] != "Vacant")
    sqfts = [u["sqft"] for u in units if u["sqft"]]

    return {
        "file": path.name,
        "date": dt.date.today().isoformat(),
        "summary": {
            "totalUnits": total,
            "occupied": occupied,
            "notice": notice,
            "vacant": vacant,
            "occupiedUnits": occupied + notice,   # notice tenants still pay
            "occupancyPct": round((total - vacant) / total, 4) if total else None,
            "totalMarketRent": market_total or None,
            "totalActualRent": actual_total or None,
            "avgSqft": round(sum(sqfts) / len(sqfts)) if sqfts else None,
        },
        "units": units,
    }


# ---------------------------------------------------------------------------
# T-12 (label-matching; conservative)
# ---------------------------------------------------------------------------

# schema key -> label aliases (normalized). Only unambiguous labels included.
_T12_LABELS: dict[str, tuple[str, ...]] = {
    "grossPotentialRent": ("grosspotentialrent", "gpr", "grosspotentialincome",
                           "scheduledgrossrent", "grossscheduledrent",
                           "potentialrent", "marketrent"),
    "vacancy":            ("vacancy", "vacancyloss", "vacancies"),
    "concessions":        ("concessions", "concessionloss"),
    "badDebt":            ("baddebt", "baddebtwriteoff", "collectionloss"),
    "otherIncome":        ("otherincome", "totalotherincome", "ancillaryincome",
                           "miscincome", "miscellaneousincome"),
    "totalRevenue":       ("totalrevenue", "totalincome", "effectivegrossincome",
                           "egi", "totaloperatingincome", "grossoperatingincome"),
    "realEstateTaxes":    ("realestatetaxes", "propertytaxes", "retaxes", "taxes"),
    "insurance":          ("insurance", "propertyinsurance"),
    "payroll":            ("payroll", "salariespayroll", "payrollbenefits",
                           "salariesandwages"),
    "marketing":          ("marketing", "advertising", "advertisingmarketing"),
    "repairsMaintenance": ("repairsmaintenance", "repairsandmaintenance",
                           "maintenancerepairs", "rm"),
    "utilities":          ("utilities", "totalutilities"),
    "managementFee":      ("managementfee", "managementfees", "propertymanagement",
                           "mgmtfee"),
    "contractServices":   ("contractservices", "contractedservices"),
    "administrative":     ("administrative", "adminexpenses", "generaladmin",
                           "generaladministrative", "gna"),
    "totalOpex":          ("totaloperatingexpenses", "totalexpenses", "totalopex",
                           "operatingexpenses"),
    "noi":                ("netoperatingincome", "noi"),
}

_REVENUE_KEYS = ("grossPotentialRent", "vacancy", "concessions", "badDebt",
                 "otherIncome")
_EXPENSE_KEYS = ("payroll", "marketing", "repairsMaintenance", "utilities",
                 "managementFee", "contractServices", "administrative")
_FIXED_KEYS = ("realEstateTaxes", "insurance")


def parse_t12(path: Path) -> dict[str, Any] | None:
    """Parse a tabular T-12 by matching row labels.

    Takes the LAST numeric cell of each matched row - T-12 layouts put the
    12-month total in the rightmost column after the monthly columns. Returns
    None unless at least the revenue/expense/NOI backbone is found, so a
    random spreadsheet never yields a half-parsed statement.
    """
    rows = _load_rows(path)
    if not rows:
        return None

    values: dict[str, float] = {}
    for row in rows:
        if not row:
            continue
        label = _norm(row[0])
        if not label:
            continue
        for key, aliases in _T12_LABELS.items():
            if key in values:
                continue
            if label in aliases:
                numbers = [n for c in row[1:] if (n := _to_number(c)) is not None]
                if numbers:
                    values[key] = numbers[-1]
                break

    # Backbone requirement: revenue AND (opex or NOI). Anything less is not
    # a statement we should write into the workbench.
    if "totalRevenue" not in values or not ({"totalOpex", "noi"} & values.keys()):
        return None

    # Loss lines are stored as positive losses (ingest convention).
    for key in ("vacancy", "concessions", "badDebt"):
        if key in values:
            values[key] = abs(values[key])

    out: dict[str, Any] = {}
    for key in ("totalRevenue", "totalOpex", "noi"):
        if key in values:
            out[key] = values[key]
    rev = {k: values[k] for k in _REVENUE_KEYS if k in values}
    if rev:
        out["t12_revenue"] = rev
    exp = {k: values[k] for k in _EXPENSE_KEYS if k in values}
    if exp:
        out["t12_expenses"] = exp
    fixed = {k: values[k] for k in _FIXED_KEYS if k in values}
    if fixed:
        out["t12_fixedCharges"] = fixed
    return out
