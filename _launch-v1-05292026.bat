@echo off
REM Internal helper: launches V1 Streamlit on port 8501. Called by run.bat.
REM Explicitly UNSETS ER_THEME so V1 is guaranteed to render in V1 mode
REM regardless of what's in the parent shell.

setlocal
set "ER_THEME="

set "UV="
set "TRY=%USERPROFILE%\.local\bin\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
set "TRY=%APPDATA%\Python\Python314\Scripts\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
set "TRY=%APPDATA%\Python\Python313\Scripts\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
set "TRY=%APPDATA%\Python\Python312\Scripts\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
set "TRY=%APPDATA%\Python\Python311\Scripts\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
set "TRY=%LOCALAPPDATA%\Programs\Python\Python314\Scripts\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
set "TRY=%LOCALAPPDATA%\Programs\Python\Python313\Scripts\uv.exe"
if exist "%TRY%" (set "UV=%TRY%"& goto :found)
where uv >nul 2>nul
if not errorlevel 1 (set "UV=uv"& goto :found)

echo.
echo ERROR: uv not found. Run setup-05062026.bat first.
echo.
pause
exit /b 1

:found
cd /d "%~dp0"

echo.
echo ============================================================
echo Eight Rock Workbench -- V1 (current Streamlit)
echo ============================================================
echo.
echo Port = 8501
echo URL  = http://localhost:8501/
echo uv   = %UV%
echo.

"%UV%" run python -m streamlit run app.py --server.port=8501 --server.headless=false

endlocal
pause
