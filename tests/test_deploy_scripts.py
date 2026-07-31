"""Consistency checks across the Windows deploy scripts.

These are shell scripts run once, by hand, on go-live day — the worst place
to discover that two of them disagree. Today they did twice: the updater
killed a process name the launcher never creates, and the blue-green swap
restarted services the installer had no way to create. Both failed silently
and reported success.

No pwsh in CI, so this validates structure and cross-file agreement rather
than executing anything.
"""

from __future__ import annotations

import pathlib
import re

import pytest

DEPLOY = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "windows"
ROOT = pathlib.Path(__file__).resolve().parent.parent
COLOURS = {"WorkbenchBlue": "8501", "WorkbenchGreen": "8502"}


def _src(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.ps1")))
def test_scripts_are_ascii(script):
    """Windows PowerShell 5.1 reads .ps1 as ANSI — a smart quote breaks it."""
    (DEPLOY / script).read_text(encoding="utf-8").encode("ascii")


@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.ps1")))
def test_delimiters_balance(script):
    src = _src(script)
    src = re.sub(r"<#.*?#>", "", src, flags=re.S)
    code = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
    for a, b in (("{", "}"), ("(", ")"), ("[", "]")):
        assert code.count(a) == code.count(b), f"{script}: unbalanced {a}{b}"


@pytest.mark.parametrize("colour,port", sorted(COLOURS.items()))
def test_every_script_agrees_on_the_blue_green_pair(colour, port):
    """The swap restarts these names; the installer must be able to make them
    and Caddy must route to their ports."""
    assert colour in _src("install-lan-service.ps1")
    assert colour in _src("deploy-swap.ps1")
    assert colour in _src("install.ps1")
    assert port in _src("Caddyfile")


def test_installer_takes_the_parameters_the_swap_script_advertises():
    lan = _src("install-lan-service.ps1")
    for param in ("$Name", "$Port", "$BindAddress"):
        assert param in lan, f"install-lan-service.ps1 missing {param}"


def test_installer_rejects_a_pair_caddy_cannot_route():
    """A third colour would install and then never be routed to or restarted."""
    lan = _src("install-lan-service.ps1")
    assert "known.ContainsKey" in lan or "known = @{" in lan


def test_no_stale_single_service_registration():
    """install.ps1's old 'Workbench' service bound the same 8501 as
    WorkbenchBlue — running both installers left two services fighting."""
    assert not re.search(r"nssm install Workbench\s", _src("install.ps1"))


def test_swap_fails_loudly_when_no_service_is_installed():
    """It used to skip both and exit 0 — 'zero-downtime deploy complete' on a
    machine where nothing had been deployed."""
    swap = _src("deploy-swap.ps1")
    assert "installed.Count -eq 0" in swap
    assert "exit 1" in swap


def test_each_colour_gets_its_own_uv_environment():
    """A shared UV_PROJECT_ENVIRONMENT lets both services sync into the same
    tree during a swap."""
    lan = _src("install-lan-service.ps1")
    # $Name is concatenated outside the quotes, so match the whole expression.
    m = re.search(r"UV_PROJECT_ENVIRONMENT=[^\n]*", lan)
    assert m, "no UV_PROJECT_ENVIRONMENT set"
    assert "$Name" in m.group(0), f"uv env dir is not per-colour: {m.group(0)}"


def test_localhost_mode_does_not_open_the_firewall():
    """When Caddy fronts the app, the raw port must not be exposed."""
    lan = _src("install-lan-service.ps1")
    assert "$lanMode" in lan
    assert "if ($lanMode) {" in lan


def test_launcher_and_updater_stop_the_process_they_actually_start():
    """The updater killed streamlit.exe; start-workbench.bat runs python.exe
    via `uv run python -m streamlit`, so nothing was ever stopped."""
    upd = (ROOT / "update-workbench.bat").read_text(encoding="utf-8")
    start = (ROOT / "start-workbench.bat").read_text(encoding="utf-8")
    assert "python -m streamlit" in start
    for bat, name in ((upd, "update-workbench.bat"),
                      (start, "start-workbench.bat")):
        assert "8501 8502" in bat, f"{name} does not stop by listening port"
