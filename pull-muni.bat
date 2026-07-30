@echo off
REM ===================================================================
REM  Pull the Hampton Roads municipal feeds (built-in + discovered)
REM  into data\workbench.db. Safe to re-run; replaces per-feed rows.
REM  Double-click. Takes a while for big cities.
REM  The full output is saved to reports\pull-latest.txt and pushed
REM  to GitHub so Claude reads the results directly - no screenshots.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
if not exist reports mkdir reports
echo NOTE: the app may stay open - the database now runs in WAL mode.
echo.
"%UV%" run python -u etl_munidata.py --hr 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -Variable out; $out | Set-Content -Path 'reports\pull-latest.txt' -Encoding UTF8"
call "%~dp0_push-report.bat" reports\pull-latest.txt "pull-muni"
echo.
echo Done. Now double-click run-phase0.bat to rebuild the backbone.
pause
