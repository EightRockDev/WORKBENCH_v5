@echo off
REM ===================================================================
REM  Fixes the ingest and installs it as a proper Windows service, so it
REM  starts itself after a reboot and never goes quiet again.
REM
REM  Double-click on the SERVER. It asks for administrator rights.
REM  Writes ingest-service-report.txt and opens it in Notepad.
REM ===================================================================
title Install ingest service
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

if not exist "install-ingest-service.ps1" (
  echo. & echo  STOPPED: install-ingest-service.ps1 is not next to this file. & echo.
  pause & popd & exit /b 1
)

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo  Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  popd
  exit /b
)

echo.
echo  Working... about 45 seconds.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-ingest-service.ps1" > "%~dp0ingest-service-report.txt" 2>&1

echo  Done. Opening the report.
echo  Copy everything in Notepad and send it to Claude.
echo.
start notepad "%~dp0ingest-service-report.txt"
timeout /t 5 >nul
popd
