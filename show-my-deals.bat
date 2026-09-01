@echo off
REM ===================================================================
REM  Lists the properties that came from the deal sweep. READ-ONLY.
REM  Double-click any time on the SERVER.
REM ===================================================================
title My swept deals
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)
.venv\Scripts\python.exe show_my_deals.py > "%~dp0my-deals.txt" 2>&1
type "%~dp0my-deals.txt"
echo.
echo  (also saved as my-deals.txt)
echo.
pause
popd
