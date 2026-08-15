@echo off
REM ===================================================================
REM  Stop the Autopilot console window from appearing (owner 2026-08-15).
REM
REM  Re-points the existing scheduled task at autopilot-hidden.vbs, which
REM  runs the exact same cycle with the window hidden. Takes two seconds
REM  and does NOT start a cycle - unlike re-running install-autopilot.bat,
REM  which would kick off a fresh 30-45 minute run just to change this.
REM
REM  Nothing is lost by hiding it: the window only ever echoed what
REM  reports\autopilot.log and reports\autopilot-status.txt already hold.
REM  Double-clicking autopilot.bat still shows a window, for debugging.
REM ===================================================================
cd /d "%~dp0"
echo Re-pointing the Autopilot task at the hidden launcher...
echo.

if not exist "%~dp0autopilot-hidden.vbs" (
  echo !! autopilot-hidden.vbs is missing from this folder.
  echo !! Run update-workbench.bat first, then try again.
  pause
  exit /b 1
)

schtasks /Create /F /TN "EightRockWorkbenchAutopilot" /TR "wscript.exe //nologo \"%~dp0autopilot-hidden.vbs\"" /SC DAILY /ST 03:00
if errorlevel 1 (
  echo.
  echo Could not update the scheduled task - screenshot this window.
  pause
  exit /b 1
)

echo.
echo Done. The 3:00 AM cycle now runs with no window.
echo.
echo Check on it any time with:
echo    type reports\autopilot-status.txt
echo.
echo To watch a cycle live, or to debug one, double-click autopilot.bat
echo directly - that still opens a visible window on purpose.
echo.
pause
