@echo off
REM ===================================================================
REM  Pull the Hampton Roads municipal feeds (built-in + discovered)
REM  into data\workbench.db. Safe to re-run; replaces per-feed rows.
REM  Double-click. Takes a while for big cities.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
echo NOTE: close the workbench app first (uninstall-service.bat stops the
echo service) - the database cannot be written while the app holds it.
echo.
"%UV%" run python etl_munidata.py --hr
echo.
echo Done. Now double-click run-phase0.bat to rebuild the spine.
pause
