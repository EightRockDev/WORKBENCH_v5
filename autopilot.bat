@echo off
REM ===================================================================
REM  Eight Rock Workbench AUTOPILOT - the whole data cycle, hands-free:
REM  update code -> discover feeds -> pull Hampton Roads -> rebuild the
REM  backbone -> publish every report to GitHub. Installed as a nightly
REM  scheduled task by install-autopilot.bat; safe to double-click too.
REM  Full log: reports\autopilot.log
REM ===================================================================
cd /d "%~dp0"
echo Eight Rock Autopilot is running silently (30-45 min).
echo Progress log: reports\autopilot.log - this window closes itself when done.
call "%~dp0_find-uv.bat" || exit /b 1
if not exist reports mkdir reports
echo === Eight Rock Autopilot start %date% %time% === >> reports\autopilot.log
"%UV%" run python -u scripts/autopilot.py >> reports\autopilot.log 2>&1
"%UV%" sync >> reports\autopilot.log 2>&1
"%UV%" run python -u scripts/autopilot_run.py >> reports\autopilot.log 2>&1
echo === Eight Rock Autopilot end %date% %time% === >> reports\autopilot.log
exit /b 0
