@echo off
REM ===================================================================
REM  Tells you whether deal ingest is working. Double-click any time.
REM ===================================================================
setlocal
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

if not exist "ingest_client.py" (
  echo.
  echo  STOPPED: ingest_client.py is not in this folder.
  echo  All the ingest files have to sit together in WORKBENCH_V5.
  echo.
  pause
  popd
  exit /b 1
)

REM Load the settings out of .env so the check can see the password.
if not exist ".env" (
  echo.
  echo  STOPPED: no .env file here. Run setup-ingest.bat first.
  echo.
  pause
  popd
  exit /b 1
)
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"

.venv\Scripts\python.exe ingest_client.py

echo.
echo  ---------------------------------------------------------------
echo   If it says PASS, it is working.
echo   If it says FAIL, copy the message above and send it to Claude.
echo  ---------------------------------------------------------------
echo.
pause
popd
endlocal
