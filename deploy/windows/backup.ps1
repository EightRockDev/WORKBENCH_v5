<#
===========================================================================
 Eight Rock Workbench v5.0 - nightly DB backup (Windows, Section 9.1/9.2)
 --------------------------------------------------------------------------
 A pg_dump writes ONE closed file, then it is safe to copy to a OneDrive
 backup target (Section 9.2). The LIVE database must never live in a synced
 folder - only the closed dump is synced.

 Schedule via Task Scheduler (run as SYSTEM or an admin), daily ~02:15:
   schtasks /create /tn "Workbench Backup" /tr ^
     "powershell -NoProfile -ExecutionPolicy Bypass -File C:\WORKBENCH_V5\deploy\windows\backup.ps1" ^
     /sc daily /st 02:15 /ru SYSTEM
===========================================================================
#>
param(
    [string]$DbName            = "workbench",
    [string]$LocalBackupDir    = "",                         # default resolved below
    [string]$OneDriveBackupDir = "",                         # optional synced target
    [int]   $RetainDays        = 30
)
$ErrorActionPreference = "Stop"
$pgDump = "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"

# Default local dir: prefer a second disk when one exists, else C: - the
# documented D:\ default silently assumed hardware this host may not have.
if ($LocalBackupDir -eq "") {
    if (Test-Path "D:\") { $LocalBackupDir = "D:\Backup\8rw" }
    else                 { $LocalBackupDir = "C:\Backup\8rw" }
}
New-Item -ItemType Directory -Force -Path $LocalBackupDir | Out-Null

# Non-interactive credentials: read DATABASE_URL from the app's .env so the
# dump works under SYSTEM at 02:15. 'pg_dump -U postgres' would PROMPT for a
# password and hang the scheduled run - the reason this script must never
# rely on it when the app's own credentials are available.
$dbUser = "postgres"; $dbHost = "localhost"; $dbPort = "5432"
$envFile = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) ".env"
if (Test-Path $envFile) {
    $line = (Get-Content $envFile | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1)
    if ($line -and $line -match 'postgres(?:ql)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/([A-Za-z0-9_-]+)') {
        $dbUser = $Matches[1]
        $env:PGPASSWORD = $Matches[2]
        $dbHost = $Matches[3]
        if ($Matches[4]) { $dbPort = $Matches[4] }
        $DbName = $Matches[5]
        Write-Host "[$(Get-Date -Format o)] using credentials from .env (user $dbUser, db $DbName)"
    }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out   = Join-Path $LocalBackupDir "workbench-$stamp.dump"

Write-Host "[$(Get-Date -Format o)] dumping $DbName -> $out"
# -f writes the binary custom-format dump directly (no PowerShell byte piping).
& $pgDump -U $dbUser -h $dbHost -p $dbPort -Fc -f $out $DbName
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE" }

# Only after the dump is fully written/closed do we copy to OneDrive.
if ($OneDriveBackupDir -ne "") {
    New-Item -ItemType Directory -Force -Path $OneDriveBackupDir | Out-Null
    Copy-Item $out $OneDriveBackupDir
    Write-Host "[$(Get-Date -Format o)] synced closed dump to $OneDriveBackupDir"
}

# Retention
Get-ChildItem $LocalBackupDir -Filter "workbench-*.dump" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetainDays) } |
    Remove-Item -Force
Write-Host "[$(Get-Date -Format o)] backup complete; pruned dumps older than $RetainDays d"
