@echo off
REM Internal helper: launches the V2 Streamlit server on port 8502.
REM Sets ER_THEME=v2 BEFORE invoking streamlit so the env var reliably
REM reaches the python process. Called by run.bat.
REM
REM Why a separate file: Windows `cmd /k "set X=y && ..."` quoting is
REM fragile -- the env var sometimes fails to propagate to the child
REM process if the path has unusual characters. Doing it here in a
REM dedicated script with setlocal makes it bulletproof.

setlocal
set "ER_THEME=v2"

REM Resolve uv.exe (same logic as run.bat — duplicated rather than
REM imported because batch scripts don't have clean imports).
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
echo Eight Rock Workbench -- V2.0 'Quiet Operator'
echo ============================================================
echo.
echo ER_THEME = %ER_THEME%   ^<-- must say "v2"
echo Port     = 8502
echo URL      = http://localhost:8502/
echo uv       = %UV%
echo.
echo If ER_THEME above is NOT "v2", something is broken --
echo close this window and tell Claude.
echo.

"%UV%" run python -m streamlit run app.py --server.port=8502 --server.headless=false

endlocal
pause
