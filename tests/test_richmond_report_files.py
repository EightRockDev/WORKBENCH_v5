"""Section 2c of the Richmond review: per-workbook column inventory.

The report is the only eye we have on the host database, so the section
that says WHICH rva.gov workbooks landed (and names their unit column)
has to be right before the host ever runs it - the 2026-09-03 fix hinges
on this readout."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "run_richmond_report", ROOT / "scripts" / "run_richmond_report.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["run_richmond_report"] = m
    spec.loader.exec_module(m)
    return m


def _db_with_files(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE muni_records (market TEXT, state TEXT, county TEXT,"
        " kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)")
    for rec in rows:
        conn.execute(
            "INSERT INTO muni_records VALUES ('Richmond','VA','Richmond',"
            "'assessor','files:rva.gov/assessor-real-estate',"
            "'2026-09-03T04:00:00',?)", (json.dumps(rec),))
    return conn


def test_inventory_lists_every_file_and_flags_unit_columns(capsys):
    m = _mod()
    conn = _db_with_files([
        {"_file": "https://rva.gov/f/Parcels_0826.xlsx",
         "PIN": "C0010124002", "TotalValue": 100},
        {"_file": "https://rva.gov/f/Buildings_0826.xlsx",
         "PIN": "C0010124002", "NumberOfUnits": 48, "Stories": 3},
        {"_file": "https://rva.gov/f/Buildings_0826.xlsx",
         "PIN": "C0010124003", "NumberOfUnits": 12},
    ])
    gaps: list[str] = []
    m._files_column_inventory(conn, "files:rva.gov/assessor-real-estate",
                              gaps)
    out = capsys.readouterr().out
    assert "Parcels_0826.xlsx: 1 rows" in out
    assert "Buildings_0826.xlsx: 2 rows" in out
    assert "[unit-suspect] NumberOfUnits" in out
    assert "sample: 48" in out
    # 2 distinct files on hand -> no "only N workbooks" gap
    assert not gaps


def test_inventory_gaps_when_only_one_workbook_landed(capsys):
    m = _mod()
    conn = _db_with_files([
        {"_file": "https://rva.gov/f/Parcels_0826.xlsx",
         "PIN": "C0010124002", "TotalValue": 100},
    ])
    gaps: list[str] = []
    m._files_column_inventory(conn, "files:rva.gov/assessor-real-estate",
                              gaps)
    out = capsys.readouterr().out
    assert "Parcels_0826.xlsx" in out
    assert len(gaps) == 1 and "3-file set" in gaps[0]


def test_unitish_flags_the_plausible_names_only():
    m = _mod()
    hits = [c for c in ("NumberOfUnits", "DwellingUnits", "Stories",
                        "ImprovementValue", "BldgType", "ApartmentCount")
            if m._UNITISH.search(c)]
    assert len(hits) == 6
    misses = [c for c in ("PIN", "TotalValue", "OwnerName", "Zip")
              if m._UNITISH.search(c)]
    assert not misses
