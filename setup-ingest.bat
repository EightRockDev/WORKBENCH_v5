@echo off
REM ===================================================================
REM  Sets up deal ingest. Double-click this once. Nothing to type.
REM ===================================================================
setlocal enabledelayedexpansion
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

echo.
echo  ===============================================
echo   Setting up deal ingest
echo  ===============================================
echo.

REM --- check we are in the right folder --------------------------------
if not exist "api_server.py" (
  echo  STOPPED: I can't find api_server.py in this folder.
  echo.
  echo  This file needs to sit in C:\WORKBENCH_V5 next to api_server.py.
  echo  Move it there and double-click it again.
  echo.
  pause
  popd
  exit /b 1
)
if not exist "ingest_routes.py" (
  echo  STOPPED: ingest_routes.py is missing from this folder.
  echo.
  echo  Copy all 6 ingest files into WORKBENCH_V5 together,
  echo  then double-click this again.
  echo.
  pause
  popd
  exit /b 1
)
if not exist "ingest_client.py" (
  echo  STOPPED: ingest_client.py is missing from this folder.
  echo.
  echo  Copy all 6 ingest files into WORKBENCH_V5 together,
  echo  then double-click this again.
  echo.
  pause
  popd
  exit /b 1
)

echo  [1 of 4] Files are in the right place.
echo.

REM --- the one library it needs ----------------------------------------
echo  [2 of 4] Checking the library it needs (httpx)...
set "HTTPX_OK="
.venv\Scripts\python.exe -c "import httpx" >nul 2>&1
if not errorlevel 1 (
  set "HTTPX_OK=1"
  echo         Already installed.
) else (
  echo         Not there yet - installing...
  uv add httpx 2>&1 | findstr /i /c:"error" /c:"failed" >nul
  .venv\Scripts\python.exe -c "import httpx" >nul 2>&1
  if not errorlevel 1 (
    set "HTTPX_OK=1"
    echo         Installed.
  ) else (
    .venv\Scripts\python.exe -m pip install httpx 2>&1 | findstr /i /c:"Successfully" >nul
    .venv\Scripts\python.exe -c "import httpx" >nul 2>&1
    if not errorlevel 1 (
      set "HTTPX_OK=1"
      echo         Installed.
    )
  )
)

if not defined HTTPX_OK (
  echo.
  echo         COULD NOT INSTALL IT FROM HERE. Carrying on anyway -
  echo         the rest of the setup does not need it. Here is the
  echo         actual error, for Claude:
  echo         ------------------------------------------------------
  .venv\Scripts\python.exe -c "import httpx" 2>&1
  echo         ------------------------------------------------------
  echo         This usually means the script is running across the
  echo         network. Running it ON the desktop PC normally fixes it.
  echo.
)
echo.

REM --- password + settings ---------------------------------------------
echo  [3 of 4] Setting up the password file...
.venv\Scripts\python.exe setup_ingest_helper.py env
if errorlevel 1 (
  echo         SOMETHING WENT WRONG. Tell Claude what it says above.
  echo.
  pause
  popd
  exit /b 1
)
echo.

REM --- switch it on -----------------------------------------------------
echo  [4 of 4] Switching it on in api_server.py...
.venv\Scripts\python.exe setup_ingest_helper.py patch
if errorlevel 1 (
  echo         SOMETHING WENT WRONG. Tell Claude what it says above.
  echo.
  pause
  popd
  exit /b 1
)
echo.

echo  ===============================================
echo   SETUP FINISHED
echo  ===============================================
echo.
echo   Next, do these two things:
echo.
echo     1. Double-click  run-api.bat        (leave that window open)
echo     2. Double-click  check-ingest.bat   (tells you if it worked)
echo.
pause
popd
endlocal
