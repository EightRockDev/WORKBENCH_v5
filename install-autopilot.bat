@echo off
REM ===================================================================
REM  One-time setup: installs the nightly Autopilot (3:00 AM) and runs
REM  the first cycle right now. After this, the entire data loop is
REM  hands-free - no more windows to run or watch.
REM ===================================================================
cd /d "%~dp0"
echo Installing the Eight Rock Workbench Autopilot (nightly 3:00 AM)...
schtasks /Create /F /TN "EightRockWorkbenchAutopilot" /TR "\"%~dp0autopilot.bat\"" /SC DAILY /ST 03:00
if errorlevel 1 (
  echo Could not create the scheduled task - screenshot this window.
  pause
  exit /b 1
)
echo.
echo Task installed. Running the FIRST cycle now (30-45 minutes).
echo.
echo IF A GITHUB SIGN-IN WINDOW APPEARS: click "Sign in with your
echo browser", press the green Authorize button in the browser, and
echo wait for the small window to close by itself. One time only.
echo.
call "%~dp0autopilot.bat"
echo.
echo First cycle done. Log: reports\autopilot.log  (also pushed to GitHub)
echo From now on it runs every night at 3:00 AM. Nothing else to do.
pause
