@echo off
REM ===================================================================
REM  Eight Rock Workbench - launcher. Double-click to start the app.
REM  Always cd's to its own folder first, so the workbench can live
REM  anywhere on disk - the location is never hardcoded.
REM ===================================================================
cd /d "%~dp0"
title Eight Rock Workbench
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
set ER_DEV_LOGIN=1
REM The app runs as python.exe ("uv run python -m streamlit run app.py"), so
REM `taskkill /IM streamlit.exe` never matched it and the error was swallowed
REM by >nul. Every update left the OLD server running with the previous code
REM still loaded in memory - which is why the topbar pill kept reporting a
REM stale version after an update + restart + browser refresh. Kill whatever
REM is actually listening on the workbench ports; that is launch-agnostic.
for %%P in (8501 8502) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do (
    taskkill /F /PID %%I >nul 2>&1
  )
)
taskkill /F /IM streamlit.exe >nul 2>&1
echo Starting Eight Rock Workbench...
echo Open your browser to http://localhost:8501 once it is up.
echo (Close this window to stop the app.)
echo.
"%UV%" run python -m streamlit run app.py
pause
