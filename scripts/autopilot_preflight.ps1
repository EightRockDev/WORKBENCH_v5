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

# ORPHANS ONLY (2026-08-11, second lesson of the day: the 11:00 scheduled
# fire's preflight shot down the owner's healthy 10:45 manual cycle).
# A living cycle is a chain of live processes - cmd -> uv -> python. An
# orphan's parent is DEAD (Task Scheduler killed the launcher, the children
# survived). So: kill a matching process only when its parent PID is no
# longer alive, and take its whole subtree with it (taskkill /T). A healthy
# concurrent cycle is left alone - the launcher then fails the log-append
# check and reports "another autopilot is running" instead of killing it.
$all = Get-CimInstance Win32_Process
$live = @{}; foreach ($p in $all) { $live[[uint32]$p.ProcessId] = $true }

$killed = 0
$all |
  Where-Object { $_.CommandLine -and $keep -notcontains $_.ProcessId -and
                 $_.CommandLine -match $pattern -and
                 -not $live[[uint32]$_.ParentProcessId] } |
  ForEach-Object {
    $c = ($_.CommandLine -replace '\s+', ' ')
    if ($c.Length -gt 160) { $c = $c.Substring(0, 160) }
    Write-Output ("killed orphan PID {0} (parent dead): {1}" -f
                  $_.ProcessId, $c)
    taskkill /T /F /PID $_.ProcessId 2>&1 | Out-Null
    $killed++
  }

# A git process killed mid-write leaves .git\index.lock, which wedges the
# stage-1 code update. Only safe to clear when NO healthy cycle survives -
# a live cycle may be mid-git and its lock is real, not stale.
$healthy = @($all | Where-Object {
    $_.CommandLine -and $keep -notcontains $_.ProcessId -and
    $_.CommandLine -match $pattern -and $live[[uint32]$_.ParentProcessId] })
if ($healthy.Count -gt 0) {
  Write-Output ("left alone: {0} process(es) of a live cycle" -f
                $healthy.Count)
} else {
  $lock = Join-Path (Split-Path $PSScriptRoot -Parent) '.git\index.lock'
  if (Test-Path $lock) {
    Remove-Item -Force $lock
    Write-Output 'removed stale .git\index.lock'
  }
}

if ($killed -eq 0) { Write-Output 'clean - no orphaned cycle processes' }
