@echo off
REM ===================================================================
REM  Make Norfolk sale history show up (owner 2026-08-15: "I need to see
REM  sale history in norfolk now. All properties.")
REM
REM  Forces the sale index to rebuild right now instead of waiting for
REM  the 3:00 AM cycle, then reports whether Norfolk's sales actually
REM  join to Norfolk's properties - and if they don't, prints the two
REM  parcel-id formats side by side so the mismatch is visible.
REM
REM  CLOSE THE WORKBENCH FIRST. The rebuild needs the single writer
REM  slot; with the app open you may get "database is locked".
REM ===================================================================
cd /d "%~dp0"
title Eight Rock - Norfolk sale history
echo Rebuilding the sale index and checking Norfolk...
echo (close the workbench first if this reports "database is locked")
echo.

call "%~dp0_find-uv.bat" || goto :fail_uv

"%UV%" run python -u scripts/fix_norfolk_sales.py
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
  echo Done. If section 5 says FIXED, restart the workbench and open a
  echo Norfolk deal - the app caches sale lookups for the life of the
  echo process, so a running instance will not pick this up.
) else (
  echo The script could not finish - screenshot the output above.
)
echo.
pause
