"""SR-2.2 — customer data never reaches a model that trains on it.

The spec commits to this architecturally rather than as a setting: "all LLM
calls route through no-training API endpoints ... no customer-data
fine-tuning pipeline exists ... the commitment is contractual in the ToS."
A promise enforced by architecture has to be checked against the
architecture, so this asserts the shape of the code rather than the behaviour
of one call.

Deal data, T-12s, rent rolls and POC records are the most sensitive material
the product handles. A second model vendor added in a hurry, or a call that
bypasses the shared client, is exactly the change that would break the
commitment silently — nothing would fail, and no output would look different.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Where model calls are allowed to live. Every entry is a deliberate,
# reviewed call site; adding one is a decision, not an accident.
ALLOWED_CALL_SITES = {
    "core/artifact_engine.py",      # deal documents
    "core/document_ingest.py",      # T-12 / rent roll extraction
    "core/ic_memo_validator.py",    # memo audit
    "etl_listings/concessions.py",  # concession text parsing
    "etl_listings/property_site.py",  # listing page parsing
    # Market-data ETL folded in from GRANITE 2026-08-09 (so GRANITE can be
    # archived). Its listings parser makes the SAME reviewed LLM calls as the
    # etl_listings pair above (public listing-page text -> structured fields,
    # no customer data). Reviewed and approved as a deliberate data path.
    "hampton-roads-etl/pullers/listings/concessions.py",
    "hampton-roads-etl/pullers/listings/property_site.py",
}

# Vendors whose presence would mean customer data leaves the approved path.
# Anthropic's no-training commitment is what SR-2.2 relies on; anything else
# needs its own review and its own contract.
_FOREIGN_VENDORS = re.compile(
    r"\b(openai|OpenAI|chatgpt|gpt-4|gpt-3|"
    r"google\.generativeai|genai|gemini|"
    r"mistralai|cohere|replicate|together\.ai|ollama|huggingface|"
    r"transformers\.pipeline)\b")

# Endpoints that would train on, or persist, what we send.
_TRAINING_SURFACE = re.compile(
    r"(fine[_-]?tun|/v1/fine|create_fine|training_file|"
    r"\.train\(|model\.fit\(|upload_training)", re.IGNORECASE)

_SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "logs",
              "ingest-uploads", "tests", "docs", "reports"}


def _source_files():
    for p in ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _rel(p: pathlib.Path) -> str:
    return p.relative_to(ROOT).as_posix()


def test_model_calls_only_happen_at_reviewed_sites():
    """`messages.create` / `messages.stream` anywhere else is a new data path."""
    found = set()
    for path in _source_files():
        src = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\.messages\.(create|stream)\s*\(", src):
            found.add(_rel(path))
    unexpected = found - ALLOWED_CALL_SITES
    assert not unexpected, (
        f"model calls at unreviewed sites: {sorted(unexpected)} — every LLM "
        f"call must be a deliberate, reviewed data path (SR-2.2)")


def test_no_second_model_vendor_is_referenced():
    """SR-2.2 rests on Anthropic's no-training commitment; another vendor
    would need its own contract and its own review."""
    hits = []
    for path in _source_files():
        for i, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue          # prose about the industry is not a call
            m = _FOREIGN_VENDORS.search(line)
            if m:
                hits.append(f"{_rel(path)}:{i}: {m.group(0)}")
    assert not hits, "foreign model vendor referenced in code:\n" + "\n".join(hits)


def test_no_fine_tuning_or_training_surface_exists():
    """The spec's wording is 'no customer-data fine-tuning pipeline exists'."""
    hits = []
    for path in _source_files():
        for i, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            m = _TRAINING_SURFACE.search(line)
            if m:
                hits.append(f"{_rel(path)}:{i}: {m.group(0)}")
    assert not hits, "training/fine-tuning surface found:\n" + "\n".join(hits)


def test_every_call_site_imports_the_anthropic_sdk():
    """A raw HTTP POST to a model endpoint would bypass the SDK's guarantees
    and any future policy we attach to the client."""
    for rel in sorted(ALLOWED_CALL_SITES):
        path = ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.module
            else a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n.names[0]])
        }
        assert "anthropic" in imports, (
            f"{rel} calls a model without importing the anthropic SDK")


def test_no_customer_data_is_posted_to_an_arbitrary_host():
    """requests/httpx POSTs are fine for municipal feeds; a model-shaped
    endpoint reached that way is not."""
    suspicious = []
    for path in _source_files():
        src = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(requests|httpx)\.post\(\s*[\"']([^\"']+)", src):
            url = m.group(2)
            if re.search(r"(completion|chat|/v1/messages|inference|generate)",
                         url, re.IGNORECASE):
                suspicious.append(f"{_rel(path)}: {url}")
    assert not suspicious, (
        "model-shaped endpoint reached outside the SDK:\n" + "\n".join(suspicious))


def test_the_allow_list_has_not_gone_stale():
    """An entry for a file that no longer calls a model hides a later
    reintroduction at that path."""
    stale = []
    for rel in sorted(ALLOWED_CALL_SITES):
        path = ROOT / rel
        if not path.is_file():
            stale.append(f"{rel} (file is gone)")
            continue
        src = path.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"\.messages\.(create|stream)\s*\(", src):
            stale.append(f"{rel} (no longer calls a model)")
    assert not stale, "ALLOWED_CALL_SITES is stale:\n" + "\n".join(stale)
