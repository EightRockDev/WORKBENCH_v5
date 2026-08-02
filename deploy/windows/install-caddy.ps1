<#
  Install Caddy and run it as a Windows service against our Caddyfile.

  Split out of install.ps1 so the HTTPS front door can be set up on its own,
  after the app services are already running - which is the order the go-live
  sequence actually follows.

  ASCII only - Windows PowerShell 5.1 reads .ps1 as ANSI.
#>
param(
    [string]$AppDir = "C:\WORKBENCH_V5",
    [string]$Domain = "workbench.eight-rock.com",
    [string]$Email  = "ops@eight-rock.com"
)
$ErrorActionPreference = "Stop"
Set-Location $AppDir

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from install-caddy.bat (it elevates itself)."
}

function Step($m){ Write-Host "`n>> $m" -ForegroundColor Yellow }

# --- 1. Caddy ------------------------------------------------------------
Step "Checking Caddy"
$caddy = (Get-Command caddy -ErrorAction SilentlyContinue).Source
if (-not $caddy) {
    Write-Host "   Installing via winget..."
    winget install --id CaddyServer.Caddy -e --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    $caddy = (Get-Command caddy -ErrorAction SilentlyContinue).Source
    if (-not $caddy) {
        $guess = Get-ChildItem "$env:ProgramFiles\Caddy*","$env:LOCALAPPDATA\Microsoft\WinGet\Packages\CaddyServer*" `
                     -Recurse -Filter caddy.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($guess) { $caddy = $guess.FullName }
    }
}
if (-not $caddy) { throw "Could not install Caddy. Download it from caddyserver.com/download, put caddy.exe on PATH, and re-run." }
Write-Host "   Caddy: $caddy"

# --- 2. Config -----------------------------------------------------------
Step "Writing Caddyfile.active for $Domain"
$src = Join-Path $AppDir "deploy\windows\Caddyfile"
$act = Join-Path $AppDir "deploy\windows\Caddyfile.active"
if (-not (Test-Path $src)) { throw "Missing $src" }
(Get-Content $src) `
    -replace "workbench\.eight-rock\.com", $Domain `
    -replace "ops@eight-rock\.com", $Email `
    -replace "C:\\WORKBENCH_V5", $AppDir.TrimEnd('.').TrimEnd('\') |
    Set-Content $act -Encoding ASCII
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "logs") | Out-Null

# Fail early on a malformed config rather than at certificate time.
& $caddy validate --config $act --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw "Caddyfile.active failed validation - not registering the service." }
Write-Host "   Config valid."

# --- 3. Are the app services actually up? --------------------------------
Step "Checking the upstreams Caddy will route to"
foreach ($p in 8501, 8502) {
    $ok = $false
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:$p/_stcore/health"
        $ok = ($r.StatusCode -eq 200)
    } catch { }
    if ($ok) { Write-Host ("   port {0}: healthy" -f $p) -ForegroundColor Green }
    else {
        Write-Host ("   port {0}: NOT responding - run install-service.bat first" -f $p) -ForegroundColor Red
    }
}

# --- 4. Service ----------------------------------------------------------
Step "Registering the Caddy service"
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) { throw "NSSM not found - run install-service.bat first (it installs NSSM)." }

# ---------------------------------------------------------------------------
# Safe NSSM invocation
# ---------------------------------------------------------------------------
# `nssm stop <name>` on a service that does not exist yet writes "Can't open
# service!" to STDERR. Under Windows PowerShell 5.1, redirecting a native
# command's stderr (`2>$null`) turns that output into a NativeCommandError
# record, and with $ErrorActionPreference = "Stop" that becomes TERMINATING.
# The result: the install died on its own idempotent cleanup step, before it
# had installed anything, on every machine where the service did not already
# exist - which is every first install.
#
# So native calls run with the preference relaxed and their exit code checked
# explicitly. `-Quiet` is for the stop/remove pair, whose failure is expected
# and meaningless on a clean box.
function Invoke-Nssm {
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string[]]$NssmArgs,
        [switch]$Quiet
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $nssm @NssmArgs 2>&1
        $code = $LASTEXITCODE
        if ($code -ne 0 -and -not $Quiet) {
            Write-Host ("   nssm " + ($NssmArgs -join " ") + " -> exit " + $code) -ForegroundColor Yellow
            $out | ForEach-Object { Write-Host ("   " + $_) -ForegroundColor Yellow }
        }
        return $code
    } finally {
        $ErrorActionPreference = $prev
    }
}


Invoke-Nssm -Quiet @("stop", "Caddy") | Out-Null
Invoke-Nssm -Quiet @("remove", "Caddy", "confirm") | Out-Null
$rc = Invoke-Nssm @("install", "Caddy", $caddy, "run", "--config", $act, "--adapter", "caddyfile")
if ($rc -ne 0) { throw ("nssm install failed (exit " + $rc + ") - see the message above.") }
Invoke-Nssm @("set", "Caddy", "AppDirectory", $AppDir) | Out-Null
Invoke-Nssm @("set", "Caddy", "DisplayName", "Caddy (Eight Rock Workbench HTTPS)") | Out-Null
Invoke-Nssm @("set", "Caddy", "Start", "SERVICE_AUTO_START") | Out-Null
Invoke-Nssm @("set", "Caddy", "AppStdout", (Join-Path $AppDir "logs\caddy-out.log")) | Out-Null
Invoke-Nssm @("set", "Caddy", "AppStderr", (Join-Path $AppDir "logs\caddy-err.log")) | Out-Null
Invoke-Nssm @("start", "Caddy") | Out-Null

# --- 5. Firewall ---------------------------------------------------------
Step "Opening firewall TCP 80/443"
foreach ($pair in @(@("Workbench HTTP", 80), @("Workbench HTTPS", 443))) {
    Remove-NetFirewallRule -DisplayName $pair[0] -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $pair[0] -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $pair[1] | Out-Null
}

Step "Done."
Write-Host "   https://$Domain" -ForegroundColor Green
Write-Host ""
Write-Host "A certificate is only issued once BOTH are true:" -ForegroundColor Cyan
Write-Host "  1. $Domain resolves to this network's public IP" -ForegroundColor Cyan
Write-Host "  2. your router forwards TCP 80 AND 443 to this PC" -ForegroundColor Cyan
Write-Host "Watch progress: type logs\caddy-err.log" -ForegroundColor Cyan
