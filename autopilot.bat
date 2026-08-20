@echo off
REM ===================================================================
REM  Eight Rock Workbench AUTOPILOT - the whole data cycle, hands-free:
REM  update code -> discover feeds -> pull Hampton Roads -> rebuild the
REM  backbone -> publish every report to GitHub. Installed as a nightly
REM  scheduled task by install-autopilot.bat; safe to double-click too.
REM  Full log: reports\autopilot.log
REM
REM  Self-healing (2026-08-11): a preflight clears orphans of any killed
REM  prior cycle before touching the log, and every failure path keeps
REM  this window open long enough to read - it never just flashes shut.
REM ===================================================================
cd /d "%~dp0"
REM --hidden is passed by autopilot-hidden.vbs (the scheduled task's real
REM entry point). Nobody can read a window that is not being shown, so the
REM banner is skipped and the failure paths must NOT sit on a timeout -
REM they write reports\autopilot-status.txt and exit. Double-clicking this
REM .bat still behaves exactly as before.
set "ER_HIDDEN="
if /i "%~1"=="--hidden" set "ER_HIDDEN=1"
title Eight Rock Autopilot - RUNNING
if not defined ER_HIDDEN (
  echo Eight Rock Autopilot is running silently (30-45 min).
  echo Progress log: reports\autopilot.log - this window closes itself when done.
  echo To check from anywhere: type reports\autopilot-status.txt
)
if not exist reports mkdir reports
call "%~dp0_find-uv.bat" || goto :fail_uv

REM Preflight (owner 2026-08-11 "flashes too fast"): Task Scheduler's 3h
REM limit kills the cmd it launched but orphans the python children; an
REM orphan appending to reports\autopilot.log makes this window's first
REM ">>" redirect fail and the launcher exit in under a second. Clear
REM leftover cycle processes + stale git lock BEFORE the first append.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autopilot_preflight.ps1" > reports\autopilot-preflight.txt 2>&1

> reports\autopilot-status.txt echo RUNNING - started %date% %time%
REM A locked log is NORMAL, not fatal (lesson of 2026-08-15: a new launch
REM collided with the previous cycle writing its last lines, treated the
REM busy file as a hard failure, and the chain stayed dead for five days).
REM A finishing cycle releases the log within seconds - retry for ~90s,
REM then STAND DOWN quietly: the hourly trigger is the retry, exit 0.
REM (ping is the sleep here: `timeout` dies in a non-interactive session.)
set /a LOGTRY=0
:try_log
echo === Eight Rock Autopilot start %date% %time% === >> reports\autopilot.log
if not errorlevel 1 goto :log_ok
set /a LOGTRY+=1
if %LOGTRY% geq 7 goto :standdown
ping -n 16 127.0.0.1 >nul
goto :try_log
:log_ok
"%UV%" run python -u scripts/autopilot.py >> reports\autopilot.log 2>&1
"%UV%" sync >> reports\autopilot.log 2>&1
"%UV%" run python -u scripts/autopilot_run.py >> reports\autopilot.log 2>&1
if errorlevel 1 (
  > reports\autopilot-status.txt echo DONE with step failures - see reports\autopilot-run.log on GitHub  %date% %time%
) else (
  > reports\autopilot-status.txt echo DONE - finished %date% %time%
)
echo === Eight Rock Autopilot end %date% %time% === >> reports\autopilot.log
title Eight Rock Autopilot - DONE
exit /b 0

:fail_uv
echo.
echo !! Could not find or install uv - see the messages above.
> reports\autopilot-status.txt echo FAILED - uv missing  %date% %time%
call :publish_status
if not defined ER_HIDDEN timeout /t 60
exit /b 1

:standdown
REM Another cycle is genuinely running (the preflight already cleared any
REM ORPHANS, so a still-held log means a LIVE cycle). That is healthy -
REM say so honestly instead of crying FAILED, and let the next hourly
REM fire pick the work up. Exit 0: this is not an error.
echo.
echo Another Autopilot cycle is already running - standing down.
> reports\autopilot-status.txt echo STANDING DOWN - another cycle is running (normal); next hourly run continues  %date% %time%
call :publish_status
exit /b 0

REM Best-effort: push the status file so the outside world can see a
REM launcher-level failure. Five days of silence happened because every
REM failure before the first publish was written to a LOCAL file only.
REM Plain git, no uv needed; every step is allowed to fail quietly - a
REM status publish must never block or crash the launcher itself.
:publish_status
git add -f reports\autopilot-status.txt >nul 2>&1
git commit -m "autopilot launcher status" >nul 2>&1
git push origin main >nul 2>&1
exit /b 0
