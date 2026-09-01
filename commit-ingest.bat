@echo off
REM ===================================================================
REM  PREVIEW ONLY. Shows what would be committed to your repo.
REM  Changes nothing. Double-click on the SERVER.
REM ===================================================================
title Commit ingest - preview
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)
if not exist "commit-ingest.ps1" (
  echo. & echo  STOPPED: commit-ingest.ps1 is not next to this file. & echo.
  pause & popd & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0commit-ingest.ps1" > "%~dp0commit-report.txt" 2>&1
type "%~dp0commit-report.txt"
echo.
echo  (also saved as commit-report.txt)
echo.
pause
popd
