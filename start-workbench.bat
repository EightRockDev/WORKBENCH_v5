@echo off
setlocal EnableDelayedExpansion
REM ===================================================================
REM  Eight Rock Workbench - launcher. Double-click to start the app.
REM  Always cd's to its own folder first, so the workbench can live
REM  anywhere on disk - the location is never hardcoded.
REM
REM  This ALSO gets the latest code before starting (2026-08-15). The
REM  owner was three-plus releases behind while every fix shipped on
REM  time, because update-workbench.bat is a separate file he has to
REM  remember to run, and the app he actually double-clicks was happy
REM  to launch stale code forever without saying so.
REM
REM  Launching IS updating now. The sync is best-effort: no network, a
REM  wedged git state, anything at all - it says so in one line and
REM  starts the app regardless. A launcher that refuses to launch
REM  because of an update is a worse failure than being a day behind.
REM ===================================================================
cd /d "%~dp0"
title Eight Rock Workbench
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
set ER_DEV_LOGIN=1

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

echo Checking for a newer version...
set "SYNCED="
set "GIT_ASK_YESNO=false"
set "REPO_FWD=%CD:\=/%"
git config --global --add safe.directory "%REPO_FWD%" >nul 2>&1
git remote set-url origin https://github.com/EightRockDev/WORKBENCH_v5.git >nul 2>&1
git fetch origin >nul 2>&1
if not errorlevel 1 (
  REM Clear any wedged in-progress operation first - a half-finished
  REM rebase from weeks ago must never be why today's fix does not land.
  git rebase --abort      >nul 2>&1
  git cherry-pick --abort >nul 2>&1
  git merge --abort       >nul 2>&1
  git symbolic-ref HEAD refs/heads/main >nul 2>&1
  git reset --soft origin/main >nul 2>&1
  REM reports\ is Autopilot OUTPUT and may be open right now; Windows
  REM blocks git from rewriting an open file and the whole sync aborts.
  git checkout -f origin/main -- . ":(exclude)reports" >nul 2>&1
  if not errorlevel 1 set "SYNCED=1"
)
if defined SYNCED (
  "%UV%" sync >nul 2>&1
) else (
  echo   ^(Could not check - starting with the version already on this PC.^)
)

echo.
echo ===========================================================
for /f "tokens=2 delims==" %%V in ('findstr /C:"WORKBENCH_VERSION =" config.py') do (
  set "VER=%%V"
  set "VER=!VER: =!"
  set "VER=!VER:"=!"
  echo   You are running WORKBENCH !VER!
)
echo ===========================================================
echo.
echo Starting Eight Rock Workbench...
echo Open your browser to http://localhost:8501 once it is up.
echo (Close this window to stop the app.)
echo.
"%UV%" run python -m streamlit run app.py
pause
