@echo off
REM ===================================================================
REM  Puts the swept deals into your Workbench property list.
REM
REM  Double-click        = PREVIEW. Shows what it would do, changes nothing.
REM  To actually write, open a command window here and run:
REM        promote-deals.bat GO
REM  Or make a shortcut with GO on the end.
REM ===================================================================
title Promote deals into the property list
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

if not exist "promote_deals.py" (
  echo. & echo  STOPPED: promote_deals.py is not in this folder. & echo.
  pause & popd & exit /b 1
)
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)

.venv\Scripts\python.exe promote_deals.py %1

echo.
pause
popd
