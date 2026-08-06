"""The owner-facing DATA-DICTIONARY.pdf must never go stale (owner directive
2026-08-07: "update that file any time we add/remove/update/change the data
dictionary"). The generator embeds a hash of the field policy + its own prose
in the PDF metadata; if either changes without a rebuild, this fails with the
exact command to run."""

from __future__ import annotations

import pathlib

from pypdf import PdfReader

_REPO = pathlib.Path(__file__).resolve().parent.parent
_PDF = _REPO / "docs" / "DATA-DICTIONARY.pdf"


def _expected_hash() -> str:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_data_dictionary_pdf",
        _REPO / "scripts" / "build_data_dictionary_pdf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.content_hash()


def test_pdf_exists_and_matches_current_policy():
    assert _PDF.exists(), (
        "docs/DATA-DICTIONARY.pdf is missing - rebuild with: "
        "uv run --with reportlab python scripts/build_data_dictionary_pdf.py")
    subject = str(PdfReader(str(_PDF)).metadata.subject or "")
    assert subject == f"policy-hash:{_expected_hash()}", (
        "docs/DATA-DICTIONARY.pdf is STALE - the field policy or the "
        "generator changed after the PDF was built. Rebuild + commit: "
        "uv run --with reportlab python scripts/build_data_dictionary_pdf.py")
