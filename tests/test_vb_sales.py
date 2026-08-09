"""VB sales puller — self-gating on the confirmed Spatialest resource, and
the extract path once rows land. No network: resolver + parsing only."""

from __future__ import annotations

import importlib


def test_resolver_env_override(monkeypatch):
    import scripts.pull_vb_sales as vb
    importlib.reload(vb)
    monkeypatch.setenv("ER_VB_SALES_RESOURCE", "sales-history")
    assert vb.resolve_resource() == "sales-history"


def test_resolver_parses_probe_found(tmp_path, monkeypatch):
    import scripts.pull_vb_sales as vb
    importlib.reload(vb)
    monkeypatch.delenv("ER_VB_SALES_RESOURCE", raising=False)
    report = tmp_path / "vb-sales-probe.txt"
    report.write_text(
        "PROBE COMPLETE - FOUND: "
        "https://api.spatialest.com/v1/va/virginiabeach/sales/14552807310000")
    monkeypatch.setattr(vb, "_PROBE_REPORT", report)
    assert vb.resolve_resource() == "sales"


def test_resolver_skips_when_unconfirmed(tmp_path, monkeypatch):
    import scripts.pull_vb_sales as vb
    importlib.reload(vb)
    monkeypatch.delenv("ER_VB_SALES_RESOURCE", raising=False)
    missing = tmp_path / "nope.txt"
    monkeypatch.setattr(vb, "_PROBE_REPORT", missing)
    assert vb.resolve_resource() is None


def test_main_skips_cleanly_when_unconfirmed(tmp_path, monkeypatch, capsys):
    import scripts.pull_vb_sales as vb
    importlib.reload(vb)
    monkeypatch.delenv("ER_VB_SALES_RESOURCE", raising=False)
    monkeypatch.setattr(vb, "_PROBE_REPORT", tmp_path / "nope.txt")
    assert vb.main() == 0
    assert "not confirmed" in capsys.readouterr().out
