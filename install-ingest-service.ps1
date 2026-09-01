# Repairs the ingest mount and installs the API as a Windows service, so it
# starts itself after a reboot and cannot be lost to a console window closing.
# Run from install-ingest-service.bat (which handles the admin prompt).

$ErrorActionPreference = 'Continue'
$AppDir  = 'C:\WORKBENCH_V5'
$Py      = "$AppDir\.venv\Scripts\python.exe"
$SvcName = 'WorkbenchIngest'
$Port    = 8600

function Section($t) { "`n===== $t =====" }

Section "WHEN"
Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'

# --- 1. Put the switch-on line back into api_server.py ----------------------
Section "1. REPAIRING THE INGEST MOUNT"
if (-not (Test-Path "$AppDir\api_server.py")) {
    "STOPPED: no api_server.py in $AppDir"; exit 1
}
if (-not (Test-Path "$AppDir\setup_ingest_helper.py")) {
    "STOPPED: setup_ingest_helper.py is missing - copy the ingest files in first"; exit 1
}

$has = Select-String -Path "$AppDir\api_server.py" -Pattern 'include_ingest_routes' -Quiet
if ($has) {
    "api_server.py already has the switch-on line."
} else {
    "api_server.py is MISSING the switch-on line - this is why /v1/ingest returns 404."
    "Most likely a git pull or update-workbench.bat restored the file."
    Push-Location $AppDir
    & $Py setup_ingest_helper.py patch 2>&1 | ForEach-Object { "  $_" }
    Pop-Location
}

# --- 2. Prove the routes actually load before installing anything -----------
Section "2. CHECKING THE ROUTES LOAD"
Push-Location $AppDir
$check = & $Py -c @"
import sys, traceback
sys.path.insert(0, r'$AppDir')
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api_server
    c = TestClient(api_server.app)
    r = c.get('/v1/ingest/health')
    print('HEALTH', r.status_code, r.text[:120])
except Exception:
    traceback.print_exc()
"@ 2>&1
Pop-Location
$check | ForEach-Object { "  $_" }

# Collapse to a single string first. Against an array, -match returns the
# matching ELEMENTS, and any non-empty array is truthy - so testing an array
# directly is always wrong here.
$checkText = ($check | Out-String)

if ($checkText -notmatch 'HEALTH 200') {
    ""
    "STOPPED: the routes still do not answer. Nothing was installed."
    "Send this whole report to Claude."
    exit 1
}
"Routes load correctly."

# --- 3. Clear whatever is holding the port ----------------------------------
Section "3. FREEING PORT $Port"
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
if ($conn) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    "Port $Port is held by PID $($conn.OwningProcess) ($($proc.ProcessName))."
    $svc = Get-CimInstance Win32_Service -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
    if ($svc) {
        "  That is the service '$($svc.Name)' - stopping it."
        Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
    } else {
        "  That is a console window (run-api.bat). Closing it."
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
} else {
    "Nothing is on port $Port."
}

# --- 4. Install or update the service ---------------------------------------
Section "4. INSTALLING THE SERVICE"
$nssm = 'C:\Users\brian2\AppData\Local\Microsoft\WinGet\Links\nssm.exe'
if (-not (Test-Path $nssm)) { $nssm = (Get-Command nssm.exe -ErrorAction SilentlyContinue).Source }
if (-not $nssm -or -not (Test-Path $nssm)) {
    ""
    "STOPPED: nssm.exe not found - that is the tool that turns a program into a"
    "service, and it is what Caddy already uses here. Send this to Claude."
    exit 1
}
"nssm: $nssm"

$existing = Get-Service -Name $SvcName -ErrorAction SilentlyContinue
if ($existing) {
    "Service '$SvcName' already exists - updating it."
    Stop-Service -Name $SvcName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
} else {
    "Creating service '$SvcName'."
    & $nssm install $SvcName $Py 2>&1 | ForEach-Object { "  $_" }
}

& $nssm set $SvcName Application    $Py                                            | Out-Null
& $nssm set $SvcName AppParameters  "-m uvicorn api_server:app --host 127.0.0.1 --port $Port" | Out-Null
& $nssm set $SvcName AppDirectory   $AppDir                                        | Out-Null
& $nssm set $SvcName DisplayName    "Eight Rock Workbench Ingest API"              | Out-Null
& $nssm set $SvcName Description    "Receives deals from the daily SharePoint sweep and updates the property list." | Out-Null
& $nssm set $SvcName Start          SERVICE_AUTO_START                             | Out-Null
& $nssm set $SvcName AppStdout      "$AppDir\logs\ingest-api.log"                  | Out-Null
& $nssm set $SvcName AppStderr      "$AppDir\logs\ingest-api.log"                  | Out-Null
& $nssm set $SvcName AppRotateFiles 1                                              | Out-Null
& $nssm set $SvcName AppRotateBytes 10485760                                       | Out-Null
# Come back on its own if it ever crashes.
& $nssm set $SvcName AppExit Default Restart                                       | Out-Null
& $nssm set $SvcName AppRestartDelay 5000                                          | Out-Null

New-Item -ItemType Directory -Path "$AppDir\logs" -Force | Out-Null
"Service configured: auto-start, restarts itself on failure, logs to logs\ingest-api.log"

# --- 5. Start it and check --------------------------------------------------
Section "5. STARTING IT"
Start-Service -Name $SvcName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 8
$svc = Get-Service -Name $SvcName -ErrorAction SilentlyContinue
"Status: $($svc.Status)   StartType: $($svc.StartType)"

Section "6. DOES IT ANSWER"
try {
    $r = Invoke-RestMethod "http://127.0.0.1:$Port/v1/ingest/health" -TimeoutSec 15
    "LOCAL  : ok=$($r.ok) version=$($r.version) inbox=$($r.inbox | ConvertTo-Json -Compress)"
} catch {
    "LOCAL  : FAILED - $($_.Exception.Message)"
    if (Test-Path "$AppDir\logs\ingest-api.log") {
        "last lines of the log:"
        Get-Content "$AppDir\logs\ingest-api.log" -Tail 15 | ForEach-Object { "    $_" }
    }
}

# The public address is the one that actually matters - the sweep uses it.
# NOTE this machine's hosts file may answer locally for eight-rock.com names,
# so treat a pass here as suggestive, not proof.
try {
    $r2 = Invoke-WebRequest "https://ingest.eight-rock.com/v1/ingest/health?svc=1" `
            -TimeoutSec 25 -UseBasicParsing
    "PUBLIC : $($r2.StatusCode) $($r2.Content.Substring(0,[Math]::Min(120,$r2.Content.Length)))"
} catch {
    "PUBLIC : FAILED - $($_.Exception.Message)"
}

Section "DONE"
"The ingest API is now a Windows service. It starts automatically after a"
"reboot and restarts itself if it stops. You no longer need run-api.bat."
"To check on it later: Services > Eight Rock Workbench Ingest API"
