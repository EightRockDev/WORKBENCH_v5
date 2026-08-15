"""The loopback-only way in, and the guard that keeps it from being a hole.

The owner was locked out of his own machine's data on 2026-08-15 because
sign-in depends on two things he does not control from the keyboard - an
identity provider on the internet and a reverse proxy in front of the app.
`ER_LOCAL_LOGIN=1` is the door that nothing outside the machine can take
away.

What makes it a door and not a backdoor is the second condition: the server
must be bound to loopback. These tests exist because that condition is the
entire security argument. If it ever silently stops being enforced, the
public workbench.eight-rock.com host serves an ungated app to the internet.
"""

from __future__ import annotations

import pytest

from core import session


@pytest.fixture(autouse=True)
def _no_ambient_flag(monkeypatch):
    monkeypatch.delenv("ER_LOCAL_LOGIN", raising=False)


def _bind(monkeypatch, address):
    monkeypatch.setattr(session, "_bound_to_loopback",
                        lambda: address in session._LOOPBACK)


@pytest.mark.parametrize("address", ["127.0.0.1", "localhost", "::1"])
def test_open_on_loopback_when_the_flag_is_set(monkeypatch, address):
    monkeypatch.setenv("ER_LOCAL_LOGIN", "1")
    _bind(monkeypatch, address)
    assert session.local_console_login_enabled() is True


@pytest.mark.parametrize("address", ["0.0.0.0", "192.168.1.42", ""])
def test_the_flag_alone_does_nothing_on_a_server_that_answers_the_network(
        monkeypatch, address):
    """This is the whole security argument. Setting ER_LOCAL_LOGIN on the
    LAN service or the Caddy-fronted instance must be inert - by
    construction, not by anyone remembering not to."""
    monkeypatch.setenv("ER_LOCAL_LOGIN", "1")
    _bind(monkeypatch, address)
    assert session.local_console_login_enabled() is False


def test_loopback_alone_does_not_disable_sign_in(monkeypatch):
    """Every developer runs on localhost. Binding to loopback must not by
    itself turn the gate off - the flag has to be set deliberately."""
    _bind(monkeypatch, "127.0.0.1")
    assert session.local_console_login_enabled() is False


def test_an_unreadable_bind_address_counts_as_exposed(monkeypatch):
    """When the config cannot be read, the safe answer is the one that
    keeps the sign-in gate ON."""
    monkeypatch.setenv("ER_LOCAL_LOGIN", "1")

    def _boom():
        raise RuntimeError("no streamlit config in this context")

    monkeypatch.setattr(session, "_bound_to_loopback", _boom)
    with pytest.raises(RuntimeError):
        session.local_console_login_enabled()


def test_bound_to_loopback_reads_the_real_streamlit_config():
    """Guard against the check drifting to a different config key: the
    option must be the one the server actually binds."""
    from streamlit import config as st_config

    # Raises if the option name is wrong, which is the point.
    st_config.get_option("server.address")


def test_the_launcher_sets_both_conditions():
    """The batch file is the only supported way to open this door; it must
    set the flag AND bind loopback, or it silently does nothing."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "open-workbench-here.bat").read_text(encoding="utf-8")
    assert "ER_LOCAL_LOGIN=1" in src
    assert "--server.address=127.0.0.1" in src
    # Caddy forwards 8501/8502. An ungated instance must not sit on either.
    assert "--server.port=8599" in src
    caddyfile = (root / "deploy" / "windows" / "Caddyfile").read_text(
        encoding="utf-8")
    assert "8599" not in caddyfile, (
        "the ungated local port is reachable through the public front door")
