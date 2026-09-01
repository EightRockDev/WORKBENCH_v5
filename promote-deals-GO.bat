@echo off
REM ===================================================================
REM  THIS ONE ACTUALLY WRITES. Run promote-deals.bat first to preview.
REM  Makes a backup of the database before touching anything.
REM ===================================================================
title Promote deals - WRITING
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)

echo.
echo  This will add the swept deals to your property list.
echo  Run promote-deals.bat first if you have not previewed it.
echo.
choice /C YN /M "Continue"
if errorlevel 2 (
  echo  Cancelled. Nothing changed.
  echo.
  pause & popd & exit /b 0
)

.venv\Scripts\python.exe promote_deals.py GO

echo.
pause
popd
