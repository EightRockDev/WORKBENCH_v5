<#
===========================================================================
 Eight Rock Workbench v5.0 — nightly DB backup (Windows, Section 9.1/9.2)
 --------------------------------------------------------------------------
 A pg_dump writes ONE closed file, then it is safe to copy to a OneDrive
 backup target (Section 9.2). The LIVE database must never live in a synced
 folder — only the closed dump is synced.

 Schedule via Task Scheduler (run as SYSTEM or an admin), daily ~02:15:
   schtasks /create /tn "Workbench Backup" /tr ^
     "powershell -NoProfile -ExecutionPolicy Bypass -File C:\WORKBENCH_V5\deploy\windows\backup.ps1" ^
     /sc daily /st 02:15 /ru SYSTEM
===========================================================================
#>
param(
    [string]$DbName            = "workbench",
    [string]$LocalBackupDir    = "D:\Backup\8rw",           # a SECOND local disk
    [string]$OneDriveBackupDir = "",                         # optional synced target
    [int]   $RetainDays        = 30
)
$ErrorActionPreference = "Stop"
$pgDump = "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
New-Item -ItemType Directory -Force -Path $LocalBackupDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out   = Join-Path $LocalBackupDir "workbench-$stamp.dump"

Write-Host "[$(Get-Date -Format o)] dumping $DbName -> $out"
# -f writes the binary custom-format dump directly (no PowerShell byte piping).
& $pgDump -U postgres -Fc -f $out $DbName

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
