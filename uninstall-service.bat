@echo off
REM ===================================================================
REM  Remove the workbench Windows service + firewall rule. Double-click.
REM ===================================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
nssm stop EightRockWorkbench
nssm remove EightRockWorkbench confirm
powershell -NoProfile -Command "Remove-NetFirewallRule -DisplayName 'Eight Rock Workbench' -ErrorAction SilentlyContinue"
echo Service removed. start-workbench.bat still works as before.
echo.
pause
