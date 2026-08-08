@echo off
REM Eight Rock Workbench -- one-time backup-role setup.
REM Right-click this file -> "Run as administrator".
REM Thin launcher only (CLAUDE.md rule 2): all logic is in the .ps1.
REM It self-elevates if you double-click without admin.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\setup-backup.ps1"
echo.
pause
