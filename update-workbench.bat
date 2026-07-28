@echo off
REM ===================================================================
REM  Eight Rock Workbench - updater. Double-click to get the latest
REM  code, dependencies, and database schema. Safe to run anytime.
REM  Your .env (keys/DB password) is never touched - it is gitignored.
REM ===================================================================
cd /d "%~dp0"

REM Trust this folder for git no matter which Windows account runs the
REM update (a folder created by one user and updated by another trips
REM git's "dubious ownership" safety check and blocks the sync).
set "REPO_FWD=%CD:\=/%"
git config --global --add safe.directory "%REPO_FWD%" >nul 2>&1

echo === Stopping any running app so files are not locked ===
taskkill /F /IM streamlit.exe >nul 2>&1

echo.
echo === Syncing code to the latest pushed version ===
git fetch origin
git reset --hard origin/main
if errorlevel 1 (
  echo.
  echo !! Could not sync. Read the git message above - it usually says exactly what to do.
  pause
  exit /b 1
)

echo.
echo === Installing any new dependencies ===
uv sync

echo.
echo === Applying database migration (idempotent) ===
powershell -ExecutionPolicy Bypass -File "%~dp0deploy\windows\migrate-db.ps1" -AppDir "%~dp0."

echo.
echo === Current version ===
findstr /C:"WORKBENCH_VERSION =" config.py

echo.
echo Update complete. Double-click start-workbench.bat to launch.
pause
