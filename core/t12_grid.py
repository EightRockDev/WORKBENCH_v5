"""Flatten period-grid income statements to an annual view.

Property-management systems (Yardi, RealPage, AppFolio and the agent-branded
exports built on them) ship a T-12 as a GRID: one row per GL line, one column
per month, and usually a trailing ``Total`` column. Dumping that grid straight
into an extraction prompt gives the model thirteen numbers per line with
nothing marking which one is the year, on a sheet that also prints a summary
block and then repeats every category at GL level further down.

Grand Hampton (Jan 2026) is the case that motivated this: the statement's own
``Total`` column reads 1,363,689 income / 989,908 opex and its nine expense
categories sum to exactly 989,910, yet extraction returned 285,532 of
expenses -- it had been reading across the monthly columns.

So the grid is resolved here, deterministically, before any model sees it
(spec section 11 keeps the core LLM-free). The rule is simple and auditable:
use the sheet's own ``Total`` column when it has one, otherwise add up the
period columns.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Sequence

# A header cell that names a period: a real date, or text like "02/28/2025",
# "Jan 2026", "Jan-26". Deliberately conservative -- a false positive here
# would flatten a sheet that is not a period grid.
_DATE_TEXT = re.compile(
    r"^\s*(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"                       # 02/28/2025
    r"|\d{4}-\d{2}(?:-\d{2})?"                             # 2025-02
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"[\s\-/]*\d{2,4}"                                     # Jan 2026 / Jan-26
    r")\s*$",
    re.IGNORECASE,
)
_TOTAL_TEXT = re.compile(r"^\s*(?:ytd\s+)?total\s*$", re.IGNORECASE)

# Below this many period columns it is a report with a couple of date fields,
# not a monthly grid. Twelve is the norm; some exports carry 6 or 13.
_MIN_PERIODS = 6


@dataclass(frozen=True)
class GridInfo:
    """Where the periods and the annual total live on a detected grid."""
    header_row: int
    label_col: int
    period_cols: tuple[int, ...]
    total_col: int | None

    @property
    def has_explicit_total(self) -> bool:
        return self.total_col is not None


def _is_period_header(v: Any) -> bool:
    if isinstance(v, (dt.date, dt.datetime)):
        return True
    return isinstance(v, str) and bool(_DATE_TEXT.match(v))


def _is_total_header(v: Any) -> bool:
    return isinstance(v, str) and bool(_TOTAL_TEXT.match(v))


def _num(v: Any) -> float | None:
    """Parse a spreadsheet cell to a float, or None.

    Handles the accounting conventions these exports use: thousands commas,
    parenthesised negatives, currency symbols, and a bare "-" for zero.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or s in {"-", "--"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def detect_period_grid(rows: Sequence[Sequence[Any]]) -> GridInfo | None:
    """Find the header row of a monthly grid. None when the sheet is not one."""
    for r, row in enumerate(rows[:40]):          # headers live near the top
        period_cols = [c for c, v in enumerate(row) if _is_period_header(v)]
        if len(period_cols) < _MIN_PERIODS:
            continue
        total_cols = [c for c, v in enumerate(row)
                      if _is_total_header(v) and c > period_cols[0]]
        # The label column is whatever sits left of the first period.
        label_col = 0
        for c in range(period_cols[0] - 1, -1, -1):
            if any(isinstance(rr[c] if c < len(rr) else None, str)
                   and str(rr[c]).strip() for rr in rows[r:r + 30]):
                label_col = c
                break
        return GridInfo(
            header_row=r,
            label_col=label_col,
            period_cols=tuple(period_cols),
            total_col=total_cols[0] if total_cols else None,
        )
    return None


def annual_value(row: Sequence[Any], info: GridInfo) -> float | None:
    """The year figure for one row: the sheet's Total column, else the sum.

    A blank Total cell on a row that does carry monthly numbers falls back to
    the sum, so a partially-filled total column cannot silently zero a line.
    """
    def cell(c: int) -> Any:
        return row[c] if c < len(row) else None

    if info.total_col is not None:
        v = _num(cell(info.total_col))
        if v is not None:
            return v
    vals = [_num(cell(c)) for c in info.period_cols]
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def flatten_to_annual(
    rows: Sequence[Sequence[Any]],
    info: GridInfo,
) -> list[tuple[str, float]]:
    """(label, annual amount) for every row of the grid that carries numbers."""
    out: list[tuple[str, float]] = []
    for row in rows[info.header_row + 1:]:
        if not row:
            continue
        raw = row[info.label_col] if info.label_col < len(row) else None
        label = str(raw).strip() if raw is not None else ""
        if not label:
            continue
        total = annual_value(row, info)
        if total is None:
            continue
        out.append((label, total))
    return out


def normalized_annual_text(rows: Sequence[Sequence[Any]],
                           sheet_name: str = "") -> str | None:
    """A two-column annual rendering of a period grid, or None if not one.

    Emitted alongside the raw grid rather than replacing it -- the monthly
    detail is still wanted for seasonality and anomaly checks; this block just
    removes the guesswork about which number is the year.
    """
    info = detect_period_grid(rows)
    if info is None:
        return None
    lines = flatten_to_annual(rows, info)
    if not lines:
        return None
    src = ("the statement's own Total column"
           if info.has_explicit_total
           else f"summing {len(info.period_cols)} period columns")
    head = (
        f"--- ANNUAL TOTALS{(': ' + sheet_name) if sheet_name else ''} ---\n"
        f"Derived from {src}. Each line is the FULL-YEAR figure -- use these "
        f"numbers, not the monthly columns in the raw grid below.\n"
    )
    body = "\n".join(f"{label}\t{amount:,.2f}" for label, amount in lines)
    return head + body + "\n"
