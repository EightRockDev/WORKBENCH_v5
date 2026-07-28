@echo off
REM ===================================================================
REM  AC-P0-1 verification sweep: case-insensitive scan for ALN
REM  references across the repo, databases and deal folders.
REM  Read-only - reports, never deletes. Double-click.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python scripts/phase0_sweep.py
echo.
pause
