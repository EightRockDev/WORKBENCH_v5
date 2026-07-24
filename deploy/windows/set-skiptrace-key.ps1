<#
  Set the BatchData API key + enable live skip trace, written into .env
  correctly and idempotently. Prints a masked confirmation. Run via the
  double-click wrapper set-skiptrace-key.bat.
#>
param([string]$AppDir = "C:\WORKBENCH_V5")
$ErrorActionPreference = "Stop"
Set-Location $AppDir
$envPath = Join-Path $AppDir ".env"

$key = Read-Host "Paste your BatchData API key, then press Enter"
if (-not $key) { Write-Host "No key entered - nothing changed." -ForegroundColor Red; exit 1 }

$keep = @()
if (Test-Path $envPath) {
    $keep = Get-Content $envPath | Where-Object { $_ -notmatch '^(ER_SKIPTRACE_PROVIDERS|BATCHDATA_API_KEY)=' }
}
$out = @($keep) + "ER_SKIPTRACE_PROVIDERS=live" + ("BATCHDATA_API_KEY=" + $key)
Set-Content -Path $envPath -Value $out -Encoding ASCII

Write-Host ""
Write-Host "Saved to $envPath :" -ForegroundColor Green
Get-Content $envPath |
    Select-String "^(ER_SKIPTRACE_PROVIDERS|BATCHDATA_API_KEY)=" |
    ForEach-Object { $_.Line -replace '(BATCHDATA_API_KEY=).{4}.*', '$1****(hidden)' } |
    ForEach-Object { Write-Host "   $_" }
Write-Host ""
Write-Host "NEXT: close the app window (the one running start-workbench), then" -ForegroundColor Yellow
Write-Host "      double-click start-workbench.bat again. The status line must" -ForegroundColor Yellow
Write-Host "      read: skiptrace: live (batchdata)." -ForegroundColor Yellow
