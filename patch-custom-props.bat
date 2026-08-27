@echo off
REM ===================================================================
REM  Correct stored fields on custom properties. Today: Eastwyk Village
REM  coordinates, which were saved at the modal default and sit 5.37
REM  miles from the real address.
REM
REM  Double-click. The script prints a before/after diff and waits for
REM  you to type YES. Nothing is deleted. Safe to run twice - a field
REM  already correct is reported as such and skipped.
REM ===================================================================
cd /d "%~dp0"
title Patch custom properties - press a key to close when finished
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python -u scripts/patch_custom_props.py
echo.
pause
