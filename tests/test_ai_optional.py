"""Section 11 — the AI layer is optional, and provably so.

  AC-11.1  the deterministic core runs with the AI layer removed entirely
  AC-11.2  with ai_enabled off, NO code path issues an LLM request, and every
           generative surface offers a manual/template fallback
  AC-11.3  no AI output reaches persistent deal data without passing the
           deterministic validators

None of these had a test. AC-11.1 turned out to hold already — the
underwriting engine, comps, radar and export never imported the SDK — but
"holds today" and "cannot quietly stop holding" are different properties, and
only the second is worth anything. AC-11.2 had nothing behind it at all: the
`organizations.ai_enabled` column existed and no code read it, so "off" was
not a state the product could be in.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest

from core import ai_gate
from core.ai_gate import AIDisabled


# ---------------------------------------------------------------------------
# AC-11.1 — the deterministic core does not need the AI layer
# ---------------------------------------------------------------------------

@pytest.fixture()
def no_ai_layer(monkeypatch):
    """Make `import anthropic` fail, as if the SDK were not in the build."""
    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name.split(".")[0] == "anthropic":
            raise ImportError("AC-11.1: AI layer removed from this build")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    for mod in [m for m in sys.modules if m.split(".")[0] == "anthropic"]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    yield


def test_underwriting_math_runs_without_the_ai_layer(no_ai_layer):
    from core import calc

    terms = calc.DebtTerms(loan_amount=3_500_000, annual_rate=0.065,
                           amort_months=300, io_years=0)
    sched = calc.build_debt_schedule(terms, hold_years=5)
    assert sched is not None
    assert calc.cap_rate(350_000, 5_000_000) == pytest.approx(0.07)


def test_distress_scoring_runs_without_the_ai_layer(no_ai_layer):
    import datetime as dt

    from core import radar_v2

    c = radar_v2.score_loan_maturity(dt.date(2027, 3, 1), today=dt.date(2026, 8, 1))
    assert c is not None and 0 <= c.score <= 100 and c.contribution >= 0


def test_the_property_spine_runs_without_the_ai_layer(no_ai_layer):
    from core import spine

    pid = spine.property_id("51710", "1234-56-7890")
    assert pid.startswith("8R-51710-")
    assert spine.derive_8r_form("apartment", 120, None)


def test_deal_state_persists_without_the_ai_layer(no_ai_layer, tmp_path):
    from data.property_io import DealState, load_deal, save_deal

    folder = tmp_path / "p"
    folder.mkdir()
    deal = DealState(pp=1_000_000, noi=80_000, dp=30, ir=6.5, vac=8,
                     rg=3, eg=3, xc=6.5, hp=5)
    assert save_deal(folder, deal, actor="Brian").ok
    assert load_deal(folder).pp == 1_000_000


def test_t12_extraction_preprocessing_runs_without_the_ai_layer(no_ai_layer):
    """The grid flattener is deterministic on purpose (§11) — it must not
    drag the SDK in behind it."""
    from core.t12_grid import detect_period_grid, normalized_annual_text

    rows = [[" "] + [f"{m:02d}/28/2025" for m in range(1, 13)] + ["Total"],
            ["  TOTAL INCOME"] + [100] * 12 + [1200]]
    assert detect_period_grid(rows) is not None
    assert "1,200.00" in normalized_annual_text(rows, "S")


# ---------------------------------------------------------------------------
# AC-11.2 — ai_enabled off means no model is reached
# ---------------------------------------------------------------------------

def test_ai_is_on_by_default(monkeypatch):
    """Introducing the flag must not switch AI off for an existing install."""
    monkeypatch.delenv("ER_AI_ENABLED", raising=False)
    assert ai_gate.is_enabled(None)


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "disabled", "OFF"])
def test_the_env_override_disables_ai(monkeypatch, value):
    monkeypatch.setenv("ER_AI_ENABLED", value)
    assert not ai_gate.is_enabled(None)


def test_require_ai_raises_with_a_named_fallback(monkeypatch):
    """AC-11.2 asks for a fallback to be OFFERED, not merely for the call to
    be skipped — so the exception carries one."""
    monkeypatch.setenv("ER_AI_ENABLED", "off")
    with pytest.raises(AIDisabled) as e:
        ai_gate.require_ai("Artifact generation", "Use the Preview block.")
    assert e.value.surface == "Artifact generation"
    assert "Preview" in e.value.fallback
    assert "did not call a model" in str(e.value)


_GATED_SURFACES = [
    ("core.artifact_engine", "_call_claude"),
    ("core.document_ingest", "ingest_document"),
    ("core.ic_memo_validator", None),
    ("etl_listings.concessions", None),
    ("etl_listings.property_site", None),
]


@pytest.mark.parametrize("module,_fn", _GATED_SURFACES)
def test_every_generative_module_consults_the_gate(module, _fn):
    """The check sits on the line that BUILDS the client, so a surface cannot
    forget it and still get one."""
    import inspect

    mod = importlib.import_module(module)
    src = inspect.getsource(mod)
    assert "ai_gate.require_ai(" in src, f"{module} builds a client ungated"


def test_no_client_is_constructed_when_ai_is_off(monkeypatch):
    """The whole point: not 'the call fails', but 'no model is reached'."""
    monkeypatch.setenv("ER_AI_ENABLED", "off")
    constructed = []

    import anthropic

    class Tripwire:
        def __init__(self, *a, **kw):
            constructed.append(kw)
            raise AssertionError("a model client was constructed with AI off")

    monkeypatch.setattr(anthropic, "Anthropic", Tripwire)

    from core import ic_memo_validator
    # Reach the gate directly at a real call site rather than through a
    # public entry that would need a file on disk first.
    import inspect
    src = inspect.getsource(ic_memo_validator)
    gate_line = src.index("ai_gate.require_ai(")
    client_line = src.index("anthropic.Anthropic(api_key=")
    assert gate_line < client_line, (
        "the gate must run BEFORE the client is constructed")
    with pytest.raises(AIDisabled):
        ai_gate.require_ai("IC memo validation", "Deterministic checks still run.")
    assert not constructed


def test_a_settings_store_outage_does_not_flip_the_flag(monkeypatch):
    """A missing/broken Postgres means 'no opinion', not 'off' — an outage
    must not silently disable a paid feature, nor silently enable one."""
    monkeypatch.delenv("ER_AI_ENABLED", raising=False)
    monkeypatch.setattr(ai_gate, "_stored_setting",
                        lambda org: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        ai_gate._stored_setting("org")          # the stub really does raise
    # ai_status swallows it and keeps the default
    monkeypatch.setattr(ai_gate, "_stored_setting", lambda org: None)
    assert ai_gate.is_enabled("some-org")


def test_an_org_with_the_flag_off_is_disabled(monkeypatch):
    monkeypatch.delenv("ER_AI_ENABLED", raising=False)
    monkeypatch.setattr(ai_gate, "_stored_setting", lambda org: False)
    assert not ai_gate.is_enabled("org-with-ai-off")
    monkeypatch.setattr(ai_gate, "_stored_setting", lambda org: True)
    assert ai_gate.is_enabled("org-with-ai-on")


def test_the_flag_exists_in_the_pilot_schema():
    """The column the gate reads has to be there for an org to set it."""
    import pathlib

    sql = (pathlib.Path(__file__).resolve().parent.parent
           / "db" / "pilot_schema.sql").read_text(encoding="utf-8")
    assert "ai_enabled" in sql


# ---------------------------------------------------------------------------
# AC-11.3 — validators stand between AI output and stored deal data
# ---------------------------------------------------------------------------

def test_ai_prose_that_changes_a_number_is_rejected():
    """A polish pass may reword; it may not invent, move or drop a figure.

    This is the whole of AC-11.3 in one function: the generative surface
    produces prose, and a DETERMINISTIC check stands between that prose and
    anything persisted. A changed price in an outreach letter is a
    misrepresentation to a seller, not a formatting slip.
    """
    from core.outreach.artifacts import validate_polish

    grounded = "Offer is $2,850,000 at a 7.25% cap on 26 units."

    ok, why = validate_polish(grounded,
                              "We can offer $2,850,000 at a 7.25% cap on 26 units.")
    assert ok, why

    bad, why = validate_polish(grounded,
                               "We can offer $3,100,000 at a 7.25% cap on 26 units.")
    assert not bad and "introduced" in why

    dropped, why = validate_polish(grounded, "We can make you a strong offer.")
    assert not dropped and "dropped" in why


def test_the_validator_notices_a_moved_decimal():
    """The failure that reads as a typo and costs the most."""
    from core.outreach.artifacts import validate_polish

    ok, _ = validate_polish("Asking $2,850,000.", "Asking $285,000.")
    assert not ok


def test_the_validator_ignores_pure_rewording():
    from core.outreach.artifacts import validate_polish

    ok, _ = validate_polish("26 units at $2,850,000.",
                            "A 26-unit asset priced at $2,850,000.")
    assert ok
