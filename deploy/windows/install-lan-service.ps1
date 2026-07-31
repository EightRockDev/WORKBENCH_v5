<#
  Run the Workbench as a Windows SERVICE reachable on the office network.

  What it does (idempotent - safe to re-run):
    1. Installs NSSM (service manager) via winget if missing.
    2. Prompts for a workbench passcode (required before exposing the app
       beyond this machine) and writes ER_APP_PASSCODE + ER_DEV_LOGIN=1
       into .env.
    3. Registers/updates one BLUE-GREEN service (-Name/-Port): streamlit
       bound to 0.0.0.0:<port>, auto-start at boot, auto-restart on crash,
       logs to logs\service-<name>-*.log.
    4. Opens Windows Firewall TCP <port> for the private (LAN) profile only.

  Run it TWICE for zero-downtime deploys - once with the defaults
  (WorkbenchBlue / 8501), once with -Name WorkbenchGreen -Port 8502. Caddy
  load-balances the pair and deploy-swap.ps1 restarts them one at a time.
    5. Prints the address(es) to open from other devices.

  Run via the double-click wrapper install-service.bat (self-elevating).
  ASCII only - Windows PowerShell 5.1 reads .ps1 as ANSI.
#>
param(
    [string]$AppDir = "C:\WORKBENCH_V5",
    # Blue-green pair (deploy-swap.ps1 restarts these by name). Run once with
    # the defaults, then again with -Name WorkbenchGreen -Port 8502.
    [string]$Name   = "WorkbenchBlue",
    [int]$Port      = 8501,
    # 0.0.0.0 = reachable directly on the LAN (no reverse proxy).
    # 127.0.0.1 = Caddy fronts it; the port must NOT be exposed, so the
    # firewall rule is skipped automatically.
    [string]$BindAddress = "0.0.0.0",
    # Reuse the passcode already in .env without prompting. Used for the
    # SECOND colour so installing the pair asks once, not twice.
    [switch]$KeepPasscode
)
$lanMode = ($BindAddress -eq "0.0.0.0")
$ErrorActionPreference = "Stop"
$svc = $Name
Set-Location $AppDir

# deploy-swap.ps1 and deploy/windows/Caddyfile both hard-code this pair. A
# third name would install fine and then never be restarted or routed to, so
# refuse it rather than leave a service nothing knows about.
$known = @{ "WorkbenchBlue" = 8501; "WorkbenchGreen" = 8502 }
if (-not $known.ContainsKey($Name)) {
    throw ("Name must be WorkbenchBlue or WorkbenchGreen (got '{0}'). " +
           "Caddy and deploy-swap only know those two." -f $Name)
}
if ($known[$Name] -ne $Port) {
    throw ("{0} must run on port {1}, not {2} - Caddy routes by port." -f
           $Name, $known[$Name], $Port)
}

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from the install-service.bat wrapper (it elevates itself)."
}

function Step($m){ Write-Host "`n>> $m" -ForegroundColor Yellow }

# --- OneDrive guard: a synced folder corrupts live databases (spec 9.2) ---
if ($AppDir -match "OneDrive|Dropbox|Google Drive") {
    Write-Host ""
    Write-Host "STOP: the workbench is inside a cloud-synced folder:" -ForegroundColor Red
    Write-Host "      $AppDir" -ForegroundColor Red
    Write-Host "Move it to a plain local folder (e.g. C:\WORKBENCH_V5) first." -ForegroundColor Red
    exit 1
}

# --- 1. NSSM --------------------------------------------------------------
Step "Checking NSSM (service manager)"
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) {
    winget install --id NSSM.NSSM -e --accept-source-agreements --accept-package-agreements | Out-Null
    $nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
    if (-not $nssm) {
        # winget installs may need a fresh PATH; look in the usual spots.
        $guess = Get-ChildItem "$env:ProgramFiles\nssm*","$env:LOCALAPPDATA\Microsoft\WinGet\Packages\NSSM*" -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($guess) { $nssm = $guess.FullName }
    }
}
if (-not $nssm) { throw "Could not install NSSM. Install it from nssm.cc and re-run." }
Write-Host "   NSSM: $nssm"

# --- 2. Passcode ----------------------------------------------------------
Step "Workbench passcode (required - the app will be reachable on your network)"
$envPath = Join-Path $AppDir ".env"
$existing = ""
if (Test-Path $envPath) {
    $line = Get-Content $envPath | Where-Object { $_ -match "^ER_APP_PASSCODE=" } | Select-Object -First 1
    if ($line) { $existing = $line.Substring("ER_APP_PASSCODE=".Length) }
}
if ($KeepPasscode) {
    if (-not $existing) {
        throw ("-KeepPasscode was given but no ER_APP_PASSCODE exists in " +
               "$envPath. Install the first colour (WorkbenchBlue) before " +
               "the second, so a passcode is set once.")
    }
    Write-Host "   Reusing the passcode already in .env."
    $pc = $existing
} else {
    if ($existing) {
        Write-Host "   A passcode is already set. Press Enter to keep it, or type a new one."
    }
    $pc = Read-Host "Passcode"
    if (-not $pc -and $existing) { $pc = $existing }
}
if (-not $pc -or $pc.Trim().Length -lt 6) {
    Write-Host "Passcode must be at least 6 characters. Nothing changed." -ForegroundColor Red
    exit 1
}
$keep = @()
if (Test-Path $envPath) {
    $keep = Get-Content $envPath | Where-Object { $_ -notmatch "^(ER_APP_PASSCODE|ER_DEV_LOGIN)=" }
}
$out = @($keep) + ("ER_APP_PASSCODE=" + $pc.Trim()) + "ER_DEV_LOGIN=1"
Set-Content -Path $envPath -Value $out -Encoding ASCII
Write-Host "   Saved to .env (passcode hidden)."

# --- 3. Service -----------------------------------------------------------
Step "Registering service '$svc'"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    # uv installs per-user; check this user's spots, then every profile on
    # the machine (the app may have been set up under a different account).
    $spots = @("$env:USERPROFILE\.local\bin\uv.exe",
               "$env:LOCALAPPDATA\Programs\uv\uv.exe",
               "$env:USERPROFILE\.cargo\bin\uv.exe")
    $spots += Get-ChildItem "C:\Users" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { @((Join-Path $_.FullName ".local\bin\uv.exe"),
                           (Join-Path $_.FullName "AppData\Local\Programs\uv\uv.exe")) }
    $uv = $spots | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $uv) { throw "uv.exe not found anywhere. Run start-workbench.bat once (it auto-installs uv), then re-run this." }

$logDir = Join-Path $AppDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

& $nssm stop $svc 2>$null | Out-Null
& $nssm remove $svc confirm 2>$null | Out-Null
& $nssm install $svc $uv run python -m streamlit run app.py --server.address $BindAddress --server.port $Port --server.headless true
& $nssm set $svc AppDirectory $AppDir
& $nssm set $svc DisplayName ("Eight Rock Workbench (" + $Name + ")")
& $nssm set $svc Description ("Eight Rock Workbench v5 (Streamlit) on port " + $Port + " - auto-starts with Windows")
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 5000
& $nssm set $svc AppStdout (Join-Path $logDir ("service-" + $Name + "-out.log"))
& $nssm set $svc AppStderr (Join-Path $logDir ("service-" + $Name + "-err.log"))
& $nssm set $svc AppRotateFiles 1
& $nssm set $svc AppRotateBytes 10485760
# The service runs as SYSTEM - give it its own environment dir outside the
# app folder, so it never fights user accounts over .venv ownership. Each
# colour gets a SEPARATE dir: sharing one means both services can `uv run`
# into the same tree at once during a swap and corrupt it mid-sync.
& $nssm set $svc AppEnvironmentExtra ("UV_PROJECT_ENVIRONMENT=C:\ProgramData\EightRockWorkbench\venv-" + $Name)
& $nssm start $svc | Out-Null
Write-Host "   Service installed and started."

# --- 4. Firewall (LAN only) ----------------------------------------------
$fwName = "Eight Rock Workbench (" + $Name + ")"
if ($lanMode) {
    Step ("Opening firewall TCP " + $Port + " (private networks only)")
    Remove-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $fwName -Direction Inbound `
        -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Domain | Out-Null
} else {
    # Caddy owns the public side. Remove any rule a previous LAN-mode run
    # left behind, so the app port does not stay open to the network.
    Step ("Closing firewall TCP " + $Port + " (Caddy fronts this instance)")
    Remove-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue
}

# --- 5. Addresses ---------------------------------------------------------
if ($lanMode) {
    Step "Done. Open the workbench from any device on your network:"
    Write-Host ("   This computer:  http://localhost:" + $Port) -ForegroundColor Green
    Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp,Manual -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
        ForEach-Object { Write-Host ("   Other devices:  http://" + $_.IPAddress + ":" + $Port) -ForegroundColor Green }
} else {
    Step "Done. This instance listens on localhost only - reach it through Caddy."
    Write-Host ("   Local health check:  http://127.0.0.1:" + $Port + "/_stcore/health") -ForegroundColor Green
}
Write-Host ""
Write-Host "Everyone gets the passcode prompt first. The service starts itself" -ForegroundColor Cyan
Write-Host "after every reboot - no more start-workbench window needed." -ForegroundColor Cyan
Write-Host "(start-workbench.bat still works for a manual console run.)" -ForegroundColor Cyan

if ($Name -eq "WorkbenchBlue") {
    Write-Host ""
    Write-Host "NEXT: install the GREEN half so deploys have no downtime:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File deploy\windows\install-lan-service.ps1 -Name WorkbenchGreen -Port 8502" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "Both colours installed. Verify with: .\deploy\windows\deploy-swap.ps1" -ForegroundColor Green
}
