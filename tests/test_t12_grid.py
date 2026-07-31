"""Period-grid flattening (core.t12_grid).

The fixture mirrors the SHAPE of the Franklin Group / Yardi export that
motivated this module -- title block, a header row of twelve month-end dates
plus a Total column, a summary section, then a detail section repeating the
same categories -- with invented numbers. The owner's actual statements are
deal data and do not belong in the repo.
"""

from __future__ import annotations

import datetime as dt

from core.t12_grid import (
    GridInfo,
    annual_value,
    detect_period_grid,
    flatten_to_annual,
    normalized_annual_text,
)

_MONTHS = ["02/28/2025", "03/31/2025", "04/30/2025", "05/31/2025",
           "06/30/2025", "07/31/2025", "08/31/2025", "09/30/2025",
           "10/31/2025", "11/30/2025", "12/31/2025", "01/31/2026"]


def _grid(total_col: bool = True):
    """Twelve months of 100 (income) / 40 (an expense line)."""
    header = [" "] + _MONTHS + (["Total"] if total_col else [])
    def row(label, v, total=None):
        r = [label] + [v] * 12
        if total_col:
            r.append(total if total is not None else v * 12)
        return r
    return [
        ["Grand Example", "", ""],
        ["12 Month Income Statement", "", ""],
        ["As of Date:", "01/31/2026", ""],
        [" ", "", ""],
        header,
        ["  TOTAL INCOME"] + [100] * 12 + ([1200] if total_col else []),
        ["  EXPENSE SUMMARIES"] + ["  "] * 12 + (["  "] if total_col else []),
        row("    Utilities", 40),
        row("    Personnel Costs", 25),
        ["  OPERATING EXPENSE"] + [65] * 12 + ([780] if total_col else []),
        ["  NET OPERATING INCOME"] + [35] * 12 + ([420] if total_col else []),
    ]


def test_detects_the_header_period_and_total_columns():
    info = detect_period_grid(_grid())
    assert info is not None
    assert info.header_row == 4
    assert info.label_col == 0
    assert len(info.period_cols) == 12
    assert info.total_col == 13
    assert info.has_explicit_total


def test_prefers_the_statements_own_total_over_re_adding():
    """If the sheet prints a Total, that is the number the owner reconciles to
    -- re-deriving it invites a rounding argument with their accountant."""
    rows = _grid()
    # A Total that deliberately disagrees with the monthly sum.
    rows[5] = ["  TOTAL INCOME"] + [100] * 12 + [9999]
    info = detect_period_grid(rows)
    assert annual_value(rows[5], info) == 9999


def test_falls_back_to_summing_periods_without_a_total_column():
    rows = _grid(total_col=False)
    info = detect_period_grid(rows)
    assert info is not None and info.total_col is None
    assert annual_value(rows[5], info) == 1200


def test_blank_total_cell_falls_back_instead_of_zeroing_the_line():
    rows = _grid()
    rows[7] = ["    Utilities"] + [40] * 12 + [None]
    info = detect_period_grid(rows)
    assert annual_value(rows[7], info) == 480


def test_expense_lines_tie_to_the_printed_operating_expense():
    """The tie-out the extractor was failing: categories must sum to the total."""
    rows = _grid()
    lines = dict(flatten_to_annual(rows, detect_period_grid(rows)))
    cats = lines["Utilities"] + lines["Personnel Costs"]
    assert cats == lines["OPERATING EXPENSE"] == 780


def test_real_datetime_headers_are_recognised():
    """openpyxl hands back datetimes when the export stores real dates."""
    rows = _grid()
    rows[4] = [" "] + [dt.datetime(2025, m, 28) for m in range(1, 13)] + ["Total"]
    info = detect_period_grid(rows)
    assert info is not None and len(info.period_cols) == 12


def test_accounting_formats_parse():
    rows = _grid()
    rows[7] = ["    Utilities"] + ["(1,234.50)"] * 12 + ["($14,814.00)"]
    info = detect_period_grid(rows)
    assert annual_value(rows[7], info) == -14814.0


def test_a_plain_sheet_is_left_alone():
    """A rent roll is not a period grid -- flattening one would be wrong."""
    rent_roll = [
        ["Unit", "Tenant", "Rent", "Lease End"],
        ["101", "Smith", 1200, "2026-06-30"],
        ["102", "Jones", 1250, "2026-08-31"],
    ]
    assert detect_period_grid(rent_roll) is None
    assert normalized_annual_text(rent_roll) is None


def test_two_date_columns_are_not_a_grid():
    """Guard the threshold: a report with a start/end date must not flatten."""
    sheet = [["Report", "01/01/2025", "12/31/2025"], ["Revenue", 10, 20]]
    assert detect_period_grid(sheet) is None


def test_normalized_text_labels_its_source_and_leads_with_guidance():
    txt = normalized_annual_text(_grid(), "Sheet1")
    assert txt is not None
    assert "ANNUAL TOTALS: Sheet1" in txt
    assert "the statement's own Total column" in txt
    assert "TOTAL INCOME\t1,200.00" in txt

    summed = normalized_annual_text(_grid(total_col=False), "Sheet1")
    assert "summing 12 period columns" in summed


# ---------------------------------------------------------------------------
# Extraction prompt must carry the grid rules (the flattener is only half)
# ---------------------------------------------------------------------------

def test_t12_prompt_tells_the_model_how_to_read_a_grid():
    from core.document_ingest import _T12_PROMPT

    assert "ANNUAL TOTALS" in _T12_PROMPT
    assert "Do not add up monthly columns" in _T12_PROMPT
    # summary + GL detail duplication
    assert "ONCE" in _T12_PROMPT
    # combined lines must not be dropped -- the main cause of a partial set
    assert "Taxes & Insurance" in _T12_PROMPT
    # below-the-line exclusions
    for below in ("debt service", "capital"):
        assert below in _T12_PROMPT.lower()
    # self-check before returning
    assert "extraction_notes" in _T12_PROMPT
