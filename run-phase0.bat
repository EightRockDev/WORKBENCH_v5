@echo off
REM ===================================================================
REM  Phase 0 step P0-1: build the Eight Rock property spine
REM  (properties_8r) from the municipal records. Double-click.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python scripts/run_phase0.py
echo.
pause
