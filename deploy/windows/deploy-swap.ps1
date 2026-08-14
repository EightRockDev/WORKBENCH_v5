# ===========================================================================
#  Zero-downtime deploy swap (Section 9 step 2, owner commitment 2026-07-29)
#  Restarts the BLUE (8501) and GREEN (8502) app services ONE AT A TIME,
#  waiting for the restarted instance to pass Streamlit's health check
#  before touching the other - Caddy routes around whichever is down, so
#  users never see a restart. Pure ASCII (PowerShell 5.1 rule).
#
#  Prereq: two NSSM services named WorkbenchBlue / WorkbenchGreen running
#  streamlit on ports 8501 / 8502. Create them with:
#    install-lan-service.ps1                                 (blue, 8501)
#    install-lan-service.ps1 -Name WorkbenchGreen -Port 8502 (green)
#  Safe to run any time; exits nonzero if an instance never comes healthy.
# ===========================================================================
$ErrorActionPreference = "Stop"

# Restart-Service needs administrator rights. Without them it fails with the
# opaque "Cannot open WorkbenchBlue service on computer '.'" and the swap dies
# mid-way, leaving the old code serving (owner report 2026-08-04). Fail early
# with a clear instruction instead. (update-workbench.bat self-elevates, so the
# normal path already runs elevated; this guards a direct run.)
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[swap] Administrator rights are required to restart the services."
    Write-Host "[swap] Open PowerShell as Administrator and re-run, or just run"
    Write-Host "[swap] update-workbench.bat (it elevates itself)."
    exit 1
}

function Wait-Healthy([int]$Port) {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 `
                -Uri ("http://127.0.0.1:{0}/_stcore/health" -f $Port)
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

# Count what is actually installed BEFORE touching anything. Skipping missing
# services and then exiting 0 reported "zero-downtime deploy complete" on a
# machine where no service existed at all - a false green.
$installed = @()
foreach ($pair in @(@("WorkbenchBlue", 8501), @("WorkbenchGreen", 8502))) {
    if (Get-Service -Name $pair[0] -ErrorAction SilentlyContinue) { $installed += $pair[0] }
}
if ($installed.Count -eq 0) {
    Write-Host "[swap] NEITHER WorkbenchBlue nor WorkbenchGreen is installed."
    Write-Host "[swap] Nothing was restarted. Install them first:"
    Write-Host "[swap]   install-lan-service.ps1                                  (blue, 8501)"
    Write-Host "[swap]   install-lan-service.ps1 -Name WorkbenchGreen -Port 8502   (green)"
    exit 1
}
if ($installed.Count -eq 1) {
    Write-Host ("[swap] WARNING: only {0} is installed." -f $installed[0])
    Write-Host "[swap] Restarting it WILL drop connections - there is no second"
    Write-Host "[swap] colour for Caddy to route to. Install the other half for"
    Write-Host "[swap] real zero-downtime deploys."
}

foreach ($pair in @(@("WorkbenchBlue", 8501), @("WorkbenchGreen", 8502))) {
    $name = $pair[0]; $port = $pair[1]
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Write-Host ("[swap] service {0} not installed - skipping" -f $name)
        continue
    }
    Write-Host ("[swap] restarting {0} (port {1})..." -f $name, $port)
    try {
        Restart-Service -Name $name -Force -ErrorAction Stop
    } catch {
        # "Failed to start service" alone sent the owner into a 45-minute
        # dig 45 minutes before an investor demo (2026-08-13). Name the
        # cause here, and leave the other colour serving.
        Write-Host ("[swap] {0} FAILED TO START: {1}" -f $name, $_.Exception.Message)
        Write-Host "[swap] the other colour is untouched and still serving."
        Write-Host "[swap] for the actual reason (and the remedy), run:"
        Write-Host "[swap]   powershell -ExecutionPolicy Bypass -File deploy\windows\diagnose-service.ps1"
        exit 1
    }
    if (-not (Wait-Healthy $port)) {
        Write-Host ("[swap] {0} did NOT come healthy - stopping swap so the" -f $name)
        Write-Host "[swap] other color keeps serving. Investigate before rerunning."
        exit 1
    }
    Write-Host ("[swap] {0} healthy - users were served by the other color" -f $name)
}
if ($installed.Count -eq 2) {
    Write-Host "[swap] zero-downtime deploy complete - both colours on new code."
} else {
    Write-Host ("[swap] {0} restarted on new code (single-colour, brief downtime)." -f $installed[0])
}
exit 0
