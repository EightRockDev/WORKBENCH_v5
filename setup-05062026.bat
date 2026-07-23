@echo off
REM Eight Rock Workbench - one-time setup.
REM Installs uv (the Python package manager) on this machine.
REM Run once, then double-click run.bat from now on.

setlocal

echo ============================================================
echo Eight Rock Workbench - First-Time Setup
echo ============================================================
echo.
echo This will install uv (a Python package manager) into:
echo   %USERPROFILE%\.local\bin\uv.exe
echo.
echo It does NOT need administrator privileges.
echo It does NOT modify your system Python or PATH globally.
echo.
echo Press any key to start the install (or close this window to cancel)...
pause >nul

echo.
echo Installing... this takes 30-60 seconds.
echo.

powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

if errorlevel 1 (
    echo.
    echo ============================================================
    echo INSTALL FAILED
    echo ============================================================
    echo.
    echo If you have an internet connection, try this manually:
    echo   1. Press Windows key, type "powershell", open it
    echo   2. Paste this command and press Enter:
    echo      irm https://astral.sh/uv/install.ps1 ^| iex
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo SUCCESS
echo ============================================================
echo.
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    echo uv installed at: %USERPROFILE%\.local\bin\uv.exe
) else (
    echo uv installed - location varies, run.bat will find it
)
echo.
echo Next step: double-click run.bat to start the workbench.
echo.
pause
