"""Tests for core.ic_memo_validator — deterministic sub-validators + end-to-end."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core import ic_memo_validator as v
from core.due_diligence import bootstrap_default_state, save_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_docx(path: Path, body: str) -> Path:
    """Write a minimal .docx containing the given paragraphs (one per line)."""
    from docx import Document
    doc = Document()
    for line in body.splitlines():
        doc.add_paragraph(line)
    doc.save(str(path))
    return path


def _briefing(
    *,
    purchase_price: float = 5_250_000.0,
    loan_amount: float = 3_675_000.0,
    equity_raise: float = 1_575_000.0,
    noi_in_place: float = 225_536.0,
    stabilized_noi: float = 287_046.0,
    year1_gpr: float = 424_420.0,
    annual_debt_service_year_1: float = 252_000.0,
    cap_rate_going_in: float = 0.043,
    dscr_year_1: float = 0.89,
    cash_on_cash_year_1: float = -0.05,
    price_per_unit: float = 201_923.0,
    city: str = "Norfolk",
) -> dict[str, Any]:
    """Synthetic briefing in the shape `core.artifact_engine._build_briefing` produces."""
    return {
        "property": {"name": "Test", "city": city, "units": 26},
        "deal_dials": {
            "purchase_price": purchase_price,
            "loan_amount": loan_amount,
            "equity_raise": equity_raise,
            "noi_in_place": noi_in_place,
        },
        "metrics": {
            "stabilized_noi": stabilized_noi,
            "year1_gpr": year1_gpr,
            "annual_debt_service_year_1": annual_debt_service_year_1,
            "cap_rate_going_in": cap_rate_going_in,
            "dscr_year_1": dscr_year_1,
            "cash_on_cash_year_1": cash_on_cash_year_1,
            "price_per_unit": price_per_unit,
        },
    }


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

class TestExtractDocxText:
    def test_extracts_paragraphs(self, tmp_path: Path):
        p = _write_docx(tmp_path / "m.docx", "Line one\nLine two")
        text = v.extract_docx_text(p)
        assert "Line one" in text
        assert "Line two" in text

    def test_missing_file_returns_empty(self, tmp_path: Path):
        text = v.extract_docx_text(tmp_path / "nope.docx")
        assert text == ""

    def test_not_a_docx_returns_empty(self, tmp_path: Path):
        p = tmp_path / "junk.docx"
        p.write_text("not a docx", encoding="utf-8")
        text = v.extract_docx_text(p)
        assert text == ""


# ---------------------------------------------------------------------------
# Section coverage
# ---------------------------------------------------------------------------

class TestCheckSections:
    def test_all_required_sections_present_returns_empty(self):
        text = (
            "Recommendation: GO\n"
            "Purchase Price: $5,250,000\n"
            "Key Metrics overview\n"
            "Rationale: clears all bars\n"
            "Risks: insurance escalation in HR\n"
        )
        findings = v._check_sections(text, "executive_summary")
        assert findings == []

    def test_alternative_section_name_counts(self):
        # "Verdict" is an alternative for "Recommendation"; "Asking Price" for "Purchase Price"
        text = (
            "Verdict: WATCH\n"
            "Asking Price: $5,000,000\n"
            "Headline Metrics\n"
            "Thesis: value-add play\n"
            "Key Risks: insurance\n"
        )
        findings = v._check_sections(text, "executive_summary")
        assert findings == []

    def test_missing_section_produces_critical(self):
        text = "Recommendation: GO\nKey Metrics shown\nRationale present\n"
        findings = v._check_sections(text, "executive_summary")
        titles = [f.title for f in findings]
        assert any("Purchase Price" in t for t in titles)
        assert any("Risks" in t for t in titles)
        assert all(f.severity == "critical" for f in findings)

    def test_unknown_artifact_type_returns_empty(self):
        findings = v._check_sections("anything", "unknown_type")
        assert findings == []


# ---------------------------------------------------------------------------
# Numeric cross-reference
# ---------------------------------------------------------------------------

class TestCheckNumbers:
    def test_all_canonical_figures_cited_returns_empty(self):
        text = (
            "Purchase price $5,250,000. "
            "Loan $3,675,000. Equity $1,575,000. "
            "NOI $225,536. Stabilized NOI $287,046. "
            "Year-1 GPR $424,420. Debt service $252,000."
        )
        findings = v._check_numbers(text, _briefing())
        assert findings == []

    def test_within_tolerance_passes(self):
        # Purchase price $5,250,000 ± 5% → $5,000,000 should still pass
        text = "Purchase price $5,000,000. Loan $3,675,000. Equity $1,575,000. "
        text += "NOI $225,536. Stabilized NOI $287,046. GPR $424,420. Debt $252,000."
        findings = v._check_numbers(text, _briefing())
        # Purchase $5M is within 5% of $5.25M; all others exact.
        assert not any("Purchase price" in f.title for f in findings)

    def test_missing_figure_produces_warning(self):
        text = "Loan $3,675,000. Equity $1,575,000. NOI $225,536. "
        text += "Stabilized NOI $287,046. GPR $424,420. Debt $252,000."
        findings = v._check_numbers(text, _briefing())
        titles = [f.title for f in findings]
        assert any("Purchase price" in t for t in titles)
        assert all(f.severity == "warning" for f in findings)

    def test_briefing_without_metrics_does_not_crash(self):
        text = "Some memo text."
        findings = v._check_numbers(text, {"deal_dials": {}, "metrics": {}})
        assert findings == []


# ---------------------------------------------------------------------------
# Verdict consistency
# ---------------------------------------------------------------------------

class TestCheckVerdictConsistency:
    def test_matching_verdict_returns_empty(self):
        # Briefing defaults produce NO-GO (cap 4.3%, dscr 0.89, coc -5%).
        text = "Recommendation: NO-GO due to thin coverage."
        findings = v._check_verdict_consistency(text, _briefing())
        assert findings == []

    def test_missing_verdict_produces_critical(self):
        text = "Some memo with no recommendation word at all."
        findings = v._check_verdict_consistency(text, _briefing())
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert "No GO/WATCH/NO-GO" in findings[0].title

    def test_disagreeing_verdict_produces_critical(self):
        # Briefing produces NO-GO; memo claims GO.
        text = "Recommendation: GO — clears all bars."
        findings = v._check_verdict_consistency(text, _briefing())
        assert any(
            f.severity == "critical" and "disagrees" in f.title
            for f in findings
        )

    def test_does_not_match_go_inside_nogo(self):
        # Make sure "GO" doesn't false-positive on "NO-GO".
        text = "Recommendation: NO-GO due to thin coverage."
        findings = v._check_verdict_consistency(text, _briefing())
        # Briefing actual verdict is NO-GO; memo says NO-GO; no disagreement.
        assert not any("disagrees" in f.title for f in findings)


# ---------------------------------------------------------------------------
# DD gate
# ---------------------------------------------------------------------------

class TestCheckDDGate:
    def test_no_dd_state_returns_info(self):
        findings = v._check_dd_gate("any text", dd_state=None)
        assert len(findings) == 1
        assert findings[0].severity == "info"
        assert "No DD state" in findings[0].title

    def test_memo_claims_ic_ready_but_gate_open(self, tmp_path: Path):
        state = bootstrap_default_state("Test")  # all items pending → gate open
        save_state(tmp_path, state)
        from core.due_diligence import load_state
        loaded = load_state(tmp_path)
        text = "This deal is IC-ready and ready for investment committee."
        findings = v._check_dd_gate(text, loaded)
        # Should produce a critical finding
        assert any(
            f.severity == "critical" and "DD gate is open" in f.title
            for f in findings
        )


# ---------------------------------------------------------------------------
# Threshold citations
# ---------------------------------------------------------------------------

class TestCheckThresholdCitations:
    def test_no_threshold_cites_returns_empty(self):
        text = "Just some prose with no threshold callouts."
        findings = v._check_threshold_citations(text)
        # Calibration may not init in test; either way no flags.
        assert all(f.category == "calibration" for f in findings)
        # The function returns [] when no GO cap is cited; bare empty acceptable
        # if calibration not initialized
        assert findings == []

    def test_matching_threshold_cite_returns_empty(self, monkeypatch):
        # Stub calibration so we have a deterministic value: GO_CAP = 7.50%
        from core import calibration as cal

        class StubThreshold:
            def __init__(self, name, value, source="floor"):
                self.name = name
                self.effective_value = value
                self.effective_source = source

        monkeypatch.setattr(
            cal, "get_all_thresholds",
            lambda: [StubThreshold("GO_CAP", 0.075)],
        )
        text = "Clears the GO cap of 7.50% with room to spare."
        findings = v._check_threshold_citations(text)
        assert findings == []

    def test_stale_threshold_cite_produces_warning(self, monkeypatch):
        from core import calibration as cal

        class StubThreshold:
            def __init__(self, name, value, source="market"):
                self.name = name
                self.effective_value = value
                self.effective_source = source

        # Current calibrated GO_CAP is 7.85%, memo cites 7.50% (stale).
        monkeypatch.setattr(
            cal, "get_all_thresholds",
            lambda: [StubThreshold("GO_CAP", 0.0785)],
        )
        text = "Clears the GO cap of 7.50% (stale)."
        findings = v._check_threshold_citations(text)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "GO cap 7.50%" in findings[0].title


# ---------------------------------------------------------------------------
# End-to-end validate()
# ---------------------------------------------------------------------------

class TestValidate:
    def test_missing_artifact_returns_critical(self, tmp_path: Path):
        report = v.validate(
            artifact_path=tmp_path / "nope.docx",
            artifact_type="executive_summary",
            briefing=_briefing(),
        )
        assert not report.overall_ready
        assert any(f.category == "extraction" for f in report.critical)

    def test_empty_artifact_returns_critical(self, tmp_path: Path):
        p = tmp_path / "empty.docx"
        p.write_text("not real docx", encoding="utf-8")
        report = v.validate(
            artifact_path=p,
            artifact_type="executive_summary",
            briefing=_briefing(),
        )
        assert not report.overall_ready
        assert any("Empty" in f.title or "not found" in f.title.lower() for f in report.critical)

    def test_clean_memo_passes(self, tmp_path: Path):
        body = (
            "Recommendation: NO-GO\n"
            "Purchase price: $5,250,000\n"
            "Loan amount: $3,675,000\n"
            "Equity raise: $1,575,000\n"
            "NOI in-place: $225,536\n"
            "Stabilized NOI: $287,046\n"
            "Year-1 GPR: $424,420\n"
            "Annual debt service: $252,000\n"
            "Key metrics: cap 4.3%, DSCR 0.89x, CoC -5%\n"
            "Rationale: thin coverage, sub-treasury cap.\n"
            "Risks: insurance escalation, lender re-trade risk.\n"
        )
        p = _write_docx(tmp_path / "memo.docx", body)
        report = v.validate(
            artifact_path=p,
            artifact_type="executive_summary",
            briefing=_briefing(),
        )
        # Critical findings should be zero (DD-gate info is acceptable)
        assert len(report.critical) == 0
        assert report.overall_ready

    def test_summary_message_reflects_critical_count(self, tmp_path: Path):
        body = "Some text with no required sections."
        p = _write_docx(tmp_path / "bad.docx", body)
        report = v.validate(
            artifact_path=p,
            artifact_type="executive_summary",
            briefing=_briefing(),
        )
        assert not report.overall_ready
        assert "Revisions required" in report.summary

    def test_report_to_dict_serializes(self, tmp_path: Path):
        body = "Recommendation: NO-GO\nKey metrics here\nRationale stated\nRisks listed\nPurchase price $5,250,000"
        p = _write_docx(tmp_path / "m.docx", body)
        report = v.validate(
            artifact_path=p,
            artifact_type="executive_summary",
            briefing=_briefing(),
        )
        d = report.to_dict()
        assert "artifact_path" in d
        assert "findings" in d
        assert "counts" in d
        assert isinstance(d["counts"]["critical"], int)
