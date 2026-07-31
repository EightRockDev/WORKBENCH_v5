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
    Restart-Service -Name $name -Force
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
