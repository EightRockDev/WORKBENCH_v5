<#
===========================================================================
 Eight Rock Workbench - restore the pilot database from a backup.
 --------------------------------------------------------------------------
 Written for the 2026-08-18 data loss: `uv run pytest` on this server ran
 the pilot suites against the LIVE database, and their "TRUNCATE users,
 organizations, audit_log ... CASCADE" emptied production - 5 user
 accounts, both organizations, 3 deals, 37 CRM contacts, 41 paid
 skip-trace records, 81 inbox messages, 53 activity rows, 17 audit
 entries. The owner's own account was re-created by his next login, which
 is why it looked like "the users disappeared" rather than a wipe.

 The property backbone (SQLite: properties, properties_8r, sale_records)
 is a different database entirely and was never touched.

 WHAT THIS DOES, in order, stopping at the first problem:
   1. Dumps the CURRENT database to a pre-restore file. Always. This is
      the way back if anything below is wrong.
   2. Builds a scratch database (wb_recover) from the chosen backup and
      counts every table. Nothing live is touched.
   3. Only if those counts look right: stops the app and the autopilot,
      renames the live database aside (workbench_broken_<stamp>), and
      promotes the verified scratch copy in its place. A rename is instant
      and reversible - the wiped database is KEPT, never dropped.
   4. Restarts what it stopped and prints the final counts.

 Run WITHOUT -Swap first to do steps 1-2 only and read the report.
 Add -Swap to carry out step 3.

   cd C:\WORKBENCH_V5
   powershell -ExecutionPolicy Bypass -File deploy\windows\restore-pilot-db.ps1
   powershell -ExecutionPolicy Bypass -File deploy\windows\restore-pilot-db.ps1 -Swap

 NOTE on error handling: this file never uses "2>$null" on a native
 command. Under Windows PowerShell 5.1 with $ErrorActionPreference =
 "Stop", that form manufactures a terminating NativeCommandError - it
 killed both installers on their own cleanup step (2026-08-02). Native
 calls here go through Invoke-Native / Get-Count, which drop the
 preference to Continue and read $LASTEXITCODE instead.
===========================================================================
#>
[CmdletBinding()]
param(
    [string]$AppDir     = "C:\WORKBENCH_V5",
    [string]$PgVersion  = "16",
    [string]$BackupFile = "",
    [string]$ScratchDb  = "wb_recover",
    [switch]$Swap
)
$ErrorActionPreference = "Stop"

$psql      = "C:\Program Files\PostgreSQL\$PgVersion\bin\psql.exe"
$pgDump    = "C:\Program Files\PostgreSQL\$PgVersion\bin\pg_dump.exe"
$pgRestore = "C:\Program Files\PostgreSQL\$PgVersion\bin\pg_restore.exe"
foreach ($exe in @($psql, $pgDump, $pgRestore)) {
    if (-not (Test-Path $exe)) { throw "not found: $exe" }
}

function Say([string]$m, [string]$c = "Gray") { Write-Host $m -ForegroundColor $c }

function Invoke-Native([scriptblock]$block) {
    # Run a native command tolerantly and report success by exit code.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $block 2>&1
        return @{ ok = ($LASTEXITCODE -eq 0); out = ($out | Out-String).Trim() }
    } catch {
        return @{ ok = $false; out = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# --- connection ------------------------------------------------------------
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) { throw "$envFile not found." }
$line = Get-Content $envFile | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
if (-not $line) { throw "DATABASE_URL not found in $envFile" }
$liveUrl = ($line -replace '^DATABASE_URL=', '').Trim()

$noQuery    = ($liveUrl -split '\?')[0]
$query      = if ($liveUrl -match '\?') { '?' + ($liveUrl -split '\?', 2)[1] } else { '' }
$baseUrl    = $noQuery -replace '/[^/]+$', ''
$liveDb     = ($noQuery -split '/')[-1]
$adminUrl   = "$baseUrl/postgres$query"     # a database we never rename
$scratchUrl = "$baseUrl/$ScratchDb$query"

# FORCE ROW LEVEL SECURITY applies to the table owner too, so the app's own
# role can neither pg_dump the RLS tables nor COUNT their rows honestly - it
# sees the empty no-context view and reports 0. That is why the nightly
# backup runs as the dedicated read-only BYPASSRLS role (CLAUDE.md lesson
# 2026-08-09), and this script must use it for exactly the same reasons:
# a truthful dump, and truthful verification counts.
$bkLine = Get-Content $envFile | Where-Object { $_ -match '^ER_BACKUP_DATABASE_URL=' } | Select-Object -First 1
$backupUrl = if ($bkLine) { ($bkLine -replace '^ER_BACKUP_DATABASE_URL=', '').Trim() } else { "" }
if (-not $backupUrl) {
    Say ""
    Say "ER_BACKUP_DATABASE_URL is not set in .env." Red
    Say "pg_dump cannot read the row-level-security tables as the app role," Red
    Say "so both the safety snapshot and the row counts would be wrong." Red
    Say "Create the read-only backup role once (as the postgres superuser):" Yellow
    # Single-quoted: PowerShell escapes quotes with a BACKTICK, never a
    # backslash, and a stray \" ends the string and breaks the file.
    Say ('  psql -U postgres -d ' + $liveDb + ' -c "CREATE ROLE backup_reader LOGIN PASSWORD ''<long-random>'' BYPASSRLS; GRANT pg_read_all_data TO backup_reader;"') Gray
    Say "then add to .env:" Yellow
    Say ('  ER_BACKUP_DATABASE_URL=postgresql://backup_reader:<long-random>@localhost:5432/' + $liveDb) Gray
    throw "no BYPASSRLS backup role configured - see CLAUDE.md (2026-08-09)."
}
$bkNoQuery    = ($backupUrl -split '\?')[0]
$bkQuery      = if ($backupUrl -match '\?') { '?' + ($backupUrl -split '\?', 2)[1] } else { '' }
$bkBase       = $bkNoQuery -replace '/[^/]+$', ''
$readLiveUrl    = $backupUrl                       # truthful counts, live
$readScratchUrl = "$bkBase/$ScratchDb$bkQuery"     # truthful counts, scratch

function Get-Count([string]$url, [string]$table) {
    $r = Invoke-Native { & $psql -d $url -tAc "select count(*) from $table" }
    if (-not $r.ok) { return -1 }
    $t = ($r.out -split "`n" | Where-Object { $_ -match '^\s*\d+\s*$' } | Select-Object -First 1)
    if ($t) { return [int]$t.Trim() }
    return -1
}

function Invoke-Admin([string]$sql) {
    return Invoke-Native { & $psql -d $adminUrl -v ON_ERROR_STOP=1 -tAc $sql }
}

Say ""
Say "=== Eight Rock - pilot database restore ===" Cyan
Say "live database : $liveDb"
if ($Swap) { Say "mode          : VERIFY + SWAP" Yellow }
else       { Say "mode          : VERIFY ONLY (no live change)" Yellow }
Say ""

# --- pick the backup -------------------------------------------------------
$backupDir = if (Test-Path "D:\Backup\8rw") { "D:\Backup\8rw" } else { "C:\Backup\8rw" }
if (-not $BackupFile) {
    # Newest dump that still holds MORE THAN ONE user, i.e. from before the
    # wipe. Taking "newest" blindly would faithfully restore the damage.
    $candidates = Get-ChildItem "$backupDir\workbench-*.dump" | Sort-Object Name -Descending
    foreach ($c in $candidates) {
        $probe = Invoke-Native { & $pgRestore --data-only -t users -f - $c.FullName }
        $rows = @(($probe.out -split "`n") | Where-Object {
            $_ -match '@' -and $_ -notmatch '^(--|SET |SELECT |COPY |\\)' })
        if ($rows.Count -gt 1) { $BackupFile = $c.FullName; break }
    }
}
if (-not $BackupFile -or -not (Test-Path $BackupFile)) {
    throw "no usable backup found in $backupDir (need one with more than one user row)."
}
Say "backup        : $BackupFile" Green

# --- STEP 1: dump the current database, always -----------------------------
$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$preFile = Join-Path $backupDir "pre-restore-$stamp.dump"
Say ""
Say ">> STEP 1  backing up the CURRENT database first ..." Yellow
$pre = Invoke-Native { & $pgDump -Fc -f $preFile $backupUrl }
if (-not $pre.ok -or -not (Test-Path $preFile)) {
    Say $pre.out Red
    throw "could not back up the current database - stopping before any change."
}
Say "   saved: $preFile" Green

# --- STEP 2: rebuild into a scratch database and verify --------------------
Say ""
Say ">> STEP 2  rebuilding the backup into '$ScratchDb' (nothing live is touched) ..." Yellow

$drop = Invoke-Admin "DROP DATABASE IF EXISTS $ScratchDb"
if (-not $drop.ok) {
    Say "   ! could not create databases as this role:" Red
    Say "     $($drop.out)" Red
    Say "     Grant it once, as the postgres superuser:" Red
    Say "       ALTER ROLE workbench CREATEDB;" Red
    throw "insufficient privileges to build the scratch database."
}
$mk = Invoke-Admin "CREATE DATABASE $ScratchDb"
if (-not $mk.ok) { Say $mk.out Red; throw "could not create $ScratchDb." }

# pg_restore returns non-zero on benign notices; the row counts below are
# the real verification, not the exit code.
$null = Invoke-Native { & $pgRestore -d $scratchUrl --no-owner --no-privileges $BackupFile }

$tables = @('users','organizations','memberships','audit_log','deals',
            'crm_contacts','poc_records','inbox_messages','outreach_touches',
            'user_property_overrides','property_activity','term_sheets',
            'mailbox_connections','api_keys','campaigns','relationship_edges')

Say ""
Say ("{0,-26} {1,10} {2,10}" -f "table", "restored", "live now") Cyan
$restoredTotal = 0
$results = @{}
foreach ($t in $tables) {
    $r = Get-Count $readScratchUrl $t
    $l = Get-Count $readLiveUrl $t
    $results[$t] = $r
    if ($r -gt 0) { $restoredTotal += $r }
    $flag = ""
    if ($r -gt $l) { $flag = "  <- recovered" }
    Say ("{0,-26} {1,10} {2,10}{3}" -f $t, $r, $l, $flag)
}

Say ""
if ($results['users'] -lt 2) {
    throw "verification FAILED: the scratch copy has $($results['users']) user(s). Nothing was changed."
}
if ($restoredTotal -le 0) {
    throw "verification FAILED: the scratch copy is empty. Nothing was changed."
}
Say ">> VERIFIED: $($results['users']) users and $restoredTotal rows restored into '$ScratchDb'." Green

if (-not $Swap) {
    Say ""
    Say "Stopping here - no live change was made. Re-run with -Swap to promote it." Yellow
    Say "The scratch copy stays in place so the numbers above can be re-checked." Gray
    exit 0
}

# --- STEP 3: promote the verified copy -------------------------------------
Say ""
Say ">> STEP 3  promoting '$ScratchDb' to '$liveDb' ..." Yellow

Say "   pausing the autopilot ..."
$null = Invoke-Native { & schtasks /Change /TN "EightRockWorkbenchAutopilot" /DISABLE }

Say "   stopping the app so it releases the database ..."
foreach ($svc in @("WorkbenchBlue", "WorkbenchGreen")) {
    $null = Invoke-Native { & sc.exe stop $svc }
}
foreach ($port in @(8501, 8502)) {
    $listening = Invoke-Native { & netstat -ano }
    foreach ($row in ($listening.out -split "`n")) {
        if ($row -match ":$port\s.*LISTENING\s+(\d+)\s*$") {
            $processId = $matches[1]
            $null = Invoke-Native { & taskkill /F /PID $processId }
        }
    }
}
Start-Sleep -Seconds 3

# Terminate anything still attached, or RENAME fails with "is being accessed".
$null = Invoke-Admin "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$liveDb','$ScratchDb') AND pid <> pg_backend_pid()"

$brokenName = "${liveDb}_broken_$stamp"
$r1 = Invoke-Admin "ALTER DATABASE $liveDb RENAME TO $brokenName"
if (-not $r1.ok) {
    Say "   ! rename failed: $($r1.out)" Red
    Say "   Nothing was changed. The app is stopped - restart it with" Red
    Say "   start-workbench.bat, and re-enable the autopilot with:" Red
    Say "     schtasks /Change /TN EightRockWorkbenchAutopilot /ENABLE" Red
    throw "could not rename the live database."
}
$r2 = Invoke-Admin "ALTER DATABASE $ScratchDb RENAME TO $liveDb"
if (-not $r2.ok) {
    Say "   ! second rename failed - putting the original back ..." Red
    $null = Invoke-Admin "ALTER DATABASE $brokenName RENAME TO $liveDb"
    throw "could not promote the restored copy; the original database is back in place."
}
Say "   done. the wiped database is kept as '$brokenName'." Green

Say ""
Say "   restarting ..."
foreach ($svc in @("WorkbenchBlue", "WorkbenchGreen")) {
    $null = Invoke-Native { & sc.exe start $svc }
}
$null = Invoke-Native { & schtasks /Change /TN "EightRockWorkbenchAutopilot" /ENABLE }

Say ""
Say "=== FINAL STATE ===" Cyan
foreach ($t in @('users','organizations','memberships','deals','crm_contacts',
                 'poc_records','inbox_messages','property_activity')) {
    Say ("{0,-26} {1,10}" -f $t, (Get-Count $readLiveUrl $t))
}
Say ""
$who = Invoke-Native { & $psql -d $readLiveUrl -c "select email, display_name, platform_role, status from users order by created_at" }
Say $who.out
Say ""
Say "Restore complete." Green
Say "If the blue/green services are not in use, start the app with start-workbench.bat." Gray
Say "Kept for safety:" Gray
Say "  * pre-restore snapshot : $preFile" Gray
Say "  * wiped database       : $brokenName" Gray
