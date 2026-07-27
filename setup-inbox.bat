@echo off
REM ===================================================================
REM  One-time inbox setup (Module D). Double-click this file.
REM  Generates the token-encryption key and stores your Microsoft
REM  Entra Application (client) ID in .env.
REM ===================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\setup-inbox.ps1" -AppDir "%~dp0."
echo.
pause
