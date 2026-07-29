@echo off
REM ===================================================================
REM  Phase 0 step P0-1: build the Eight Rock property spine
REM  (properties_8r) from the municipal records. Double-click.
REM  The full report is saved to reports\phase0-latest.txt and pushed
REM  to GitHub so Claude reads the results directly - no screenshots.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
if not exist reports mkdir reports
"%UV%" run python -u scripts/run_phase0.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -Variable out; $out | Set-Content -Path 'reports\phase0-latest.txt' -Encoding UTF8"
call "%~dp0_push-report.bat" reports\phase0-latest.txt "run-phase0"
echo.
pause
