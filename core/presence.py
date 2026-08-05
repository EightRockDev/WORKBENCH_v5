"""Who's-online presence — track active sessions (identity, IP, locality).

Owner ask 2026-08-04: replace the topbar "V1" pill with a live count of users
on the site, clickable to a page showing who each one is logged in as, their IP,
and their locality (e.g. "Virginia Beach, Virginia").

In-memory, per server process. The Blue instance (8501) serves all traffic
(Caddy `lb_policy first`), so its registry sees every user; presence is live
state, not history, and resets on restart. `touch()` is cheap (no network) and
runs on every rerun; the geolocation lookup happens only when the who's-online
page is rendered, and is cached.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
import urllib.request

# "Online" = interacted within this window. Streamlit only reruns a session on
# interaction, so an idle-but-open tab ages out after this; the label says
# "active in the last N minutes" rather than claiming a live socket count.
ACTIVE_WINDOW_SECONDS = 300

_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}     # session_id -> {name, ip, last_seen}
_GEO_CACHE: dict[str, str] = {}
# Session ids an admin has asked to sign out (owner ask 2026-08-05). The target
# session ends itself on its next rerun (`should_logout` → `st.logout()`); we
# can't kill another browser's auth cookie remotely, so the sign-out lands when
# that user next interacts. Same-process only, which holds: Caddy's
# `lb_policy first` routes every session to the Blue instance, so the admin's
# request and the target's session share this registry.
_FORCE_LOGOUT: set[str] = set()


def _is_private_ip(ip: str) -> bool:
    """True for anything a public geo lookup can't place: blank, loopback,
    RFC1918 LAN, link-local, or 100.64/10 CGNAT (Tailscale / carrier NAT)."""
    if not ip:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    try:
        return addr in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _http_geo(ip: str) -> str:
    """Best-effort city/region from a free IP-geo service. HTTP only (the free
    tier), server-side, cached by the caller. Returns '' on any failure."""
    url = (f"http://ip-api.com/json/{ip}"
           "?fields=status,city,regionName,country")
    with urllib.request.urlopen(url, timeout=3) as r:      # noqa: S310 (trusted host)
        data = json.loads(r.read().decode("utf-8"))
    if data.get("status") != "success":
        return ""
    city = (data.get("city") or "").strip()
    region = (data.get("regionName") or "").strip()
    country = (data.get("country") or "").strip()
    parts = [p for p in (city, region) if p]
    return ", ".join(parts) if parts else country


def locality_for_ip(ip: str, *, _fetch=None) -> str:
    """'City, Region' for a public IP; 'Local network' for LAN/Tailscale/blank.

    Cached per IP; a network failure degrades to 'Unknown' rather than raising.
    `_fetch` is injectable for tests so no real request is made.
    """
    if _is_private_ip(ip):
        return "Local network"
    if ip in _GEO_CACHE:
        return _GEO_CACHE[ip]
    try:
        loc = (_fetch or _http_geo)(ip) or "Unknown"
    except Exception:
        loc = "Unknown"
    _GEO_CACHE[ip] = loc
    return loc


def touch(session_id: str, name: str, ip: str, *, now: float | None = None) -> None:
    """Record that `session_id` (a person, from `name`, at `ip`) is active now.
    Cheap and network-free — safe to call on every rerun."""
    if not session_id:
        return
    ts = time.time() if now is None else now
    with _LOCK:
        _SESSIONS[session_id] = {"name": name or "Unknown", "ip": ip or "",
                                 "last_seen": ts}


def active(*, within: int = ACTIVE_WINDOW_SECONDS,
           now: float | None = None) -> list[dict]:
    """Sessions seen within `within` seconds, newest first. Prunes stale ones."""
    ts = time.time() if now is None else now
    out: list[dict] = []
    with _LOCK:
        for sid in list(_SESSIONS):
            rec = _SESSIONS[sid]
            if ts - rec["last_seen"] > within:
                _SESSIONS.pop(sid, None)
                continue
            out.append({**rec, "session_id": sid})
    out.sort(key=lambda r: -r["last_seen"])
    return out


def count(*, within: int = ACTIVE_WINDOW_SECONDS, now: float | None = None) -> int:
    return len(active(within=within, now=now))


def request_logout(session_id: str) -> None:
    """Admin action: sign `session_id` out. Flags it for logout on its next
    rerun and drops it from the online list immediately so it disappears from
    the who's-online view without waiting for the target to act."""
    if not session_id:
        return
    with _LOCK:
        _FORCE_LOGOUT.add(session_id)
        _SESSIONS.pop(session_id, None)


def should_logout(session_id: str) -> bool:
    """True (once) if an admin has requested this session be signed out. Pops
    the flag so `st.logout()` fires a single time on the target's next rerun."""
    if not session_id:
        return False
    with _LOCK:
        if session_id in _FORCE_LOGOUT:
            _FORCE_LOGOUT.discard(session_id)
            return True
    return False


def _reset_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()
        _FORCE_LOGOUT.clear()
    _GEO_CACHE.clear()
