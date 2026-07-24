@echo off
REM ===================================================================
REM  Set the BatchData API key + enable live skip trace. Double-click.
REM  Prompts for the key and writes it into .env correctly (idempotent).
REM ===================================================================
cd /d C:\WORKBENCH_V5
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$k = Read-Host 'Paste your BatchData API key, then Enter'; if (-not $k) { Write-Host 'No key entered.' -ForegroundColor Red; exit }; $keep = @(); if (Test-Path .env) { $keep = (Get-Content .env) -notmatch '^(ER_SKIPTRACE_PROVIDERS|BATCHDATA_API_KEY)=' }; Set-Content .env ($keep + 'ER_SKIPTRACE_PROVIDERS=live' + ('BATCHDATA_API_KEY=' + $k)); Write-Host 'Saved to .env. Now double-click start-workbench.bat.' -ForegroundColor Green"
echo.
pause
