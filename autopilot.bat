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
title Eight Rock Autopilot - RUNNING
echo Eight Rock Autopilot is running silently (30-45 min).
echo Progress log: reports\autopilot.log - this window closes itself when done.
echo To check from anywhere: type reports\autopilot-status.txt
if not exist reports mkdir reports
call "%~dp0_find-uv.bat" || goto :fail_uv

REM Preflight (owner 2026-08-11 "flashes too fast"): Task Scheduler's 3h
REM limit kills the cmd it launched but orphans the python children; an
REM orphan appending to reports\autopilot.log makes this window's first
REM ">>" redirect fail and the launcher exit in under a second. Clear
REM leftover cycle processes + stale git lock BEFORE the first append.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autopilot_preflight.ps1" > reports\autopilot-preflight.txt 2>&1

> reports\autopilot-status.txt echo RUNNING - started %date% %time%
echo === Eight Rock Autopilot start %date% %time% === >> reports\autopilot.log
if errorlevel 1 goto :fail_log
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
timeout /t 60
exit /b 1

:fail_log
echo.
echo !! Cannot write reports\autopilot.log - something still holds it open
echo !! even after preflight. Preflight report:
type reports\autopilot-preflight.txt
> reports\autopilot-status.txt echo FAILED - autopilot.log locked  %date% %time%
timeout /t 60
exit /b 1
