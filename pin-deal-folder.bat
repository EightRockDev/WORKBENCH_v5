@echo off
REM ===================================================================
REM  Point the app at the folder that holds your saved sale history.
REM
REM  Your deal folders live in the OneDrive/SharePoint sync root, which
REM  is nowhere near the app folder - so the app could not find them and
REM  fell back to guessing sale history from county records.
REM
REM  This finds the folder holding the most sales.json files and pins it
REM  permanently for your account. Restart the app afterwards.
REM ===================================================================
cd /d "%~dp0"
title Pin the deal folder
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python -u scripts/pin_deal_folder.py
echo.
pause
