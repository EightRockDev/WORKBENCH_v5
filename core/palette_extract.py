"""Derive a workbench theme from a live website.

Point this at a URL and it fetches the page, reads every stylesheet it can
reach (inline `<style>`, `style=` attributes and linked CSS), and works out
what the site's palette actually *is* — not just "here are 200 hex codes".

The useful part is role assignment. A raw colour census is noise; what the
theme needs is "which colour is the page background, which is body text,
which is the accent". We infer that from where each colour is used (a colour
in `background` is a surface candidate; a colour in `color` is a text
candidate), weighted by how often it appears, then fill the rest of the
palette by deriving tints and shades so the result is internally consistent.

Contrast is enforced at the end: text is darkened or lightened until it
clears WCAG AA (4.5:1) against the background it lands on, so a site with
low-contrast marketing copy can't produce an unreadable workbench.

Nothing here touches the network unless `extract_palette()` is called.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlparse

MAX_BYTES = 2_000_000        # cap on any single fetch
MAX_STYLESHEETS = 8          # linked stylesheets to follow
TIMEOUT = 12

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Properties that signal what a colour is *for*.
_SURFACE_PROPS = ("background", "background-color")
_TEXT_PROPS = ("color", "-webkit-text-fill-color")
_BORDER_PROPS = ("border", "border-color", "border-top-color", "outline-color",
                 "border-bottom-color", "border-left-color", "border-right-color")

_DECL_RE = re.compile(r"(?P<prop>[-a-zA-Z]+)\s*:\s*(?P<val>[^;{}]+)")
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})\b")
_RGB_RE = re.compile(
    r"rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*(?:[,/]\s*([\d.%]+)\s*)?\)"
)
_HSL_RE = re.compile(
    r"hsla?\(\s*([\d.]+)(?:deg)?\s*[, ]\s*([\d.]+)%\s*[, ]\s*([\d.]+)%\s*"
    r"(?:[,/]\s*([\d.%]+)\s*)?\)"
)
_FONT_RE = re.compile(r"font-family\s*:\s*([^;{}]+)", re.I)

_NAMED = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000",
    "green": "#008000", "blue": "#0000ff", "gray": "#808080",
    "grey": "#808080", "silver": "#c0c0c0", "navy": "#000080",
    "teal": "#008080", "orange": "#ffa500",
}

_GENERIC_FAMILIES = {
    "inherit", "initial", "unset", "revert", "sans-serif", "serif",
    "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
    "ui-monospace", "ui-serif", "ui-rounded", "-apple-system", "auto",
}


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    v = v[:6].ljust(6, "0")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rgb_to_hex(rgb: Iterable[float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def luminance(hex_color: str) -> float:
    """WCAG relative luminance, 0 (black) .. 1 (white)."""
    def chan(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(hex_color)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def to_hls(hex_color: str) -> tuple[float, float, float]:
    r, g, b = (c / 255.0 for c in hex_to_rgb(hex_color))
    return colorsys.rgb_to_hls(r, g, b)


def from_hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, min(max(l, 0.0), 1.0),
                                  min(max(s, 0.0), 1.0))
    return rgb_to_hex((r * 255, g * 255, b * 255))


def saturation(hex_color: str) -> float:
    return to_hls(hex_color)[2]


def mix(a: str, b: str, t: float) -> str:
    """Blend `a` toward `b` by `t` (0..1)."""
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def shade(hex_color: str, delta: float) -> str:
    """Lighten (delta > 0) or darken (delta < 0) by a lightness step."""
    h, l, s = to_hls(hex_color)
    return from_hls(h, l + delta, s)


def ensure_contrast(fg: str, bg: str, target: float = 4.5) -> str:
    """Push `fg` away from `bg` in lightness until it clears `target`."""
    if contrast(fg, bg) >= target:
        return fg
    h, l, s = to_hls(fg)
    darken = luminance(bg) > 0.5
    for _ in range(40):
        l = l - 0.025 if darken else l + 0.025
        if not 0.0 <= l <= 1.0:
            break
        cand = from_hls(h, l, s)
        if contrast(cand, bg) >= target:
            return cand
    return "#111111" if darken else "#f8fafc"


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _norm_color(token: str) -> str | None:
    """Normalize one CSS colour token to #rrggbb, or None if not a colour."""
    t = token.strip().lower()
    if t in _NAMED:
        return _NAMED[t]
    m = _HEX_RE.fullmatch(t) or _HEX_RE.match(t)
    if m and t.startswith("#"):
        raw = m.group(1)
        if len(raw) in (4, 8):           # has alpha — drop fully transparent
            alpha = raw[3] if len(raw) == 4 else raw[6:8]
            if alpha in ("0", "00"):
                return None
        return rgb_to_hex(hex_to_rgb(raw))
    m = _RGB_RE.fullmatch(t)
    if m:
        if m.group(4) is not None:
            a = m.group(4)
            val = float(a.rstrip("%")) / (100.0 if a.endswith("%") else 1.0)
            if val < 0.15:
                return None
        return rgb_to_hex((float(m.group(1)), float(m.group(2)), float(m.group(3))))
    m = _HSL_RE.fullmatch(t)
    if m:
        if m.group(4) is not None:
            a = m.group(4)
            val = float(a.rstrip("%")) / (100.0 if a.endswith("%") else 1.0)
            if val < 0.15:
                return None
        return from_hls(float(m.group(1)) / 360.0,
                        float(m.group(3)) / 100.0,
                        float(m.group(2)) / 100.0)
    return None


def _colors_in(value: str) -> list[str]:
    out = []
    for rx in (_HEX_RE, _RGB_RE, _HSL_RE):
        for m in rx.finditer(value):
            c = _norm_color(m.group(0))
            if c:
                out.append(c)
    for name, hexv in _NAMED.items():
        if re.search(rf"\b{name}\b", value):
            out.append(hexv)
    return out


@dataclass
class ColorStats:
    surface: dict[str, float] = field(default_factory=dict)
    text: dict[str, float] = field(default_factory=dict)
    border: dict[str, float] = field(default_factory=dict)
    total: dict[str, float] = field(default_factory=dict)

    def add(self, prop: str, color: str, weight: float = 1.0) -> None:
        p = prop.lower()
        self.total[color] = self.total.get(color, 0.0) + weight
        if p in _SURFACE_PROPS or p.startswith("background"):
            self.surface[color] = self.surface.get(color, 0.0) + weight
        elif p in _TEXT_PROPS:
            self.text[color] = self.text.get(color, 0.0) + weight
        elif p in _BORDER_PROPS or p.startswith("border") or p.startswith("outline"):
            self.border[color] = self.border.get(color, 0.0) + weight


def parse_css(css: str, stats: ColorStats, fonts: dict[str, float]) -> None:
    """Accumulate colour + font evidence from a blob of CSS."""
    for m in _DECL_RE.finditer(css):
        prop, val = m.group("prop"), m.group("val")
        for color in _colors_in(val):
            stats.add(prop, color)
    for m in _FONT_RE.finditer(css):
        stack = " ".join(m.group(1).split()).strip().rstrip(";")
        first = stack.split(",")[0].strip().strip("'\"").lower()
        if first and first not in _GENERIC_FAMILIES and len(stack) < 200:
            fonts[stack] = fonts.get(stack, 0.0) + 1.0


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch(url: str) -> str:
    import requests
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": _UA}, stream=True)
    r.raise_for_status()
    body = r.raw.read(MAX_BYTES, decode_content=True) or b""
    return body.decode(r.encoding or "utf-8", errors="replace")


def _gather_css(url: str) -> tuple[str, list[str]]:
    """Return (combined CSS text, notes about what was read)."""
    from bs4 import BeautifulSoup

    html = _fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    notes: list[str] = []
    chunks: list[str] = []

    inline = [t.get_text() or "" for t in soup.find_all("style")]
    if inline:
        chunks.extend(inline)
        notes.append(f"{len(inline)} inline <style> block(s)")

    attrs = [t["style"] for t in soup.select("[style]") if t.get("style")]
    if attrs:
        # Wrap so the declaration regex sees them the same way.
        chunks.append("\n".join(f"x{{{s}}}" for s in attrs))
        notes.append(f"{len(attrs)} inline style attribute(s)")

    hrefs: list[str] = []
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        href = link.get("href")
        if href and ("stylesheet" in rel or str(href).endswith(".css")):
            hrefs.append(urljoin(url, href))
    read = 0
    for href in hrefs[:MAX_STYLESHEETS]:
        try:
            chunks.append(_fetch(href))
            read += 1
        except Exception:
            continue
    if read:
        notes.append(f"{read} linked stylesheet(s)")
    if len(hrefs) > MAX_STYLESHEETS:
        notes.append(f"skipped {len(hrefs) - MAX_STYLESHEETS} further stylesheet(s)")

    return "\n".join(chunks), notes


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------

@dataclass
class Palette:
    url: str
    is_dark: bool
    content: dict[str, str]
    chrome: dict[str, str]
    fonts: dict[str, str]
    swatches: list[tuple[str, float]]
    notes: list[str]


def _best(scores: dict[str, float], predicate, fallback: str | None = None) -> str | None:
    ranked = sorted(
        ((c, w) for c, w in scores.items() if predicate(c)),
        key=lambda cw: -cw[1],
    )
    return ranked[0][0] if ranked else fallback


def build_palette(stats: ColorStats, fonts: dict[str, float],
                  url: str, notes: list[str]) -> Palette:
    """Turn raw colour evidence into a coherent theme."""
    if not stats.total:
        raise ValueError("No colours found on that page.")

    # Is the site light or dark? Weight surface evidence by how often it's used.
    surf = stats.surface or stats.total
    weight = sum(surf.values()) or 1.0
    mean_lum = sum(luminance(c) * w for c, w in surf.items()) / weight
    is_dark = mean_lum < 0.45

    # --- surfaces -----------------------------------------------------------
    if is_dark:
        bg = _best(surf, lambda c: luminance(c) < 0.22) \
            or _best(surf, lambda c: luminance(c) < 0.5) or "#0f1117"
        step = 0.035
    else:
        bg = _best(surf, lambda c: luminance(c) > 0.82) \
            or _best(surf, lambda c: luminance(c) > 0.6) or "#f1f4f9"
        step = -0.030

    # A distinct card surface if the site has one, else a derived step.
    bg2 = _best(
        surf,
        lambda c: c != bg and abs(luminance(c) - luminance(bg)) > 0.02
        and abs(luminance(c) - luminance(bg)) < 0.30,
    ) or shade(bg, -step)
    bg3 = shade(bg, step * 0.6)
    bg4 = shade(bg, step * 1.6)

    # --- text ---------------------------------------------------------------
    txt_pool = stats.text or stats.total
    tx = _best(
        txt_pool,
        lambda c: contrast(c, bg) >= 4.0 and saturation(c) < 0.65,
    ) or _best(txt_pool, lambda c: contrast(c, bg) >= 3.0) \
        or ("#f5f7fb" if is_dark else "#0f1117")
    tx = ensure_contrast(tx, bg, 7.0)
    tx2 = ensure_contrast(mix(tx, bg, 0.28), bg, 4.5)
    tx3 = ensure_contrast(mix(tx, bg, 0.45), bg, 3.2)

    # --- borders ------------------------------------------------------------
    bdr = _best(
        stats.border,
        lambda c: 1.15 <= contrast(c, bg) <= 4.0,
    ) or mix(bg, tx, 0.22)
    bdr2 = mix(bdr, tx, 0.28)

    # --- accent -------------------------------------------------------------
    def accent_ok(c: str) -> bool:
        h, l, s = to_hls(c)
        return s >= 0.35 and 0.18 <= l <= 0.78 and contrast(c, bg) >= 1.6

    accent = _best(stats.total, accent_ok)
    if accent is None:
        accent = _best(stats.total, lambda c: saturation(c) >= 0.2) or "#C8900A"
    ac = accent
    ac2 = ensure_contrast(shade(accent, -0.10 if not is_dark else 0.12), bg, 3.0)
    ac3 = shade(accent, -0.20 if not is_dark else 0.22)

    # --- semantic hues ------------------------------------------------------
    # Prefer a real colour from the site when one sits in the right hue band;
    # otherwise keep the shipped semantic colour so verdicts stay legible.
    def hue_pick(target_h: float, fallback: str, tol: float = 0.055) -> str:
        cand = _best(
            stats.total,
            lambda c: saturation(c) >= 0.3
            and _hue_distance(to_hls(c)[0], target_h) <= tol,
        )
        return ensure_contrast(cand, bg, 3.0) if cand else fallback

    gn = hue_pick(0.33, "#15803d" if not is_dark else "#22c55e")
    rd = hue_pick(0.00, "#b91c1c" if not is_dark else "#ef4444")
    yw = hue_pick(0.11, "#b45309" if not is_dark else "#f59e0b")
    bl = hue_pick(0.60, "#1d4ed8" if not is_dark else "#3b82f6")

    tint = 0.86 if not is_dark else 0.78          # how far toward bg
    content = {
        "bg": bg, "bg2": bg2, "bg3": bg3, "bg4": bg4,
        "bdr": bdr, "bdr2": bdr2,
        "tx": tx, "tx2": tx2, "tx3": tx3,
        "ac": ac, "ac2": ac2, "ac3": ac3,
        "gn": gn, "gnbg": mix(gn, bg, tint), "gnbrd": mix(gn, bg, tint * 0.62),
        "rd": rd, "rdbg": mix(rd, bg, tint), "rdbrd": mix(rd, bg, tint * 0.62),
        "yw": yw,
        "bl": bl, "blbg": mix(bl, bg, tint),
        "src_rr": gn,
        "src_t12": yw,
        "src_ref": mix(tx, bg, 0.42),   # reference survey row - muted grey
        "src_8r": hue_pick(0.47, "#14b8a6"),  # 8R backbone - teal family
        "src_etl": hue_pick(0.75, "#7c3aed"),
        "src_user": ac2,
        "src_calc": bl,
        "src_unknown": mix(tx, bg, 0.58),
    }

    # --- chrome -------------------------------------------------------------
    # The shell stays dark. Anchor it on the site's darkest prominent colour so
    # it feels related, then derive the steps.
    dark_anchor = _best(stats.total, lambda c: luminance(c) < 0.16) \
        or _best(stats.total, lambda c: luminance(c) < 0.3) \
        or shade(mix(ac, "#0f1117", 0.82), 0.0)
    c_bg = dark_anchor
    chrome = {
        "bg": c_bg,
        "bg2": shade(c_bg, 0.035),
        "bg3": shade(c_bg, 0.065),
        "bg4": shade(c_bg, 0.095),
        "bdr": shade(c_bg, 0.14),
        "bdr2": shade(c_bg, 0.20),
        "tx": ensure_contrast("#f5f7fb", c_bg, 7.0),
        "tx2": ensure_contrast(mix("#f5f7fb", c_bg, 0.30), c_bg, 4.5),
        "tx3": ensure_contrast(mix("#f5f7fb", c_bg, 0.48), c_bg, 3.2),
        "ac": ensure_contrast(ac, c_bg, 3.0),
        "ac2": ensure_contrast(shade(ac, 0.16), c_bg, 4.5),
        "ac3": shade(ac, -0.14),
        "gn": ensure_contrast(gn, c_bg, 3.0),
        "rd": ensure_contrast(rd, c_bg, 3.0),
        "yw": ensure_contrast(yw, c_bg, 3.0),
        "bl": ensure_contrast(bl, c_bg, 3.0),
        "blbg": mix(bl, c_bg, 0.78),
    }

    # --- fonts --------------------------------------------------------------
    ranked_fonts = sorted(fonts.items(), key=lambda kv: -kv[1])
    ui_stack = ranked_fonts[0][0] if ranked_fonts else ""
    mono_stack = next(
        (s for s, _ in ranked_fonts
         if any(k in s.lower() for k in ("mono", "consol", "courier", "code"))),
        "",
    )
    font_out: dict[str, str] = {}
    if ui_stack:
        font_out["ui"] = ui_stack
    if mono_stack:
        font_out["mono"] = mono_stack

    swatches = sorted(stats.total.items(), key=lambda kv: -kv[1])[:18]
    notes = list(notes)
    notes.append(f"{len(stats.total)} distinct colours; site reads "
                 f"{'dark' if is_dark else 'light'}")
    if not stats.text:
        notes.append("no explicit text colours found — body text derived")
    if not ranked_fonts:
        notes.append("no non-generic font stacks found — fonts left unchanged")

    return Palette(url=url, is_dark=is_dark, content=content, chrome=chrome,
                   fonts=font_out, swatches=swatches, notes=notes)


def extract_palette(url: str) -> Palette:
    """Fetch `url` and derive a full theme from it. Raises on a bad fetch."""
    url = url.strip()
    if not url:
        raise ValueError("Enter a website address.")
    if not urlparse(url).scheme:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{url!r} is not a valid http(s) address.")

    css, notes = _gather_css(url)
    if not css.strip():
        raise ValueError("Fetched the page but found no CSS to read.")

    stats = ColorStats()
    fonts: dict[str, float] = {}
    parse_css(css, stats, fonts)
    return build_palette(stats, fonts, url, notes)
