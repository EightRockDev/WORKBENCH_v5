@echo off
REM ===================================================================
REM  Eight Rock Workbench - launcher. Double-click to start the app.
REM  Always cd's to the project first, so it works from anywhere.
REM ===================================================================
cd /d C:\WORKBENCH_V5
set ER_DEV_LOGIN=1
echo Starting Eight Rock Workbench...
echo Open your browser to http://localhost:8501 once it is up.
echo (Close this window to stop the app.)
echo.
uv run python -m streamlit run app.py
pause
