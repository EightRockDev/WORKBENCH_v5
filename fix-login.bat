@echo off
REM ===================================================================
REM  Sign-in says "Internal Server Error"? Run this.
REM
REM  The public address forwards to a PAIR of app instances. When only
REM  one of them is actually running, a share of requests - including
REM  the one that finishes signing you in - go to the dead one. This
REM  points the front door at whatever is really running and reloads it.
REM
REM  Safe to run any time. It only ever narrows the list to instances
REM  that answer, and it keeps a copy of the previous settings.
REM
REM  If you just need to get INTO the app right now, use
REM  open-workbench-here.bat instead - that skips the front door
REM  entirely.
REM ===================================================================
cd /d "%~dp0"
title Fix sign-in
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\fix-login.ps1" -AppDir "%~dp0."
echo.
pause
