@echo off
REM ===================================================================
REM  Add Tidewater Gardens to the Workbench as a custom property.
REM  Double-click. The script prints what it will write and waits for
REM  you to type YES before touching anything. Nothing is deleted and
REM  no other property is modified. Safe to run twice - the row is
REM  keyed on a deterministic id, so a re-run updates instead of
REM  creating a second building.
REM ===================================================================
cd /d "%~dp0"
title Add Tidewater Gardens - press a key to close when finished
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python -u scripts/add_tidewater_gardens.py
echo.
pause
