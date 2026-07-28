@echo off
REM ===================================================================
REM  Shared helper: locate uv.exe and set the UV variable.
REM  Called by the launchers - do not double-click this file directly.
REM
REM  Order: PATH, this user's install spots, any other user's install
REM  (uv is per-user, so an app set up under one Windows account is
REM  otherwise invisible to a second account), then auto-install via
REM  winget as a last resort.
REM ===================================================================
set "UV="

where uv >nul 2>&1
if %errorlevel%==0 set "UV=uv"

if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV=%LOCALAPPDATA%\Programs\uv\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"

if not defined UV (
  for /d %%U in ("C:\Users\*") do (
    if not defined UV if exist "%%U\.local\bin\uv.exe" set "UV=%%U\.local\bin\uv.exe"
    if not defined UV if exist "%%U\AppData\Local\Programs\uv\uv.exe" set "UV=%%U\AppData\Local\Programs\uv\uv.exe"
  )
)

if not defined UV (
  echo uv is not installed for this Windows account - installing it now...
  REM Official standalone installer - no winget needed (winget is often
  REM broken/uninitialized on a freshly created Windows account).
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not defined UV if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV=%LOCALAPPDATA%\Programs\uv\uv.exe"
)

if not defined UV (
  echo The standalone installer did not work - trying winget...
  winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not defined UV if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV=%LOCALAPPDATA%\Programs\uv\uv.exe"
  if not defined UV (
    where uv >nul 2>&1
    if %errorlevel%==0 set "UV=uv"
  )
)

if not defined UV (
  echo.
  echo !! Could not find or install uv. Install it from https://astral.sh/uv
  echo    then run this again.
  exit /b 1
)
exit /b 0
