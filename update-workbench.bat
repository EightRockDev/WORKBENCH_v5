@echo off
REM ===================================================================
REM  Eight Rock Workbench - updater. Double-click to get the latest
REM  code, dependencies, and database schema. Safe to run anytime.
REM  Your .env (keys/DB password) is never touched - it is gitignored.
REM ===================================================================
cd /d "%~dp0"
title Update workbench - press a key to close when finished

REM Self-elevate. In blue/green service mode the update MUST restart the
REM WorkbenchBlue/Green services at the end (and may `sc stop` them if a
REM dependency file is locked) - both need administrator rights. Without this,
REM deploy-swap.ps1 died with "Cannot open WorkbenchBlue service" and the app
REM kept serving the OLD code (owner report 2026-08-04). Elevate up front so
REM the whole update runs with the rights it needs.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

REM Never stop to ask "Should I try again? (y/n)" - fail fast instead
REM so the retry logic below (not a human) handles locked files.
set "GIT_ASK_YESNO=false"

REM Trust this folder for git no matter which Windows account runs the
REM update (a folder created by one user and updated by another trips
REM git's "dubious ownership" safety check and blocks the sync).
set "REPO_FWD=%CD:\=/%"
git config --global --add safe.directory "%REPO_FWD%" >nul 2>&1

REM Blue/green service mode? When the NSSM services exist, do NOT kill the
REM ports: NSSM restarts a killed service INSTANTLY, so it would come back
REM mid-sync still running the OLD code - and nothing would restart it
REM afterwards. The stale-version pill, back through a different door.
REM In service mode the services keep serving while the code syncs, and
REM deploy-swap.ps1 restarts them one at a time at the END: zero downtime,
REM both colours on the new code.
set "SVCMODE="
sc query WorkbenchBlue  >nul 2>&1 && set "SVCMODE=1"
sc query WorkbenchGreen >nul 2>&1 && set "SVCMODE=1"
if defined SVCMODE (
  echo === Blue/green services detected - they keep serving during the sync ===
  goto :synccode
)

echo === Stopping any running app so files are not locked ===
REM The app runs as python.exe ("uv run python -m streamlit run app.py"), so
REM `taskkill /IM streamlit.exe` never matched it and the error was swallowed
REM by >nul. Every update left the OLD server running with the previous code
REM still loaded in memory - which is why the topbar pill kept reporting a
REM stale version after an update + restart + browser refresh. Kill whatever
REM is actually listening on the workbench ports; that is launch-agnostic.
for %%P in (8501 8502) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do (
    taskkill /F /PID %%I >nul 2>&1
  )
)
taskkill /F /IM streamlit.exe >nul 2>&1

echo.
set "STILLUP="
for %%P in (8501 8502) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do set "STILLUP=%%I"
)
if defined STILLUP (
  echo [warn] something is STILL listening on 8501/8502 ^(pid %STILLUP%^).
  echo        The app may keep serving the old code - close that window first.
) else (
  echo Old app stopped.
)

:synccode
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
REM Sync CODE without touching reports\ - the Autopilot data cycle may be
REM running right now with a report file (e.g. reports\pull-latest.txt)
REM held open, and Windows blocks git from rewriting an open file. A full
REM "reset --hard" trips over that lock and aborts the whole sync (seen
REM 2026-07-31). Reports are cycle OUTPUT anyway - the next cycle rewrites
REM and fully re-syncs them itself; the updater only needs the code.
git symbolic-ref HEAD refs/heads/main >nul 2>&1
git reset --soft origin/main >nul 2>&1
git checkout -f origin/main -- . ":(exclude)reports"
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
if errorlevel 1 if defined SVCMODE (
  REM Windows blocks replacing a .pyd/.exe that a running service has
  REM loaded. Rare (only when a dependency actually changed), so pay the
  REM brief stop only when it happens - the swap at the end starts both
  REM colours again on the new code.
  echo [warn] dependency sync hit files locked by the running services.
  echo        Stopping both colours briefly and retrying...
  sc stop WorkbenchBlue  >nul 2>&1
  sc stop WorkbenchGreen >nul 2>&1
  timeout /t 5 /nobreak >nul
  "%UV%" sync
)

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
if defined SVCMODE (
  echo === Zero-downtime swap: restarting blue/green one at a time ===
  powershell -ExecutionPolicy Bypass -File "%~dp0deploy\windows\deploy-swap.ps1"
  if errorlevel 1 (
    echo [warn] the swap stopped early - the other colour is still serving
    echo        the OLD code. Read the swap output above before rerunning.
  ) else (
    echo Update complete - both colours are serving the new code. No relaunch needed.
  )
) else (
  echo Update complete. Double-click start-workbench.bat to launch.
)
pause
