@echo off
REM ===================================================================
REM  Install the workbench as a Windows service reachable on your
REM  office network. Double-click; it asks for admin rights itself.
REM ===================================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\install-lan-service.ps1" -AppDir "%~dp0."
echo.
pause
