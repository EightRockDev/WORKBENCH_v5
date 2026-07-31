"""Theme editor — the panel behind the avatar in the top-right of the topbar.

Click the avatar to open it. Everything the workbench paints with is editable
here, saved against the signed-in user, and applied on the next rerun. The
"From a website" tab hands a URL to `core.palette_extract`, which reads the
site's CSS and proposes a full palette; you review the swatches before it
touches anything.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import config
from core import theme_prefs

_DRAFT = "theme_draft"
_EXTRACT = "theme_extracted"


# ---------------------------------------------------------------------------
# Draft state
# ---------------------------------------------------------------------------

def _draft() -> dict[str, dict[str, str]]:
    """The in-progress edit, seeded from what the user has saved."""
    if _DRAFT not in st.session_state:
        saved = theme_prefs.load_overrides()
        st.session_state[_DRAFT] = {
            "content": dict(saved.get("content") or {}),
            "chrome": dict(saved.get("chrome") or {}),
            "font": dict(saved.get("font") or {}),
        }
    return st.session_state[_DRAFT]


def _set(scope: str, key: str, value: str) -> None:
    d = _draft()
    if value == theme_prefs.default_for(scope, key):
        d[scope].pop(key, None)
    else:
        d[scope][key] = value


def _value(scope: str, key: str) -> str:
    return _draft()[scope].get(key) or theme_prefs.default_for(scope, key)


def _dirty_count() -> int:
    d = _draft()
    return sum(len(d[s]) for s in ("content", "chrome", "font"))


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

def _readability_warnings() -> list[str]:
    """Flag combinations that would leave the workbench hard to read.

    Cheap insurance: it's easy to darken a background without touching the
    text and end up unable to see the controls you'd use to undo it.
    """
    from core.palette_extract import contrast

    eff = theme_prefs.effective(_draft())
    c, k = eff["content"], eff["chrome"]
    checks = [
        ("Primary text", c["tx"], c["bg"], 4.5),
        ("Secondary text", c["tx2"], c["bg"], 4.5),
        ("Card text", c["tx"], c["bg2"], 4.5),
        ("Accent", c["ac"], c["bg"], 3.0),
        ("Top-bar text", k["tx"], k["bg"], 4.5),
    ]
    out = []
    for label, fg, bg, want in checks:
        ratio = contrast(fg, bg)
        if ratio < want:
            out.append(f"**{label}** sits at {ratio:.1f}:1 on its background "
                       f"(want {want:g}:1).")
    return out


def _render_colors_tab() -> None:
    st.caption(
        "Every colour the workbench paints with. Changes preview when you hit "
        "**Save** — they apply to your login only."
    )
    problems = _readability_warnings()
    if problems:
        st.warning(
            "**Low contrast — this will be hard to read.**\n\n"
            + "\n\n".join(f"· {p}" for p in problems)
            + "\n\nAdjust the text colours to match, or use **Reset to "
              "default** below to get back to the shipped theme."
        )
    for title, help_text, tokens in theme_prefs.TOKEN_GROUPS:
        changed = sum(1 for scope, key, _ in tokens if key in _draft()[scope])
        label = f"{title}  ·  {changed} changed" if changed else title
        with st.expander(label, expanded=False):
            st.caption(help_text)
            cols = st.columns(3)
            for i, (scope, key, token_label) in enumerate(tokens):
                with cols[i % 3]:
                    picked = st.color_picker(
                        token_label,
                        value=_value(scope, key),
                        key=f"tp_{scope}_{key}",
                    )
                    _set(scope, key, picked)
                    if key in _draft()[scope]:
                        st.caption(f"was `{theme_prefs.default_for(scope, key)}`")


def _render_fonts_tab() -> None:
    st.caption(
        "Font stacks are plain CSS — list fallbacks after your first choice. "
        "Only fonts already installed on your machine (or served by the page) "
        "will render."
    )
    for key, label, help_text in theme_prefs.FONT_FIELDS:
        if key == "scale":
            try:
                cur = float(_value("font", key))
            except ValueError:
                cur = 1.0
            picked = st.slider(
                label, min_value=0.85, max_value=1.25,
                value=min(max(cur, 0.85), 1.25), step=0.01,
                help=help_text, key="tp_font_scale",
            )
            _set("font", key, f"{picked:.2f}")
        else:
            picked = st.text_input(
                label, value=_value("font", key), help=help_text,
                key=f"tp_font_{key}",
            )
            _set("font", key, picked.strip())

    st.markdown("**Preview**")
    fonts = theme_prefs.effective(_draft())["font"]
    st.markdown(
        f'<div style="font-family:{fonts["ui"]};font-size:15px;'
        f'padding:10px 12px;border:1px solid {config.COLORS["bdr"]};'
        f'border-radius:6px;background:{config.COLORS["bg2"]}">'
        f'Crossroads Townhomes — 104 units · Norfolk, VA<br>'
        f'<span style="font-family:{fonts["mono"]};font-size:13px">'
        f'Cap 6.82% · DSCR 1.34x · CoC 6.1%</span></div>',
        unsafe_allow_html=True,
    )


def _swatch_row(pairs: list[tuple[str, str]]) -> str:
    return "".join(
        f'<div style="display:inline-block;text-align:center;margin:0 8px 8px 0">'
        f'<div style="width:44px;height:30px;border-radius:5px;background:{v};'
        f'border:1px solid rgba(0,0,0,.25)"></div>'
        f'<div style="font-size:9px;color:{config.COLORS["tx3"]};margin-top:2px">'
        f'{k}</div></div>'
        for k, v in pairs
    )


def _render_website_tab() -> None:
    st.caption(
        "Point this at any site. It reads the page's stylesheets, works out "
        "which colours are background / text / accent from how they're used, "
        "then derives the rest of the palette and forces text contrast to "
        "WCAG AA so nothing comes back unreadable."
    )
    url = st.text_input(
        "Website address",
        placeholder="eightrockcp.com",
        key="tp_url",
    )
    if st.button("Pull colours from this site", type="primary", key="tp_pull"):
        if not url.strip():
            st.warning("Enter a website address first.")
        else:
            with st.spinner(f"Reading {url}…"):
                try:
                    from core.palette_extract import extract_palette
                    st.session_state[_EXTRACT] = extract_palette(url)
                except Exception as exc:                     # noqa: BLE001
                    st.session_state.pop(_EXTRACT, None)
                    st.error(f"Couldn't read that site: {exc}")

    pal: Any = st.session_state.get(_EXTRACT)
    if pal is None:
        return

    st.success(f"Read **{pal.url}** — reads as a {'dark' if pal.is_dark else 'light'} site.")
    for note in pal.notes:
        st.caption(f"· {note}")

    st.markdown("**Most-used colours on the page**")
    st.markdown(
        _swatch_row([(c, c) for c, _ in pal.swatches]),
        unsafe_allow_html=True,
    )

    st.markdown("**Proposed content palette**")
    st.markdown(
        _swatch_row(list(pal.content.items())),
        unsafe_allow_html=True,
    )
    st.markdown("**Proposed chrome (top bar & sidebar)**")
    st.markdown(
        _swatch_row(list(pal.chrome.items())),
        unsafe_allow_html=True,
    )
    if pal.fonts:
        st.markdown("**Fonts found**")
        for k, v in pal.fonts.items():
            st.caption(f"`{k}` → {v}")

    what = st.multiselect(
        "Apply which parts?",
        options=["Content palette", "Chrome", "Fonts"],
        default=["Content palette", "Chrome"] + (["Fonts"] if pal.fonts else []),
        key="tp_apply_parts",
    )
    if st.button("Load into the editor", key="tp_apply"):
        d = _draft()
        if "Content palette" in what:
            for k, v in pal.content.items():
                _set("content", k, v)
        if "Chrome" in what:
            for k, v in pal.chrome.items():
                _set("chrome", k, v)
        if "Fonts" in what:
            for k, v in pal.fonts.items():
                _set("font", k, v)
        st.session_state[_DRAFT] = d
        # The colour pickers key off session_state; drop them so they re-seed.
        for sk in [k for k in st.session_state if k.startswith(("tp_content_",
                                                               "tp_chrome_",
                                                               "tp_font_"))]:
            st.session_state.pop(sk, None)
        st.success(
            f"Loaded into the editor ({_dirty_count()} tokens changed). "
            "Review on the other tabs, then **Save theme**."
        )


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

@st.dialog("Appearance", width="large")
def open_theme_dialog() -> None:
    name = theme_prefs.current_display_name()
    try:
        from core.storage import get_storage
        backend = get_storage().backend_label
    except Exception:
        backend = "local"

    st.markdown(
        f"**{name}** · theme saved to your login "
        f"<span style='color:{config.COLORS['tx3']};font-size:11px'>"
        f"({backend})</span>",
        unsafe_allow_html=True,
    )

    tab_colors, tab_fonts, tab_site = st.tabs(
        ["Colours", "Fonts", "From a website"]
    )
    with tab_colors:
        _render_colors_tab()
    with tab_fonts:
        _render_fonts_tab()
    with tab_site:
        _render_website_tab()

    st.divider()
    n = _dirty_count()
    c1, c2, c3 = st.columns([1.2, 1.2, 2.0])
    with c1:
        if st.button("Save theme", type="primary", use_container_width=True,
                     disabled=n == 0, key="tp_save"):
            theme_prefs.save_overrides(_draft())
            theme_prefs.apply_to_config()
            st.session_state.pop(_EXTRACT, None)
            st.rerun()
    with c2:
        if st.button("Reset to default", use_container_width=True, key="tp_reset"):
            theme_prefs.clear_overrides()
            theme_prefs.apply_to_config()
            for sk in [k for k in st.session_state if k.startswith("tp_")] + [
                _DRAFT, _EXTRACT
            ]:
                st.session_state.pop(sk, None)
            st.rerun()
    with c3:
        st.caption(
            f"{n} token{'' if n == 1 else 's'} differ from the shipped theme."
            if n else "Matching the shipped theme."
        )


# ---------------------------------------------------------------------------
# Topbar entry point
# ---------------------------------------------------------------------------

_AVATAR_KEY = "v2_avatar_btn"


def render_avatar_button() -> None:
    """Render the avatar as a real button that opens the theme dialog.

    Streamlit can't attach a click handler to raw HTML, so this is a normal
    `st.button` restyled into the circular avatar. The CSS anchors on the
    `st-key-<key>` class Streamlit stamps on the element container, which is
    exact and stable — a sibling-selector marker doesn't survive Streamlit's
    DOM changes, and would also catch the wrong button if the layout moved.
    """
    initials = theme_prefs.initials_for(theme_prefs.current_display_name())
    chrome = config.DARK_COLORS
    bg = chrome.get("bg", "#0f1117")
    accent = chrome.get("ac", "#D4A017")
    scope = f".st-key-{_AVATAR_KEY}"

    if st.button(initials, key=_AVATAR_KEY,
                 help="Appearance — colours, fonts and themes"):
        open_theme_dialog()

    st.markdown(
        f"""<style>
{scope} {{ display: flex !important; justify-content: flex-end !important; }}
{scope} .stButton,
{scope} [data-testid="stTooltipHoverTarget"],
{scope} [data-testid="stTooltipIcon"] {{ width: auto !important; }}
{scope} button {{
  width: 30px !important;
  min-width: 30px !important;
  height: 30px !important;
  min-height: 30px !important;
  max-height: 30px !important;
  padding: 0 !important;
  border-radius: 50% !important;
  border: none !important;
  background: linear-gradient(135deg, {bg}, #1F2937) !important;
  color: #ffffff !important;
  line-height: 1 !important;
  box-shadow: none !important;
  transition: filter .12s ease, box-shadow .12s ease;
}}
{scope} button p,
{scope} button div,
{scope} button span {{
  font-size: 11px !important;
  font-weight: 700 !important;
  color: #ffffff !important;
  margin: 0 !important;
  letter-spacing: .3px;
}}
{scope} button:hover {{
  filter: brightness(1.3);
  box-shadow: 0 0 0 2px {accent}66 !important;
}}
</style>""",
        unsafe_allow_html=True,
    )
