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
git remote set-url origin https://github.com/EightRockDev/WORKBENCH_v5.git >nul 2>&1
git fetch origin
REM Abort any stale rebase and re-attach a detached HEAD before syncing -
REM a wedged .git state must never block the update.
git rebase --abort >nul 2>&1
git cherry-pick --abort >nul 2>&1
git merge --abort >nul 2>&1
if exist ".git\rebase-merge" rmdir /s /q ".git\rebase-merge" >nul 2>&1
if exist ".git\rebase-apply" rmdir /s /q ".git\rebase-apply" >nul 2>&1
git checkout -B main origin/main >nul 2>&1
git reset --hard origin/main
if errorlevel 1 (
  echo.
  echo !! Could not sync. Read the git message above - it usually says exactly what to do.
  pause
  exit /b 1
)

echo.
echo === Installing any new dependencies ===
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" sync

echo.
echo === Applying database migration (idempotent) ===
powershell -ExecutionPolicy Bypass -File "%~dp0deploy\windows\migrate-db.ps1" -AppDir "%~dp0."

echo.
echo === Current version ===
findstr /C:"WORKBENCH_VERSION =" config.py

echo.
echo === Ensuring the nightly Autopilot is installed ===
schtasks /Query /TN "EightRockWorkbenchAutopilot" >nul 2>&1
if errorlevel 1 (
  schtasks /Create /F /TN "EightRockWorkbenchAutopilot" /TR "\"%~dp0autopilot.bat\"" /SC DAILY /ST 03:00
  echo Autopilot installed - the full data cycle now runs itself nightly
  echo at 3:00 AM and publishes every report to GitHub for Claude.
  echo Starting the first cycle in the background now...
  start "EightRockAutopilot" /min cmd /c "%~dp0autopilot.bat"
) else (
  echo Autopilot already installed - runs nightly at 3:00 AM.
)

echo.
echo Update complete. Double-click start-workbench.bat to launch.
pause
