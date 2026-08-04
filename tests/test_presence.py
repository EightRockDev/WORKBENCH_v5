"""Who's-online presence tracking (owner ask 2026-08-04).

Pure-logic coverage: private-vs-public IP classification, the active window /
pruning, the live count, and locality lookup (with an injected fetch so no
real network call happens).
"""

from __future__ import annotations

import core.presence as p


def setup_function(_):
    p._reset_for_tests()


# ------------------------------------------------------- IP classification

def test_lan_loopback_tailscale_are_private():
    for ip in ("", "127.0.0.1", "192.168.0.45", "10.1.2.3", "172.16.5.5",
               "169.254.1.1", "100.113.210.35", "not-an-ip"):
        assert p._is_private_ip(ip), ip


def test_a_public_ip_is_not_private():
    assert not p._is_private_ip("98.190.60.27")


# ------------------------------------------------------------- locality

def test_private_ip_reads_local_without_network():
    # No _fetch passed -> must NOT hit the network for a LAN address.
    assert p.locality_for_ip("192.168.0.45") == "Local network"
    assert p.locality_for_ip("100.113.210.35") == "Local network"


def test_public_ip_uses_and_caches_the_lookup():
    calls = []

    def fake(ip):
        calls.append(ip)
        return "Virginia Beach, Virginia"

    assert p.locality_for_ip("98.190.60.27", _fetch=fake) == "Virginia Beach, Virginia"
    # Second call is served from cache — the fetch runs only once.
    assert p.locality_for_ip("98.190.60.27", _fetch=fake) == "Virginia Beach, Virginia"
    assert calls == ["98.190.60.27"]


def test_lookup_failure_degrades_to_unknown():
    def boom(ip):
        raise RuntimeError("network down")

    assert p.locality_for_ip("8.8.8.8", _fetch=boom) == "Unknown"


# --------------------------------------------------- active window / count

def test_touch_then_active_and_count():
    p.touch("s1", "Brian", "98.190.60.27", now=1000.0)
    p.touch("s2", "Peter", "192.168.0.50", now=1000.0)
    assert p.count(now=1001.0) == 2
    names = {r["name"] for r in p.active(now=1001.0)}
    assert names == {"Brian", "Peter"}


def test_a_stale_session_is_pruned():
    p.touch("s1", "Brian", "x", now=1000.0)
    # 6 minutes later, past the 5-minute window -> gone.
    assert p.count(now=1000.0 + 6 * 60) == 0


def test_re_touch_refreshes_last_seen():
    p.touch("s1", "Brian", "x", now=1000.0)
    p.touch("s1", "Brian", "x", now=1000.0 + 4 * 60)   # active again
    assert p.count(now=1000.0 + 5 * 60) == 1           # still within window
    assert len(p.active(now=1000.0 + 5 * 60)) == 1     # one session, not two


def test_blank_session_id_is_ignored():
    p.touch("", "Nobody", "x", now=1000.0)
    assert p.count(now=1000.0) == 0


def test_active_is_newest_first():
    p.touch("old", "A", "x", now=1000.0)
    p.touch("new", "B", "x", now=1005.0)
    order = [r["name"] for r in p.active(now=1006.0)]
    assert order == ["B", "A"]
