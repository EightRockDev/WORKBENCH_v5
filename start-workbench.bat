@echo off
REM ===================================================================
REM  Eight Rock Workbench - launcher. Double-click to start the app.
REM  Always cd's to its own folder first, so the workbench can live
REM  anywhere on disk - the location is never hardcoded.
REM ===================================================================
cd /d "%~dp0"
set ER_DEV_LOGIN=1
echo Starting Eight Rock Workbench...
echo Open your browser to http://localhost:8501 once it is up.
echo (Close this window to stop the app.)
echo.
uv run python -m streamlit run app.py
pause
