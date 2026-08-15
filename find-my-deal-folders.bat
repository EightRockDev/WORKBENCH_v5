@echo off
REM ===================================================================
REM  Where is the app reading your deal folders from?
REM  (Read-only. The previous version of this file inlined Python into
REM  the batch script and could not parse - it calls a real script now.)
REM ===================================================================
cd /d "%~dp0"
title Where are my deal folders?
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python -u scripts/where_are_deals.py
echo.
pause
