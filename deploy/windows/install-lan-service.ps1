<#
  Run the Workbench as a Windows SERVICE reachable on the office network.

  What it does (idempotent - safe to re-run):
    1. Installs NSSM (service manager) via winget if missing.
    2. Prompts for a workbench passcode (required before exposing the app
       beyond this machine) and writes ER_APP_PASSCODE + ER_DEV_LOGIN=1
       into .env.
    3. Registers/updates service "EightRockWorkbench": streamlit bound to
       0.0.0.0:8501, auto-start at boot, auto-restart on crash, logs to
       logs\service-*.log.
    4. Opens Windows Firewall TCP 8501 for the private (LAN) profile only.
    5. Prints the address(es) to open from other devices.

  Run via the double-click wrapper install-service.bat (self-elevating).
  ASCII only - Windows PowerShell 5.1 reads .ps1 as ANSI.
#>
param([string]$AppDir = "C:\WORKBENCH_V5")
$ErrorActionPreference = "Stop"
$svc = "EightRockWorkbench"
Set-Location $AppDir

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
if ($existing) {
    Write-Host "   A passcode is already set. Press Enter to keep it, or type a new one."
}
$pc = Read-Host "Passcode"
if (-not $pc -and $existing) { $pc = $existing }
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
& $nssm install $svc $uv run python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
& $nssm set $svc AppDirectory $AppDir
& $nssm set $svc DisplayName "Eight Rock Workbench"
& $nssm set $svc Description "Eight Rock Workbench v5 (Streamlit) - auto-starts with Windows"
& $nssm set $svc Start SERVICE_AUTO_START
& $nssm set $svc AppExit Default Restart
& $nssm set $svc AppRestartDelay 5000
& $nssm set $svc AppStdout (Join-Path $logDir "service-out.log")
& $nssm set $svc AppStderr (Join-Path $logDir "service-err.log")
& $nssm set $svc AppRotateFiles 1
& $nssm set $svc AppRotateBytes 10485760
# The service runs as SYSTEM - give it its own environment dir too, outside
# the app folder, so it never fights user accounts over .venv ownership.
& $nssm set $svc AppEnvironmentExtra "UV_PROJECT_ENVIRONMENT=C:\ProgramData\EightRockWorkbench\venv"
& $nssm start $svc | Out-Null
Write-Host "   Service installed and started."

# --- 4. Firewall (LAN only) ----------------------------------------------
Step "Opening firewall TCP 8501 (private networks only)"
Remove-NetFirewallRule -DisplayName "Eight Rock Workbench" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "Eight Rock Workbench" -Direction Inbound `
    -Action Allow -Protocol TCP -LocalPort 8501 -Profile Private,Domain | Out-Null

# --- 5. Addresses ---------------------------------------------------------
Step "Done. Open the workbench from any device on your network:"
Write-Host "   This computer:  http://localhost:8501" -ForegroundColor Green
Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp,Manual -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
    ForEach-Object { Write-Host ("   Other devices:  http://" + $_.IPAddress + ":8501") -ForegroundColor Green }
Write-Host ""
Write-Host "Everyone gets the passcode prompt first. The service starts itself" -ForegroundColor Cyan
Write-Host "after every reboot - no more start-workbench window needed." -ForegroundColor Cyan
Write-Host "(start-workbench.bat still works for a manual console run.)" -ForegroundColor Cyan
