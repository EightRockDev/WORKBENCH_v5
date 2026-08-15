@echo off
REM ===================================================================
REM  Move the deal folders into C:\WORKBENCH_V5 (owner decision
REM  2026-08-15) and pin the app to them.
REM
REM  COPIES - your original folder is left untouched until you delete
REM  it yourself, after you have seen the app working. Verifies the
REM  counts on both sides before saying it succeeded.
REM
REM  Restart the app afterwards.
REM ===================================================================
cd /d "%~dp0"
title Move deal folders into the app folder
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python -u scripts/move_deal_folders.py
echo.
pause
