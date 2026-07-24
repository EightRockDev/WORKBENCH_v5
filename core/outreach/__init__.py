"""Module B — Compliant Outreach Engine (spec §5).

Velocity inside a gate: every outbound touch routes through
:func:`core.outreach.engine.attempt_touch`, which evaluates the §4.4 compliance
gate FIRST and logs the attempt with its rule trace either way (AC-B2). A
blocked touch is recorded as ``allowed=false`` and never dispatched.
"""

from core.outreach.engine import TouchResult, attempt_touch, callable_targets  # noqa: F401
