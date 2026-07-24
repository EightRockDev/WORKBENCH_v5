"""Compliance gate (spec §4.4 C1-C7) — makes the compliant path the only path.

Federal TCPA exposure is $500-$1,500 per call/text, uncapped; state mini-TCPAs
(FL, TX, OK, MD, CT, NY, CA) add $5K-$11K per violation. Every outbound touch
must pass :func:`core.compliance.rules.evaluate` first, and the resulting rule
trace is logged with the touch (AC-B2).
"""

from core.compliance.rules import Decision, RuleResult, evaluate  # noqa: F401
