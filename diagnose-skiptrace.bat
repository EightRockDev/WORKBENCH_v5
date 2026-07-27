@echo off
REM ===================================================================
REM  Skip-trace diagnostic. Double-click, then enter an owner name +
REM  mailing address. Prints the raw + parsed vendor response.
REM ===================================================================
cd /d "%~dp0"
set /p NAME="Owner name (e.g. Robert Cleghorn): "
set /p ADDR="Mailing address (e.g. 1200 Ballentine Blvd, Norfolk VA): "
echo.
uv run python scripts/diagnose_skiptrace.py --name "%NAME%" --address "%ADDR%"
echo.
pause
