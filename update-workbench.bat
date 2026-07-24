@echo off
REM ===================================================================
REM  Eight Rock Workbench - updater. Double-click to pull the latest
REM  code and apply any database migration. Then run start-workbench.
REM ===================================================================
cd /d C:\WORKBENCH_V5
echo === Syncing code to the latest pushed version ===
git fetch origin
git reset --hard origin/main
echo.
echo === Installing any new dependencies ===
uv sync
echo.
echo === Applying database migration (safe to run anytime) ===
powershell -ExecutionPolicy Bypass -File "C:\WORKBENCH_V5\deploy\windows\migrate-db.ps1"
echo.
echo === Current version ===
findstr /C:"WORKBENCH_VERSION =" config.py
echo.
echo Update complete. Double-click start-workbench.bat to launch.
pause
