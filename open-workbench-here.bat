@echo off
REM ===================================================================
REM  WORKBENCH - open it on THIS computer, without signing in.
REM
REM  Use this when the normal address will not let you in. It does not
REM  touch Auth0, Caddy, or the internet, so nothing out there can lock
REM  you out of your own data.
REM
REM  It runs on a private port that the public website does not forward
REM  to, and it accepts connections from this computer only - that is
REM  what makes skipping the sign-in safe rather than a hole. Anyone who
REM  could reach it is already sitting at this keyboard.
REM
REM  Your normal address keeps working exactly as before; this is a
REM  second door, not a replacement.
REM ===================================================================
cd /d "%~dp0"
title Workbench (this computer only)
call "%~dp0_find-uv.bat" || (pause & exit /b 1)

REM Both are required by core.session.local_console_login_enabled():
REM the flag alone does nothing unless the server is bound to loopback.
set ER_LOCAL_LOGIN=1

echo Starting the Workbench for this computer only...
echo.
echo   Open your browser to:  http://127.0.0.1:8599
echo.
echo   (Close this window to stop it.)
echo.
"%UV%" run python -m streamlit run app.py ^
  --server.address=127.0.0.1 ^
  --server.port=8599 ^
  --server.headless=true
pause
