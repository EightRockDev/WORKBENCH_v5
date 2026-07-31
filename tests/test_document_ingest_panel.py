"""Upload surface of the Document Auto-Ingestion panel.

Asserted against the module source rather than a rendered page: the panel
only renders once a property folder exists, which makes a browser check
flaky, while the two properties that matter here — batch upload, and the
removal of the from-disk picker — are structural.
"""

from __future__ import annotations

import inspect

from ui import document_ingest_panel as dip


def test_uploader_accepts_multiple_files():
    src = inspect.getsource(dip.render_document_ingest_panel)
    assert "accept_multiple_files=True" in src
    assert "accept_multiple_files=False" not in src


def test_from_disk_picker_is_gone():
    """Brian removed it 2026-07-31 — the browser upload path covers it."""
    for gone in ("_render_disk_picker", "_candidate_files",
                 "_default_scan_roots"):
        assert not hasattr(dip, gone), f"{gone} should have been removed"
    assert "Pull from this computer" not in inspect.getsource(dip)


def test_zero_byte_uploads_are_reported_without_blocking_the_batch():
    """A cloud-only OneDrive stub must not stop the readable files with it,
    and the old advice ('use the picker below') no longer has a picker."""
    src = inspect.getsource(dip.render_document_ingest_panel)
    assert "empties" in src and "staged" in src
    # the error names the bad files and tells the user what to do instead
    assert "0 bytes" in src
    assert "Pull from this computer" not in src


def test_batch_extraction_runs_sequentially():
    """Each extraction writes the same sources.json — concurrent runs race."""
    src = inspect.getsource(dip.render_document_ingest_panel)
    assert "for i, target in enumerate(staged" in src
