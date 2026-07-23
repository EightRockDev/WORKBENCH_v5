"""Tests for data.parsers — upload document parsing into sources.json blocks."""

from __future__ import annotations

import openpyxl
import pytest

from data.parsers import (
    ParseResult,
    combine_blocks,
    parse_uploaded_document,
)


def _write_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)


# ---------------------------------------------------------------------------
# Rent roll
# ---------------------------------------------------------------------------

def test_parse_rent_roll(tmp_path):
    path = tmp_path / "rent_roll.xlsx"
    _write_xlsx(path, [
        ["RENT ROLL DETAIL"],
        ["As of Date: 05/06/2026"],
        ["Bldg/Unit", "Floorplan", "SQFT", "Unit/Lease Status", "Name",
         "Move-In", "Move-Out", "Lease End", "Market", "RENT",
         "Total Billing", "MTOM"],
        ["1-1", "2B", 1000, "Occupied", "Tenant A",
         "01/01/2024", "", "12/31/2026", 1200, 1100, 1100, 0],
        ["1-2", "2B", 1000, "Occupied-NTV", "Tenant B",
         "01/01/2023", "06/30/2026", "05/31/2026", 1200, 1050, 1050, 0],
        ["1-3", "1B", 800, "Vacant", "VACANT",
         "", "", "", 1000, 0, 0, 0],
        ["1-3", "1B", 800, "Former resident", "Old Tenant",
         "", "", "", 0, 950, 950, 0],  # secondary row — must be dropped
        ["2-1", "1B", 800, "Occupied", "Tenant C",
         "02/01/2025", "", "01/31/2027", 1000, 1000, 1000, 25],
    ])
    result = parse_uploaded_document(path)

    assert result.kind == "rent_roll"
    rr = result.blocks["rentRoll"]
    assert rr["date"] == "2026-05-06"
    assert rr["file"] == "rent_roll.xlsx"

    s = rr["summary"]
    assert s["totalUnits"] == 4              # 1-3 deduped to one physical unit
    assert s["occupied"] == 2                # 1-1, 2-1
    assert s["notice"] == 1                  # 1-2 (NTV)
    assert s["vacant"] == 1                  # 1-3
    assert s["occupancyPct"] == 75.0
    assert s["totalMarketRent"] == 4400      # 1200+1200+1000+1000
    assert s["totalActualRent"] == 3150      # 1100+1050+1000 (excl. vacant)
    assert s["avgSqft"] == 900

    assert len(rr["units"]) == 4
    by_unit = {u["unit"]: u for u in rr["units"]}
    assert by_unit["2-1"]["isMTM"] is True   # MTOM charge > 0
    assert by_unit["1-2"]["status"] == "notice"
    assert by_unit["1-3"]["status"] == "vacant"


# ---------------------------------------------------------------------------
# T-12
# ---------------------------------------------------------------------------

def test_parse_t12(tmp_path):
    path = tmp_path / "t12.xlsx"
    _write_xlsx(path, [
        ["12 Month Income Statement"],
        ["As of Date: 04/30/2026"],
        ["", "Total"],
        ["Gross Potential Rent", 2000000],
        ["Total Non-Rental Income", 200000],
        ["Total Income", 2200000],
        ["Net Rental Income", 1800000],
        ["Total Taxes & Insurance", 200000],
        ["Total Operating Expenses", 800000],
        ["Net Operating Income", 1400000],
    ])
    result = parse_uploaded_document(path)

    assert result.kind == "t12"
    b = result.blocks
    assert b["t12_netOperatingIncome"]["value"] == 1400000
    assert b["t12_period"]["value"] == "May 2025 - Apr 2026"
    assert b["t12_income"]["effectiveGrossIncome"] == 2200000
    assert b["t12_income"]["otherIncome"]["totalOtherIncome"] == 200000
    # operating expenses split: variable = total - taxes&insurance
    assert b["totalOpex"]["value"] == 600000
    assert b["fixedCharges"]["value"] == 200000
    # the split must reconcile back to NOI
    egi = b["t12_income"]["effectiveGrossIncome"]
    opex = b["totalOpex"]["value"] + b["fixedCharges"]["value"]
    assert egi - opex == b["t12_netOperatingIncome"]["value"]


# ---------------------------------------------------------------------------
# Graceful fallback
# ---------------------------------------------------------------------------

def test_unrecognized_spreadsheet(tmp_path):
    path = tmp_path / "random.xlsx"
    _write_xlsx(path, [["Foo", "Bar", "Baz"], [1, 2, 3]])
    result = parse_uploaded_document(path)
    assert result.kind == "unrecognized"
    assert result.blocks == {}


def test_non_spreadsheet_skipped(tmp_path):
    path = tmp_path / "offering.pdf"
    path.write_bytes(b"%PDF-1.7 not really a pdf")
    result = parse_uploaded_document(path)
    assert result.kind == "skipped"
    assert result.blocks == {}


# ---------------------------------------------------------------------------
# combine_blocks — newest rent roll wins
# ---------------------------------------------------------------------------

def test_combine_blocks_keeps_newest_rent_roll():
    old = ParseResult("rent_roll", {"rentRoll": {"date": "2026-05-01", "tag": "old"}})
    new = ParseResult("rent_roll", {"rentRoll": {"date": "2026-05-06", "tag": "new"}})
    # order should not matter — newest date wins either way
    assert combine_blocks([old, new])["rentRoll"]["tag"] == "new"
    assert combine_blocks([new, old])["rentRoll"]["tag"] == "new"


def test_combine_blocks_merges_distinct_keys():
    rr = ParseResult("rent_roll", {"rentRoll": {"date": "2026-05-06"}})
    t12 = ParseResult("t12", {"t12_netOperatingIncome": {"value": 1}})
    combined = combine_blocks([rr, t12])
    assert set(combined) == {"rentRoll", "t12_netOperatingIncome"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
