# =====================================================================
#  Autopilot preflight: clear the wreckage of a killed prior cycle so a
#  new launch can actually start.
#
#  Why this exists (owner 2026-08-11, "autopilot.bat flashes too fast"):
#  Task Scheduler's 3h ExecutionTimeLimit stops the cmd.exe it launched
#  but ORPHANS the python/uv children. An orphan still appending to
#  reports\autopilot.log makes the next launcher's first ">>" redirect
#  fail, so every new window died in under a second with no visible
#  reason. Kill the leftovers (never our own ancestor chain, never the
#  workbench app/API - only autopilot cycle processes), then clear any
#  stale git lock a killed git left behind.
# =====================================================================
$ErrorActionPreference = 'SilentlyContinue'

# Our own process chain (this powershell, the launcher cmd, its parents)
# is off-limits.
$keep = @(); $id = $PID
for ($i = 0; $i -lt 12 -and $id; $i++) {
  $keep += $id
  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
  if (-not $p) { break }
  $id = $p.ParentProcessId
}

# Every script the cycle can be running (STEPS in scripts/autopilot_run.py)
# plus the stage runners and any other launcher instance.
$pattern = ('autopilot\.bat|scripts[/\\]autopilot|etl_munidata|' +
            'discover_feeds|run_national_discovery|discover_sales_feeds|' +
            'run_public_data|run_listings|pull_sales|pull_arcgis_sales|' +
            'run_sale_index|run_phase0|run_validate|run_pending_users|' +
            'run_inbox_sync|run_backbone_stats|run_richmond_report|' +
            'run_alerts|preflight_cutover|run_backup')

$killed = 0
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and $keep -notcontains $_.ProcessId -and
                 $_.CommandLine -match $pattern } |
  ForEach-Object {
    $c = ($_.CommandLine -replace '\s+', ' ')
    if ($c.Length -gt 160) { $c = $c.Substring(0, 160) }
    Write-Output ("killed leftover PID {0}: {1}" -f $_.ProcessId, $c)
    Stop-Process -Id $_.ProcessId -Force
    $killed++
  }

# A git process killed mid-write leaves .git\index.lock, which wedges the
# stage-1 code update. Only safe to clear AFTER the kills above.
$lock = Join-Path (Split-Path $PSScriptRoot -Parent) '.git\index.lock'
if (Test-Path $lock) {
  Remove-Item -Force $lock
  Write-Output 'removed stale .git\index.lock'
}

if ($killed -eq 0) { Write-Output 'clean - no leftover cycle processes' }
