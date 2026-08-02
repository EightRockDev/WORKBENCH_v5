"""Per-user theme preferences.

The workbench reads its palette from two dicts: `config.COLORS` (the light
content pane) and `config.DARK_COLORS` (the top bar + sidebar chrome). Every
custom HTML component reads those dicts directly at render time, which is
exactly what makes a user theme cheap — merge a user's saved overrides into
those dicts once, early in the run, and the whole app follows. No
per-component refactoring, same trick the module docstring in `config.py`
already anticipates.

Storage is a single `_theme_prefs.json` at the workbench root, keyed by the
user's stable Entra `oid` (or `local-dev` when auth is disabled). All IO goes
through `core.storage` so this works identically on local disk and on the
Graph/OneDrive backend.

Fonts live here too, but they can't be merged into a colour dict — they're
emitted as a small CSS block by `font_css()` and injected after the theme
stylesheet so they win over the hard-coded families.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from typing import Any

import config

_PREFS_KEY = "_theme_prefs.json"

# Pristine snapshots taken at import, BEFORE anything mutates the live dicts.
# `reset()` and every "is this token still default?" check reads these, so
# applying a theme twice in one process can never compound.
DEFAULT_CONTENT: dict[str, str] = copy.deepcopy(config.COLORS)
DEFAULT_CHROME: dict[str, str] = copy.deepcopy(config.DARK_COLORS)

DEFAULT_FONTS: dict[str, str] = {
    "ui": "'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif",
    "mono": "'JetBrains Mono', Menlo, Consolas, monospace",
    "scale": "1.00",
}


# ---------------------------------------------------------------------------
# Token registry — drives the editor UI so new tokens show up automatically.
# ---------------------------------------------------------------------------

# (group title, help text, [(scope, key, label)])
TOKEN_GROUPS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    (
        "Surfaces",
        "Page and card backgrounds in the content pane.",
        [
            ("content", "bg",  "Page background"),
            ("content", "bg2", "Card / panel"),
            ("content", "bg3", "Inset / table header"),
            ("content", "bg4", "Hover / secondary"),
        ],
    ),
    (
        "Text",
        "Body copy in the content pane. Keep strong contrast against Surfaces.",
        [
            ("content", "tx",  "Primary text"),
            ("content", "tx2", "Secondary text"),
            ("content", "tx3", "Tertiary / labels"),
        ],
    ),
    (
        "Borders",
        "Card edges and dividers.",
        [
            ("content", "bdr",  "Border"),
            ("content", "bdr2", "Emphasized border"),
        ],
    ),
    (
        "Brand accent",
        "Primary accent — buttons, links, the brand mark.",
        [
            ("content", "ac",  "Accent"),
            ("content", "ac2", "Accent (emphasis)"),
            ("content", "ac3", "Accent (hover / active)"),
        ],
    ),
    (
        "Verdict & status",
        "GO / WATCH / NO-GO colours and their tinted backgrounds.",
        [
            ("content", "gn",    "GO green"),
            ("content", "gnbg",  "GO background"),
            ("content", "gnbrd", "GO border"),
            ("content", "yw",    "WATCH amber"),
            ("content", "rd",    "NO-GO red"),
            ("content", "rdbg",  "NO-GO background"),
            ("content", "rdbrd", "NO-GO border"),
            ("content", "bl",    "Link / selection blue"),
            ("content", "blbg",  "Blue background"),
        ],
    ),
    (
        "Source provenance",
        "The badge colours on the Property Card and throughout the tabs.",
        [
            ("content", "src_rr",      "Rent Roll"),
            ("content", "src_t12",     "T-12"),
            ("content", "src_ref",     "Reference record"),
            ("content", "src_8r",      "8R Backbone"),
            ("content", "src_etl",     "Public ETL"),
            ("content", "src_user",    "User input"),
            ("content", "src_calc",    "Computed"),
            ("content", "src_unknown", "Unknown"),
        ],
    ),
    (
        "Chrome (top bar & sidebar)",
        "The dark shell around the content pane.",
        [
            ("chrome", "bg",   "Chrome background"),
            ("chrome", "bg2",  "Chrome panel"),
            ("chrome", "bg3",  "Chrome inset"),
            ("chrome", "bg4",  "Chrome hover"),
            ("chrome", "bdr",  "Chrome border"),
            ("chrome", "bdr2", "Chrome border (emphasis)"),
            ("chrome", "tx",   "Chrome text"),
            ("chrome", "tx2",  "Chrome text (secondary)"),
            ("chrome", "tx3",  "Chrome text (tertiary)"),
            ("chrome", "ac",   "Chrome accent"),
            ("chrome", "ac2",  "Chrome accent (bright)"),
            ("chrome", "ac3",  "Chrome accent (deep)"),
            ("chrome", "gn",   "Chrome GO green"),
            ("chrome", "yw",   "Chrome WATCH amber"),
            ("chrome", "rd",   "Chrome NO-GO red"),
            ("chrome", "bl",   "Chrome link blue"),
            ("chrome", "blbg", "Chrome blue background"),
        ],
    ),
]

def _prune(groups):
    """Keep only tokens that exist in the shipped palette.

    The registry is written by hand; the palette evolves. Filtering here means
    a renamed or retired token quietly drops out of the editor instead of
    rendering a picker that writes an override nothing reads.
    """
    out = []
    for title, help_text, tokens in groups:
        live = [
            (scope, key, label) for scope, key, label in tokens
            if key in (DEFAULT_CHROME if scope == "chrome" else DEFAULT_CONTENT)
        ]
        if live:
            out.append((title, help_text, live))
    return out


TOKEN_GROUPS = _prune(TOKEN_GROUPS)


FONT_FIELDS: list[tuple[str, str, str]] = [
    ("ui", "Interface font stack", "Used for all body copy, labels and controls."),
    ("mono", "Monospace font stack", "Used for code, version pills and numeric tables."),
    ("scale", "Type scale", "Multiplier on the base font size (0.85–1.25)."),
]


def default_for(scope: str, key: str) -> str:
    """The shipped value for a token, ignoring any user override."""
    if scope == "chrome":
        return DEFAULT_CHROME.get(key, "#000000")
    if scope == "font":
        return DEFAULT_FONTS.get(key, "")
    return DEFAULT_CONTENT.get(key, "#000000")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def current_oid() -> str:
    """Stable per-user key. Falls back to `local-dev` when auth is off."""
    try:
        from core.auth import current_user
        oid = (current_user().oid or "").strip()
        return oid or "local-dev"
    except Exception:
        return "local-dev"


def current_display_name() -> str:
    try:
        from core.auth import current_user
        return current_user().display_name
    except Exception:
        return "User"



def identity() -> dict:
    """Who is signed in, what role is in effect, and how auth was resolved.

    Lives here rather than in the panel so it is importable without a
    Streamlit script-run context (the panel's dialog decorators are not).
    Defensive throughout: the avatar must render on the desktop path where
    there is no OIDC round-trip and no org, so every lookup degrades to a
    label rather than raising.
    """
    out = {"name": "User", "email": "", "roles": [], "org": None,
           "backend": "local dev (no login)", "preview": None, "admin": False}
    try:
        from core.auth import current_user
        u = current_user()
        out["name"] = u.display_name
        out["email"] = getattr(u, "email", "") or ""
        out["roles"] = list(getattr(u, "roles", ()) or ())
        out["admin"] = bool(getattr(u, "is_admin", False)
                            or getattr(u, "is_internal", False))
        if getattr(u, "is_anonymous", False):
            out["backend"] = "not signed in"
        elif getattr(u, "oid", "") == "local-dev":
            out["backend"] = "local dev (no login)"
        else:
            out["backend"] = "single sign-on"
    except Exception:
        pass
    try:
        import streamlit as _st
        out["org"] = _st.session_state.get("org_id")
        # §10.4: an admin can be previewing the app as another role preset.
        # That changes what the app shows, so it belongs on the identity card.
        pick = _st.session_state.get("_preview_role")
        if pick and pick not in ("", "— none —", "None"):
            out["preview"] = pick
    except Exception:
        pass
    return out

def initials_for(name: str) -> str:
    """Two-letter avatar initials from a display name.

    Parenthetical qualifiers are dropped first, so the local-dev identity
    ("Brian (local dev)") doesn't turn into "BD". Anything that yields fewer
    than two letters falls back to the shipped mark rather than showing a
    lone character where a two-letter avatar used to be.
    """
    cleaned = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(name or ""))
    parts = [w for w in cleaned.split() if w and w[0].isalpha()]
    ini = "".join(w[0].upper() for w in parts[:2])
    return ini if len(ini) >= 2 else "BM"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _read_all() -> dict[str, Any]:
    from core.storage import get_storage
    storage = get_storage()
    if not storage.is_file(_PREFS_KEY):
        return {}
    try:
        data = json.loads(storage.read_text(_PREFS_KEY))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, Any]) -> None:
    from core.storage import get_storage
    get_storage().write_text(_PREFS_KEY, json.dumps(data, indent=2, sort_keys=True))


def load_overrides(oid: str | None = None) -> dict[str, dict[str, str]]:
    """Return this user's saved overrides: {"content": {...}, "chrome": {...},
    "font": {...}}. Missing sections come back empty, never None."""
    entry = _read_all().get(oid or current_oid()) or {}
    out: dict[str, dict[str, str]] = {"content": {}, "chrome": {}, "font": {}}
    for scope in out:
        section = entry.get(scope)
        if isinstance(section, dict):
            out[scope] = {str(k): str(v) for k, v in section.items() if v}
    return out


def save_overrides(
    overrides: dict[str, dict[str, str]],
    oid: str | None = None,
) -> None:
    """Persist overrides for one user. Values equal to the shipped default are
    dropped so the stored theme only ever records genuine changes."""
    oid = oid or current_oid()
    cleaned: dict[str, dict[str, str]] = {}
    for scope in ("content", "chrome", "font"):
        section = overrides.get(scope) or {}
        kept = {
            str(k): str(v)
            for k, v in section.items()
            if v and str(v).strip() and str(v).strip() != default_for(scope, str(k))
        }
        if kept:
            cleaned[scope] = kept

    data = _read_all()
    if cleaned:
        cleaned["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        cleaned["display_name"] = current_display_name()
        data[oid] = cleaned
    else:
        data.pop(oid, None)
    _write_all(data)


def clear_overrides(oid: str | None = None) -> None:
    """Drop this user's theme entirely — back to the shipped palette."""
    data = _read_all()
    if data.pop(oid or current_oid(), None) is not None:
        _write_all(data)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def effective(overrides: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """Defaults merged with `overrides` (or the current user's saved theme)."""
    ov = load_overrides() if overrides is None else overrides
    return {
        "content": {**DEFAULT_CONTENT, **(ov.get("content") or {})},
        "chrome": {**DEFAULT_CHROME, **(ov.get("chrome") or {})},
        "font": {**DEFAULT_FONTS, **(ov.get("font") or {})},
    }


def apply_to_config(overrides: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """Merge a theme into the live `config` dicts, in place.

    Always rebuilds from the pristine defaults first, so calling this on every
    rerun (or after the user edits a token) is idempotent — a token the user
    just reset goes back to its shipped value instead of sticking.

    Returns the effective theme so callers can reuse it without re-reading.
    """
    eff = effective(overrides)
    config.COLORS.clear()
    config.COLORS.update(eff["content"])
    config.DARK_COLORS.clear()
    config.DARK_COLORS.update(eff["chrome"])
    return eff


def font_css(overrides: dict[str, dict[str, str]] | None = None) -> str:
    """A `<style>` block applying the user's fonts.

    Injected AFTER the main theme stylesheet so it overrides the hard-coded
    families in `ui/v2_theme_*.py` without editing every rule there.
    """
    fonts = effective(overrides)["font"]
    ui = fonts.get("ui") or DEFAULT_FONTS["ui"]
    mono = fonts.get("mono") or DEFAULT_FONTS["mono"]
    try:
        scale = float(fonts.get("scale") or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    scale = min(max(scale, 0.85), 1.25)

    if ui == DEFAULT_FONTS["ui"] and mono == DEFAULT_FONTS["mono"] and scale == 1.0:
        return ""

    return f"""<style>
:root {{ --qr-font-ui: {ui}; --qr-font-mono: {mono}; }}
html, body, .stApp, .stApp * {{ font-family: var(--qr-font-ui) !important; }}
code, pre, kbd, samp, .v2-version-pill, [class*="mono"] {{
  font-family: var(--qr-font-mono) !important;
}}
html {{ font-size: {scale * 100:.1f}%; }}
</style>"""
