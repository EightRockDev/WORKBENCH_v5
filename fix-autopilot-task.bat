@echo off
REM ===================================================================
REM  Autopilot not running? Run this once. It rebuilds the scheduled
REM  task EXACTLY right, so nothing depends on hand-typed settings.
REM
REM  What it sets, and why each one matters (all learned the hard way,
REM  2026-08-15..19, when the chain was silent for five days):
REM    * The task starts the SILENT launcher (autopilot-hidden.vbs),
REM      never autopilot.bat directly - no console window pops up.
REM    * It fires HOURLY as a restart net; a cycle that is already
REM      running simply stands down, so overlap is harmless.
REM    * It runs as YOU, only when you are logged on. That is what
REM      keeps the cycle's GitHub sign-in working - the "whether user
REM      is logged on or not" option silently breaks publishing.
REM      Lock the screen instead of signing out.
REM ===================================================================
cd /d "%~dp0"
title Fix the Autopilot task

if not exist "%~dp0autopilot-hidden.vbs" (
  echo !! autopilot-hidden.vbs is missing - run update-workbench.bat first.
  pause
  exit /b 1
)

echo Rebuilding the EightRockWorkbenchAutopilot task...
schtasks /Create /F /TN "EightRockWorkbenchAutopilot" ^
  /TR "wscript.exe //nologo \"%~dp0autopilot-hidden.vbs\"" ^
  /SC HOURLY /MO 1
if errorlevel 1 (
  echo.
  echo !! Could not rebuild the task - screenshot this window.
  pause
  exit /b 1
)

echo.
echo === What is now registered (verify the Task line shows wscript) ===
schtasks /Query /TN "EightRockWorkbenchAutopilot" /V /FO LIST | findstr /C:"Task To Run" /C:"Schedule Type" /C:"Run As User" /C:"Status"

echo.
echo Done. The Autopilot now fires every hour, silently, as you.
echo Starting one cycle right now so you do not wait for the next hour...
REM start, not a direct call: the launcher now WAITS for the whole cycle
REM (that wait is its evidence trail), and this window must not.
start "" wscript.exe //nologo "%~dp0autopilot-hidden.vbs"
echo.
echo Cycle launched in the background. Check progress any time with:
echo    type reports\autopilot-status.txt
echo A fresh report should appear on GitHub within about 15 minutes.
pause
