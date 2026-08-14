# =====================================================================
#  Why won't WorkbenchBlue / WorkbenchGreen start?
#
#  Owner hit this 2026-08-13 45 minutes before an investor demo: the
#  blue/green swap failed with nothing but "Failed to start service", and
#  the real cause was buried in a log nobody thought to open -
#      error: Failed to spawn: `python`
#        Caused by: An Application Control policy has blocked this file.
#                   (os error 4551)
#  The service runs as SYSTEM against its own uv environment under
#  C:\ProgramData; the copy of python.exe uv puts there is not covered by
#  the machine's Application Control policy, while the SAME app launched
#  from the owner's account has run fine for weeks.
#
#  This script reads the evidence and names the remedy instead of making
#  anyone guess. Read-only: it changes nothing.
# =====================================================================
param(
    [string]$AppDir = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
)
$ErrorActionPreference = 'SilentlyContinue'

function Line($t) { Write-Host $t }
function Head($t) { Write-Host ""; Write-Host $t -ForegroundColor Cyan }

Head "1. Service state"
$found = $false
foreach ($n in @('WorkbenchBlue', 'WorkbenchGreen')) {
    $s = Get-Service -Name $n -ErrorAction SilentlyContinue
    if (-not $s) { Line ("   {0,-16} not installed" -f $n); continue }
    $found = $true
    $wmi = Get-CimInstance Win32_Service -Filter "Name='$n'"
    Line ("   {0,-16} {1,-9} runs-as: {2}" -f $n, $s.Status, $wmi.StartName)
    Line ("   {0,-16} command: {1}" -f "", $wmi.PathName)
}
if (-not $found) {
    Line "   Neither colour is installed - install-lan-service.ps1 first."
    return
}

Head "2. What is listening"
foreach ($p in @(8501, 8502)) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        $proc = Get-Process -Id ($c.OwningProcess | Select-Object -First 1)
        Line ("   port {0}: {1} (PID {2}, session {3})" -f $p, $proc.ProcessName,
              $proc.Id, $proc.SessionId)
    } else {
        Line ("   port {0}: nothing listening" -f $p)
    }
}

Head "3. Last lines of each service log"
$logs = Get-ChildItem (Join-Path $AppDir "logs\service-*.log") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 4
if (-not $logs) { Line "   no service logs yet" }
$blocked = $false
foreach ($f in $logs) {
    Line ("   --- {0} ({1:yyyy-MM-dd HH:mm}) ---" -f $f.Name, $f.LastWriteTime)
    $tail = Get-Content $f.FullName -Tail 12
    foreach ($l in $tail) {
        Line ("   {0}" -f $l)
        if ($l -match '4551|Application Control|blocked this file') { $blocked = $true }
    }
}

Head "4. Verdict"
if ($blocked) {
    Write-Host "   APPLICATION CONTROL IS BLOCKING THE SERVICE'S PYTHON." -ForegroundColor Yellow
    Line ""
    Line "   The service account's interpreter lives outside any path the"
    Line "   machine policy trusts. Your own account's interpreter is trusted"
    Line "   - which is why start-workbench.bat works and the service does not."
    Line ""
    Line "   Remedy A (no policy change, works today):"
    Line "     run the services under YOUR account instead of SYSTEM -"
    Line "       deploy\windows\install-lan-service.ps1 -RunAsUser `"$env:USERDOMAIN\$env:USERNAME`""
    Line "     It will prompt for your Windows password, which is stored by"
    Line "     the Service Control Manager, not by us."
    Line ""
    Line "   Remedy B (permanent, needs an admin/MDM change):"
    Line "     have whoever manages the Application Control / WDAC policy"
    Line "     allow this path:"
    Line "       C:\ProgramData\EightRockWorkbench\venv-*\Scripts\python.exe"
    Line ""
    Line "   Until one of those lands, start-workbench.bat is the supported"
    Line "   way to run the app. It is not blue/green, so a restart drops"
    Line "   live sessions for a few seconds."
} elseif ((Get-Service WorkbenchBlue -ErrorAction SilentlyContinue).Status -eq 'Running' -or
          (Get-Service WorkbenchGreen -ErrorAction SilentlyContinue).Status -eq 'Running') {
    Write-Host "   At least one colour is running - blue/green is available." -ForegroundColor Green
} else {
    Line "   No Application Control signature in the logs. Read section 3"
    Line "   above: the last lines name the real failure."
}
Write-Host ""
