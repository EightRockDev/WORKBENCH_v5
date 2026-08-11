"""Per-run provider call trace — the evidence layer for live skip-trace.

Owner report 2026-08-11 ("it never has [worked] - fix it"): the panel showed
all providers live, 'Resolved 2 contact(s)', $0.00 — and no phone, no email.
Every live adapter swallowed its failure into ``None`` (a 404'd endpoint, an
auth error, an async retry never polled), the pipeline treated None as a
clean no-hit, and the UI called the run a success. Nothing anywhere said
WHAT each vendor was asked or answered.

This module is the fix's foundation: every provider call, live or mock,
records one line here — vendor, operation, outcome (hit / miss / error),
detail (HTTP status + body snippet on failure, counts on success). The
pipeline snapshots the log into the run result; the panel renders it under
the result banner; the diagnose script prints it. A silent None is no
longer possible.

Thread-local so concurrent Streamlit sessions don't interleave runs.
"""

from __future__ import annotations

import threading

_local = threading.local()


def _log() -> list[dict]:
    if not hasattr(_local, "log"):
        _local.log = []
    return _local.log


def reset() -> None:
    """Start a fresh trace (call at the top of a resolve run)."""
    _local.log = []


def record(vendor: str, op: str, outcome: str, detail: str = "") -> None:
    """One provider-call line. outcome: 'hit' | 'miss' | 'error' | 'skip'."""
    _log().append({"vendor": vendor, "op": op, "outcome": outcome,
                   "detail": (detail or "")[:400]})


def snapshot() -> list[dict]:
    """The lines recorded since the last reset()."""
    return list(_log())
