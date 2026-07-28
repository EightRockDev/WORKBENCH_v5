@echo off
REM ===================================================================
REM  Pull the Hampton Roads municipal feeds (built-in + discovered)
REM  into data\workbench.db. Safe to re-run; replaces per-feed rows.
REM  Double-click. Takes a while for big cities.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python etl_munidata.py
echo.
echo Done. Now double-click run-phase0.bat to rebuild the spine.
pause
