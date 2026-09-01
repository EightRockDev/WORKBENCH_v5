@echo off
REM ===================================================================
REM  Copies the deal documents onto this server.
REM  Double-click any time. Safe to run again - it skips what is current.
REM ===================================================================
title Copy deal documents
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

if not exist ".env" (
  echo. & echo  STOPPED: no .env file. Run setup-ingest.bat first. & echo.
  pause & popd & exit /b 1
)
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"

.venv\Scripts\python.exe doc_sync.py %1

echo.
echo  ---------------------------------------------------------------
echo   Done. Run this again any time - it only copies what is new.
echo   If anything says FAILED, send it to Claude.
echo  ---------------------------------------------------------------
echo.
pause
popd
