@echo off
REM ===================================================================
REM  Why is the browser still showing an old version?
REM
REM  There are only two possible causes, and this tells you which:
REM    (A) the CODE ON DISK is old  -> the update never synced
REM    (B) the code on disk is new, but the RUNNING app still has the
REM        old code loaded in memory -> it was never restarted
REM
REM  Read-only. Changes nothing, needs no administrator rights.
REM ===================================================================
cd /d "%~dp0"
title What is actually running?

echo ============================================================
echo  A. VERSION ON DISK  (what an app started right now would use)
echo ============================================================
findstr /C:"WORKBENCH_VERSION =" config.py
echo.

echo ============================================================
echo  B. IS THE DISK UP TO DATE WITH GITHUB?
echo ============================================================
git fetch origin >nul 2>&1
for /f %%C in ('git rev-list --count HEAD..origin/main 2^>nul') do set "BEHIND=%%C"
if not defined BEHIND set "BEHIND=?"
if "%BEHIND%"=="0" (
  echo Disk is CURRENT with origin/main.
) else (
  echo Disk is BEHIND origin/main by %BEHIND% commit^(s^).
  echo   ^-^> the update did not sync. Run update-workbench.bat and WATCH
  echo      for the administrator prompt - it opens a NEW window, and if
  echo      that prompt is dismissed the update exits without doing
  echo      anything.
)
echo.

echo ============================================================
echo  C. WHAT IS SERVING THE APP RIGHT NOW
echo ============================================================
set "FOUND="
for %%P in (8501 8502) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do (
    set "FOUND=1"
    echo   port %%P  ^-^>  PID %%I
    powershell -NoProfile -Command ^
      "$p=Get-Process -Id %%I -ErrorAction SilentlyContinue; if($p){'      started: '+$p.StartTime}" 2>nul
  )
)
if not defined FOUND (
  echo   Nothing is listening on 8501 or 8502 - the app is not running.
  echo   Double-click start-workbench.bat.
  goto :done
)
echo.
echo   If the start time above is EARLIER than your last update, this
echo   process is serving the OLD code. That is cause (B): the version
echo   pill will not change no matter how many times you refresh.
echo.
echo   FIX: close the app window, then double-click start-workbench.bat
echo        (it stops whatever holds these ports and relaunches on the
echo        code currently on disk).

:done
echo.
echo ============================================================
echo  D. BLUE/GREEN SERVICES
echo ============================================================
for %%S in (WorkbenchBlue WorkbenchGreen) do (
  sc query %%S >nul 2>&1 && (
    for /f "tokens=3" %%T in ('sc query %%S ^| findstr /c:"STATE"') do echo   %%S: %%T
  ) || echo   %%S: not installed
)
echo.
echo   A service that is installed but NOT RUNNING is not serving
echo   anything - and until V5.46.1.0.0 the updater mistook "installed"
echo   for "serving", skipped restarting the real app, and reported
echo   success. That is the bug behind the stale version pill.
echo.
pause
