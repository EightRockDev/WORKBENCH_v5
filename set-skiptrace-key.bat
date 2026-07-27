@echo off
REM ===================================================================
REM  Set the BatchData API key + enable live skip trace. Double-click.
REM  (Calls a clean PowerShell script; prompts for the key, updates .env.)
REM ===================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\set-skiptrace-key.ps1" -AppDir "%~dp0."
echo.
pause
