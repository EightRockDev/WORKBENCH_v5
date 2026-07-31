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
REM Install BOTH colours. One service is not zero-downtime: Caddy needs a
REM second upstream to route to while the first restarts, and deploy-swap.ps1
REM restarts them by name. Blue prompts for the passcode; green reuses it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\install-lan-service.ps1" -AppDir "%~dp0." -Name WorkbenchBlue -Port 8501
if errorlevel 1 goto :failed
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\install-lan-service.ps1" -AppDir "%~dp0." -Name WorkbenchGreen -Port 8502 -KeepPasscode
if errorlevel 1 goto :failed
echo.
echo Both services installed. Verify with:
echo   powershell -ExecutionPolicy Bypass -File deploy\windows\deploy-swap.ps1
goto :done

:failed
echo.
echo INSTALL FAILED - see the error above. Nothing further was attempted.

:done
echo.
pause
