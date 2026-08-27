@echo off
REM ===================================================================
REM  Submit Tidewater Gardens for the spec-16 verified badge and check
REM  it against the Norfolk assessor roll.
REM
REM  Double-click. It first prints what the Norfolk roll actually holds
REM  on Chesapeake Blvd, then waits for you to type YES. Read that block
REM  before answering - it explains the result either way.
REM ===================================================================
cd /d "%~dp0"
title Verify Tidewater Gardens - press a key to close when finished
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python -u scripts/verify_tidewater_gardens.py
echo.
pause
