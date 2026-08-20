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
    # detects both colours (now via a loop, not two literal sc calls)...
    assert "WorkbenchBlue WorkbenchGreen" in upd
    # ...BEFORE the kill, and jumps past it when they are RUNNING
    assert "goto :synccode" in upd and "\n:synccode" in upd
    assert upd.index("WorkbenchBlue WorkbenchGreen") < upd.index("taskkill"), (
        "service detection must run before the port-kill")
    # The hazard this test protects - NSSM instantly restarting a killed
    # service with the old code - only exists while a service is actually
    # RUNNING. Gating on mere existence stranded the owner three releases
    # back on 2026-08-15: the services exist, cannot start, and a stray
    # process served stale code while the updater reported success. So the
    # skip must be conditioned on RUNNING, not on existence.
    assert 'findstr /c:"RUNNING"' in upd, (
        "service mode must require RUNNING, not merely that the service exists")
    assert "INSTALLED but NOT RUNNING" in upd, (
        "an installed-but-dead service must be called out, not silently "
        "treated as if it were serving")
    # ends with the swap, and a failed swap must not read as success
    assert "deploy-swap.ps1" in upd
    assert "swap stopped early" in upd
    # and the update must state whether the NEW code is actually being served
    assert "STALE" in upd and "PREPIDS" in upd, (
        "the updater must verify the serving process changed, not just that "
        "the files on disk did")


def test_launching_the_app_also_updates_it():
    """The owner ran three-plus releases behind while every fix shipped on
    time, because update-workbench.bat is a SEPARATE file he has to remember
    to run, and start-workbench.bat was happy to launch stale code forever
    without saying so. Launching is updating now."""
    start = (ROOT / "start-workbench.bat").read_text(encoding="utf-8")
    assert "git fetch origin" in start, "the launcher does not fetch"
    assert "git checkout -f origin/main" in start, (
        "the launcher fetches but never applies the new code")
    # ...and it must still launch when the sync cannot happen. A launcher
    # that refuses to launch is a worse failure than being a day behind.
    assert "Could not check" in start, (
        "a failed sync must be reported and then ignored, never fatal")
    # rindex: a comment further up quotes the launch command when explaining
    # why the kill matches python.exe. The real INVOCATION is the last one.
    assert start.index("git fetch origin") < start.rindex("streamlit run app.py"), (
        "the sync must run before the app is launched, not after")


def test_the_launcher_states_the_version_it_is_about_to_run():
    """Three exchanges were spent establishing which version was running.
    The window that starts the app should simply say so."""
    start = (ROOT / "start-workbench.bat").read_text(encoding="utf-8")
    assert 'findstr /C:"WORKBENCH_VERSION =" config.py' in start
    assert "You are running WORKBENCH" in start


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


def test_backup_role_setup_uses_the_trust_flip_and_bypassrls():
    """setup-backup.ps1 must create a read-only BYPASSRLS role without needing
    the postgres password (same trust-flip setup-db.ps1 uses), restore the
    auth config in a finally, upsert .env, and PROVE the path by running a
    dump (owner directive 2026-08-09: make it happen, no manual psql)."""
    src = _src("setup-backup.ps1")
    assert "BYPASSRLS" in src and "pg_read_all_data" in src
    assert "trust" in src and "finally" in src.lower()
    assert "Restoring original authentication config" in src   # restore path
    assert "ER_BACKUP_DATABASE_URL" in src
    assert "run_backup.py" in src        # proves the path end to end
    # Upsert, never clobber .env (the shared-file lesson).
    assert "-ne $managedKey" in src and ".bak" in src


def test_login_diagnostic_covers_the_known_oauth_failure():
    """2026-08-10 root cause: Caddy fronts blue+green with no session
    affinity, so the OAuth state set on one instance fails the CSRF check
    on the other and /oauth2callback 500s. The diagnostic must check the
    config Caddy actually RUNS (Caddyfile.active), not the repo template -
    the fix lives in the template and only reaches the host when
    install-caddy regenerates it."""
    d = (ROOT / "diagnose-login.bat").read_text(encoding="utf-8")
    assert "Caddyfile.active" in d, "must inspect the live config, not the template"
    assert "lb_policy cookie" in d
    assert "install-caddy.ps1" in d, "must name the remedy"
    # and it must never print secret values
    assert "cookie_secret" in d and "never values" in d.lower()


def test_login_diagnostic_flags_upstream_mismatch():
    """A second route to the same 500: the live config forwards to an
    instance that is not running."""
    d = (ROOT / "diagnose-login.bat").read_text(encoding="utf-8")
    assert "8501" in d and "8502" in d
    assert "MISMATCH" in d


# ---------------------------------------------------------------------------
# The five silent days (2026-08-15..19): overlap was fatal, the launcher
# left no evidence, and every pre-publish failure was local-only.
# ---------------------------------------------------------------------------

def test_a_locked_log_stands_down_instead_of_dying():
    """A new cycle colliding with a finishing one is NORMAL. Treating the
    busy log file as fatal killed the chain on 2026-08-15 and it stayed
    dead for five days."""
    bat = (ROOT / "autopilot.bat").read_text(encoding="utf-8")
    assert "STANDING DOWN" in bat, "overlap must be named, not called FAILED"
    assert ":standdown" in bat and ":try_log" in bat
    # the retry sleeps via ping: `timeout` dies in a non-interactive session
    assert "ping -n" in bat, "retry loop must not depend on `timeout`"
    # stand-down is not an error - exit 0 so nothing upstream panics
    i = bat.index(":standdown")
    assert "exit /b 0" in bat[i:i + 700], "standing down must exit 0"


def test_launcher_failures_reach_github():
    """Every failure before the first publish used to be written to a LOCAL
    file only - from outside, indistinguishable from a machine that is off.
    The launcher now pushes its status file best-effort on every non-DONE
    path."""
    bat = (ROOT / "autopilot.bat").read_text(encoding="utf-8")
    assert ":publish_status" in bat
    body = bat[bat.index(":publish_status"):]
    assert "git push origin main" in body
    # both the uv failure and the stand-down must call it
    for label in (":fail_uv", ":standdown"):
        # the LABEL, not a goto that references it
        i = bat.index("\n" + label) + 1
        section = bat[i:i + 900]
        assert "call :publish_status" in section, f"{label} does not publish"


def test_the_hidden_launcher_leaves_evidence():
    """The scheduled task reported 'successfully finished' while the cycle
    never ran, and nothing on disk said which link broke. The launcher must
    write its breadcrumb BEFORE starting the cycle and record the cycle's
    exit code after."""
    vbs = (ROOT / "autopilot-hidden.vbs").read_text(encoding="utf-8")
    assert "launcher-last.txt" in vbs
    assert vbs.index("launcher started") < vbs.index("shell.Run("), (
        "the breadcrumb must be written before the launch, or a hung "
        "launch leaves no trace")
    assert "cycle exited" in vbs
    # waiting is what makes the exit code real evidence
    assert ", 0, True)" in vbs, "the launcher must wait for the cycle"


def test_the_task_registration_is_scripted_not_hand_typed():
    """The task's action was hand-typed once and the chain died invisibly.
    fix-autopilot-task.bat rebuilds it deterministically; the updater's
    ensure-block must agree with it on every choice that mattered."""
    fix = (ROOT / "fix-autopilot-task.bat").read_text(encoding="utf-8")
    upd = (ROOT / "update-workbench.bat").read_text(encoding="utf-8")
    for src, name in ((fix, "fix-autopilot-task.bat"),
                      (upd, "update-workbench.bat")):
        assert "EightRockWorkbenchAutopilot" in src
        assert "autopilot-hidden.vbs" in src, (
            f"{name}: the task must start the silent launcher")
        assert "/SC HOURLY" in src, f"{name}: hourly restart net"
        assert "/RU" not in src, (
            f"{name}: the task must run as the logged-on user - session 0 "
            "loses the GitHub credentials the publish depends on")
    # the updater must never fall back to the visible-window action
    ensure = upd[upd.index("Ensuring the Autopilot"):]
    assert '/TR "wscript.exe' in ensure, (
        "update-workbench.bat registers autopilot.bat directly - that is "
        "the popup window and the pre-V5.62 outage path")


def test_the_heartbeat_is_the_first_step():
    """Liveness must be published before any real work, so a dead chain is
    distinguishable from a slow first step within seconds."""
    import scripts.autopilot_run as run
    assert run.STEPS[0][0] == "heartbeat", "heartbeat must run first"
    assert run.STEPS[0][2] == "heartbeat.txt"


def test_the_heartbeat_cannot_fail(capsys):
    """A heartbeat that can crash poisons the signal it exists to provide."""
    import scripts.heartbeat as hb
    assert hb.main() == 0
    out = capsys.readouterr().out
    assert "ALIVE" in out
    assert "fix-autopilot-task.bat" in out, (
        "the heartbeat must say what to run when it goes stale")
