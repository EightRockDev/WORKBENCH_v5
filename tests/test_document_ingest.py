"""Document auto-ingestion: dedup, deterministic extraction, honest counts.

Owner report 2026-08-04: the same file uploaded several times each re-ran and
appended a "0 fields written" row, and "6 fields" vs "9 fields" on the same
document wasn't comparing like with like. These pin the three fixes:
  - file_content_hash + find_prior_ingestion  -> skip identical re-uploads
  - _count_leaves / _count_data_points         -> count data points, not keys
  - (temperature=0 on the LLM call is a code-level determinism guarantee)
"""

from __future__ import annotations

import json

import core.document_ingest as di


# --------------------------------------------------------- leaf counting

def test_null_values_are_not_counted():
    wrapped = {"value": None, "source_doc": "t12", "label": "x"}
    assert di._count_leaves(wrapped) == 0


def test_a_wrapped_scalar_counts_once():
    wrapped = {"value": 502060, "source_doc": "t12", "label": "noi"}
    assert di._count_leaves(wrapped) == 1


def test_a_nested_block_counts_its_leaves_not_one():
    # This is the 6-vs-9 bug: a whole revenue block used to count as 1.
    nested = {
        "rubsRecovery": {"value": 63012, "source_doc": "t12", "label": "r"},
        "electric": {"value": 1685, "source_doc": "t12", "label": "e"},
        "trash": {"value": 50, "source_doc": "t12", "label": "t"},
        "missing": {"value": None, "source_doc": "t12", "label": "m"},
    }
    assert di._count_leaves(nested) == 3      # three real values, null skipped


def test_rent_roll_counts_its_unit_rows():
    block = {"summary": {"totalUnits": 76}, "units": [{}] * 76}
    assert di._count_data_points("rentRoll", block) == 76


def test_a_list_field_counts_its_length():
    assert di._count_leaves([{"a": 1}, {"a": 2}]) == 2


# ------------------------------------------------------------- hashing

def test_identical_bytes_hash_identically(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"the same content")
    b.write_bytes(b"the same content")
    assert di.file_content_hash(a) == di.file_content_hash(b) != ""


def test_different_bytes_hash_differently(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"content one")
    b.write_bytes(b"content two")
    assert di.file_content_hash(a) != di.file_content_hash(b)


def test_missing_file_hashes_to_empty(tmp_path):
    # '' never matches a stored hash -> a read failure means "not a duplicate".
    assert di.file_content_hash(tmp_path / "nope.bin") == ""


# ------------------------------------------------ duplicate detection

class _FakeStore:
    def __init__(self, files):
        self.files = files

    def is_file(self, k):
        return k in self.files

    def read_text(self, k, encoding="utf-8"):
        return self.files[k]


def _patch_store(monkeypatch, log):
    files = {"KEY/sources.json": json.dumps({"_ingestion_log": log})}
    monkeypatch.setattr("core.storage.get_storage", lambda: _FakeStore(files))
    monkeypatch.setattr("data.property_io._rel", lambda p: "KEY")


def test_a_previously_ingested_file_is_found(monkeypatch, tmp_path):
    _patch_store(monkeypatch, [
        {"source_doc": "T12.xlsx", "document_type": "t12",
         "fields_written": 6, "content_hash": "abc123",
         "extracted_at": "2026-08-04T17:56:00"},
    ])
    hit = di.find_prior_ingestion(tmp_path, "abc123")
    assert hit is not None and hit["document_type"] == "t12"


def test_a_new_file_is_not_flagged_as_duplicate(monkeypatch, tmp_path):
    _patch_store(monkeypatch, [
        {"document_type": "t12", "fields_written": 6, "content_hash": "abc123"},
    ])
    assert di.find_prior_ingestion(tmp_path, "different-hash") is None


def test_a_prior_zero_field_run_does_not_block_a_retry(monkeypatch, tmp_path):
    # Only ingestions that actually wrote data should suppress a re-run.
    _patch_store(monkeypatch, [
        {"document_type": "t12", "fields_written": 0, "content_hash": "abc123"},
    ])
    assert di.find_prior_ingestion(tmp_path, "abc123") is None


def test_empty_hash_never_matches(monkeypatch, tmp_path):
    _patch_store(monkeypatch, [
        {"document_type": "t12", "fields_written": 6, "content_hash": ""},
    ])
    assert di.find_prior_ingestion(tmp_path, "") is None
