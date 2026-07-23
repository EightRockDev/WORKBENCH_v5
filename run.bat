@echo off
REM Eight Rock Workbench launcher -- double-click to start.
REM Launches BOTH V1 (port 8501) and V2 (port 8502) in separate console
REM windows. Side-by-side comparison from one click.
REM
REM   V1: http://localhost:8501/  (existing Streamlit UI)
REM   V2: http://localhost:8502/  (Quiet Operator restyle, ER_THEME=v2)
REM
REM Architecture: each version is launched via its own wrapper script
REM (_launch-v1-* and _launch-v2-*) to bulletproof env var handling.
REM The wrappers do their own uv lookup + cd, so this file is just two
REM `start` calls.
REM
REM To stop: close THIS window AND the two server windows that pop up.

setlocal
cd /d "%~dp0"

echo.
echo Starting Eight Rock Workbench (V1 + V2)...
echo.
echo   V1 (current UI):       http://localhost:8501/
echo   V2.0 (Quiet Operator): http://localhost:8502/
echo.
echo Two new console windows will open below this one.
echo Each will open its browser tab automatically.
echo.

REM Launch V1 in its own window via the V1 helper.
start "Workbench V1 - port 8501" "%~dp0_launch-v1-05292026.bat"

REM Tiny pause so the two Streamlit processes don't race on the cache dir.
timeout /t 2 /nobreak >nul

REM Launch V2 in its own window via the V2 helper (which sets ER_THEME=v2
REM cleanly via `setlocal` -- no fragile inline `set && cmd` quoting).
start "Workbench V2.0 - port 8502" "%~dp0_launch-v2-05292026.bat"

echo.
echo Both servers launching. Two new console windows are opening.
echo.
echo TO VERIFY V2 IS ACTUALLY ACTIVE:
echo   1. Look at the V2 window. It should print: ER_THEME = v2
echo   2. Open http://localhost:8502/ in your browser.
echo   3. The top of the page should say: V2.0.MMDDYYYY * Quiet Operator
echo      ...as well as a different layout (hero, chip row, stat cards).
echo   4. If V2 looks identical to V1, close all windows and tell Claude --
echo      something is broken with the env var.
echo.
echo This window can be closed any time without affecting V1 or V2.
echo.

endlocal
pause
