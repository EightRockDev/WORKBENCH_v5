# ===========================================================================
#  Zero-downtime deploy swap (Section 9 step 2, owner commitment 2026-07-29)
#  Restarts the BLUE (8501) and GREEN (8502) app services ONE AT A TIME,
#  waiting for the restarted instance to pass Streamlit's health check
#  before touching the other - Caddy routes around whichever is down, so
#  users never see a restart. Pure ASCII (PowerShell 5.1 rule).
#
#  Prereq: two NSSM services named WorkbenchBlue / WorkbenchGreen running
#  streamlit on ports 8501 / 8502 (install-lan-service.ps1 -Name/-Port).
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
Write-Host "[swap] zero-downtime deploy complete - both colors on new code."
exit 0
