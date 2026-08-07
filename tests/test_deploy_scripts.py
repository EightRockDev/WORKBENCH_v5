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


# ---------------------------------------------------------------------------
# NativeCommandError under $ErrorActionPreference = "Stop" (2026-08-02)
# ---------------------------------------------------------------------------

def _code_lines(script: str) -> list[str]:
    """Script lines with comments and here-doc blocks removed."""
    src = re.sub(r"<#.*?#>", "", _src(script), flags=re.S)
    return [l for l in src.split("\n") if not l.lstrip().startswith("#")]


@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.ps1")))
def test_no_native_command_redirects_stderr_to_null(script):
    """`nssm stop <name>` on a service that does not exist writes to stderr.

    Under Windows PowerShell 5.1, `2>$null` on a NATIVE command turns that
    into a NativeCommandError record, and with $ErrorActionPreference = "Stop"
    it becomes terminating. Both installers died on their own idempotent
    cleanup step, before installing anything, on every machine where the
    service did not already exist — which is every first install.

    `2>&1` is fine; it is the discard-to-null form that manufactures the
    error record.
    """
    offenders = [l.strip() for l in _code_lines(script) if "2>$null" in l]
    assert not offenders, (
        f"{script}: native stderr redirected to $null — under EAP=Stop this "
        f"is fatal on a clean machine:\n  " + "\n  ".join(offenders))


def test_the_nssm_helper_relaxes_the_error_preference():
    """The helper only works if it actually restores/relaxes the preference."""
    for script in ("install-lan-service.ps1", "install-caddy.ps1"):
        src = _src(script)
        assert "function Invoke-Nssm" in src, f"{script} has no safe wrapper"
        body = src[src.index("function Invoke-Nssm"):]
        assert 'ErrorActionPreference = "Continue"' in body, (
            f"{script}: wrapper does not relax the preference")
        assert "finally" in body, (
            f"{script}: wrapper does not restore the preference")


def test_the_helper_is_defined_before_it_is_used():
    """PowerShell parses top-to-bottom; a call above the definition fails."""
    for script in ("install-lan-service.ps1", "install-caddy.ps1"):
        src = _src(script)
        define = src.index("function Invoke-Nssm")
        first_use = min(
            (src.index(u) for u in ("Invoke-Nssm -Quiet", "Invoke-Nssm install",
                                    "Invoke-Nssm set", "Invoke-Nssm start")
             if u in src), default=None)
        assert first_use is not None, f"{script}: helper defined but never used"
        assert define < first_use, (
            f"{script}: Invoke-Nssm is called before it is defined")


def test_nssm_calls_pass_an_explicit_array():
    """A PowerShell FUNCTION binds tokens starting with "-" as its own
    parameters — unlike `& $exe`, which passes them through. Wrapping nssm in
    a function therefore risks "-m" and "--server.port" being eaten. Passing
    one explicit array removes the ambiguity entirely.
    """
    for script in ("install-lan-service.ps1", "install-caddy.ps1"):
        for line in _code_lines(script):
            s = line.strip()
            if not s.startswith(("Invoke-Nssm", "$rc = Invoke-Nssm")):
                continue
            assert "@(" in s, (
                f"{script}: nssm call does not pass an explicit array, so a "
                f"'-' argument could bind as a function parameter:\n  {s}")


def test_a_failed_nssm_install_stops_the_script():
    """Every later `set` is futile once install has failed, and continuing
    would end with a 'done' message over a service that does not exist."""
    for script in ("install-lan-service.ps1", "install-caddy.ps1"):
        src = _src(script)
        i = src.index('Invoke-Nssm @("install"')
        after = src[i:i + 400]
        assert "$rc" in src[max(0, i - 10):i + 40], (
            f"{script}: install's exit code is not captured")
        assert "throw" in after, (
            f"{script}: a failed nssm install does not stop the script")


def test_caddy_checks_dns_before_claiming_success():
    """A proxied (orange-cloud) record means the CDN answers the ACME
    challenge instead of this machine, so no certificate is ever issued —
    and the failure mode is silence plus retries, not an error."""
    src = _src("install-caddy.ps1")
    assert "GetHostAddresses" in src, "no DNS resolution check"
    assert "DNS only" in src, "does not tell the operator how to fix a proxied record"
    # a representative slice of Cloudflare's published ranges
    for prefix in ('"104.16.', '"172.67.', '"162.159.'):
        assert prefix in src, f"proxy detection missing range {prefix}"


def test_the_domain_is_consistent_across_every_script():
    """The domain is a -Domain parameter, but the DEFAULT has to be one real
    domain in one form. The owner holds both eight-rock.com (live: real host,
    SPF/DKIM/DMARC, M365 mail) and eightrockcapital.com (A records still on
    the RFC 5737 placeholder 192.0.2.1). eight-rock.com is the live one.
    """
    expected = "workbench.eight-rock.com"
    for name in ("Caddyfile", "install-caddy.ps1", "install.ps1"):
        src = _src(name)
        assert expected in src, f"{name} does not default to {expected}"
        assert "eightrockcapital" not in src, (
            f"{name} mixes in the secondary domain")


def test_updater_swaps_instead_of_killing_in_service_mode():
    """NSSM auto-restarts a killed service instantly, so the old
    kill-the-ports flow left an instance running OLD code mid-sync with
    nothing restarting it afterwards - the stale-version pill back through
    a different door. In service mode the updater must skip the kill and
    finish with the one-at-a-time blue-green swap."""
    upd = (ROOT / "update-workbench.bat").read_text(encoding="utf-8")
    # detects both colours...
    assert "sc query WorkbenchBlue" in upd
    assert "sc query WorkbenchGreen" in upd
    # ...BEFORE the kill, and jumps past it when they exist
    assert "goto :synccode" in upd and "\n:synccode" in upd
    assert upd.index("sc query WorkbenchBlue") < upd.index("taskkill"), (
        "service detection must run before the port-kill")
    # ends with the swap, and a failed swap must not read as success
    assert "deploy-swap.ps1" in upd
    assert "swap stopped early" in upd


def test_updater_retries_dependency_sync_when_services_hold_locks():
    """Windows refuses to replace a .pyd a running service has loaded; the
    retry stops both colours first, and the final swap starts them again."""
    upd = (ROOT / "update-workbench.bat").read_text(encoding="utf-8")
    assert "sc stop WorkbenchBlue" in upd
    assert "sc stop WorkbenchGreen" in upd
    # rindex: the swap INVOCATION (a comment near the top also names the
    # script) must come after the locked-file retry, so it restarts
    # whatever the retry stopped.
    assert upd.index("sc stop WorkbenchBlue") < upd.rindex("deploy-swap.ps1")


def test_caddy_reports_the_port_forward_target():
    """The router forwards to a LAN address. Picking the wrong device from a
    list of DHCP leases presents as 'the site never comes up', so the machine
    that knows its own address should say it."""
    src = _src("install-caddy.ps1")
    assert "Get-NetIPAddress" in src, "does not report this machine's LAN IP"
    assert "DHCP" in src, "does not warn that an unreserved lease can move"
    assert "ipify" in src or "public IP" in src, "does not report the public IP"
    assert "MISMATCH" in src, "does not compare DNS against the real public IP"


def test_backup_never_prompts_for_a_password():
    """The backup task runs as SYSTEM at 02:15 — a password prompt hangs it
    forever, invisibly (found 2026-08-08: the task was never registered, and
    if it had been, `pg_dump -U postgres` would have prompted and hung).
    The script must take credentials from the app's .env non-interactively
    and fail loudly when pg_dump fails."""
    src = _src("backup.ps1")
    assert "DATABASE_URL" in src and "PGPASSWORD" in src
    assert re.search(r"-U \$dbUser", src), "pg_dump must use .env-derived user"
    assert not re.search(r"&\s*\$pgDump[^\n]*-U postgres\b", src), (
        "the pg_dump INVOCATION must never hardcode -U postgres")
    assert "LASTEXITCODE" in src, "a failed dump must throw, not report success"


def test_backup_local_dir_does_not_assume_a_second_disk():
    # The old D:\ default silently required hardware the host may not have.
    src = _src("backup.ps1")
    assert 'Test-Path "D:\\"' in src and "C:\\Backup\\8rw" in src
