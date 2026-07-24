<#
===========================================================================
 Eight Rock Workbench v5.0 - apply DB schema migrations (Windows)
 --------------------------------------------------------------------------
 Re-applies db/pilot_schema.sql to bring the database in sync after a
 `git pull` that changed the schema. Uses the DATABASE_URL already in .env
 (the workbench role owns the tables), so NO admin rights and NO postgres
 superuser password are needed. Idempotent: safe to run anytime.

 Run from a normal PowerShell:
   cd C:\WORKBENCH_V5
   .\deploy\windows\migrate-db.ps1
===========================================================================
#>
[CmdletBinding()]
param(
    [string]$AppDir    = "C:\WORKBENCH_V5",
    [string]$PgVersion = "16"
)
$ErrorActionPreference = "Stop"

$psql = "C:\Program Files\PostgreSQL\$PgVersion\bin\psql.exe"
if (-not (Test-Path $psql)) { throw "psql not found at $psql" }

$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) { throw "$envFile not found - run setup-db.ps1 first." }

$line = Get-Content $envFile | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
if (-not $line) { throw "DATABASE_URL not found in $envFile" }
$url = ($line -replace '^DATABASE_URL=', '').Trim()

Write-Host ">> Applying db\pilot_schema.sql ..." -ForegroundColor Yellow
& $psql $url -v ON_ERROR_STOP=1 -q -f (Join-Path $AppDir "db\pilot_schema.sql")

$presets = (& $psql $url -tAc "select count(*) from role_presets").Trim()
Write-Host ("`n>> Migration complete. role_presets rows = " + $presets + " (expected 18).") -ForegroundColor Green
Write-Host "   Restart the app to pick up the change."
