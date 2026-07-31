@echo off
REM ===================================================================
REM  Install Caddy (the HTTPS front door) and register it as a service
REM  pointed at deploy\windows\Caddyfile. Double-click; it elevates.
REM
REM  Caddy gets its own certificate from Let's Encrypt automatically -
REM  but ONLY once workbench.eight-rock.com resolves to this network's
REM  public IP and ports 80/443 reach this PC. Run the DNS step first.
REM ===================================================================
cd /d "%~dp0"
title Install Caddy - press a key to close when finished
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\install-caddy.ps1" -AppDir "%~dp0."
echo.
pause
