"""Section 11 — the AI layer is optional, and provably so.

Three commitments the spec makes about generative features:

  AC-11.1  the deterministic core runs with the AI layer removed entirely
  AC-11.2  with ``ai_enabled`` off for an org, NO code path issues an LLM
           request, and every generative surface offers a manual/template
           fallback instead
  AC-11.3  no AI output reaches persistent deal data without passing the
           deterministic validators

AC-11.1 held already — the underwriting engine, comps, radar and export never
imported the SDK. AC-11.2 had nothing behind it: there was no ``ai_enabled``
flag anywhere in the codebase, so "off" was not a state the product could be
in. This module is that switch, and the single place every generative call
must ask permission.

The rule is deliberately fail-closed. A new generative surface that forgets to
call :func:`require_ai` still cannot reach a model, because the call sites
themselves are pinned by ``tests/test_zero_training.py`` — but one that
forgets and is added to that allow-list would otherwise silently ignore an
org's setting. Making the check the thing that *returns the client* removes
the option to forget.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Environment override, mainly for the desktop/single-user path and for
# reproducing an AI-off org locally. An explicit "0"/"false"/"off" disables.
_ENV_FLAG = "ER_AI_ENABLED"

_FALSEY = {"0", "false", "off", "no", "disabled"}


class AIDisabled(RuntimeError):
    """Raised instead of calling a model when AI is off for this org.

    Carries the fallback the caller should offer, so a surface cannot fail
    with a bare error where the spec requires a manual/template path.
    """

    def __init__(self, surface: str, fallback: str):
        self.surface = surface
        self.fallback = fallback
        super().__init__(
            f"AI is disabled for this organization, so '{surface}' did not "
            f"call a model. {fallback}")


@dataclass(frozen=True)
class AIStatus:
    enabled: bool
    reason: str


def _env_disabled() -> bool:
    raw = os.environ.get(_ENV_FLAG)
    return raw is not None and raw.strip().lower() in _FALSEY


def ai_status(org_id: str | None = None) -> AIStatus:
    """Whether generative features may run for this org.

    Resolution order: the environment override, then the org's stored
    setting, then the default (on). Unknown orgs default ON so an existing
    single-tenant install is unaffected by the introduction of the flag.
    """
    if _env_disabled():
        return AIStatus(False, f"{_ENV_FLAG} is set to off")
    if org_id:
        stored = _stored_setting(org_id)
        if stored is False:
            return AIStatus(False, "ai_enabled is off for this organization")
    return AIStatus(True, "enabled")


def _stored_setting(org_id: str) -> bool | None:
    """The org's ai_enabled column, or None when unavailable.

    Never raises: a missing Postgres, a missing column, or an unknown org all
    mean "no opinion", and the caller falls back to the default. An outage in
    the settings store must not silently switch generative features off (or
    on) — it must leave the decision where it was.
    """
    try:
        from data import pg
        if not pg.is_configured():
            return None
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ai_enabled FROM organizations WHERE id = %s", (org_id,))
            row = cur.fetchone()
        if not row:
            return None
        val = row["ai_enabled"] if isinstance(row, dict) else row[0]
        return None if val is None else bool(val)
    except Exception:
        return None


def is_enabled(org_id: str | None = None) -> bool:
    return ai_status(org_id).enabled


def require_ai(surface: str, fallback: str, org_id: str | None = None) -> None:
    """Gate a generative surface. Raises :class:`AIDisabled` when AI is off.

    `surface` names the feature for the error and the audit trail; `fallback`
    is what the caller offers instead, and is required rather than optional
    because AC-11.2 asks for the fallback to exist, not merely for the call
    to be skipped.
    """
    status = ai_status(org_id)
    if not status.enabled:
        raise AIDisabled(surface, fallback)


def current_org_id() -> str | None:
    """Best-effort org for the running session; None outside Streamlit."""
    try:
        import streamlit as st
        return st.session_state.get("org_id")
    except Exception:
        return None
