@echo off
REM ===================================================================
REM  Sets up document copying. Double-click this once on the SERVER.
REM  Finds your synced 03-Deals folder, then shows what it would copy.
REM ===================================================================
title Set up document copying
pushd "%~dp0" 2>nul || (echo. & echo  STOPPED: could not open this folder. & pause & exit /b 1)

echo.
echo  ===============================================
echo   Setting up document copying
echo  ===============================================
echo.

if not exist "doc_sync.py" (
  echo  STOPPED: doc_sync.py is not in this folder.
  echo  Copy all the ingest files into WORKBENCH_V5 together.
  echo.
  pause
  popd
  exit /b 1
)
if not exist ".env" (
  echo  STOPPED: no .env file here. Run setup-ingest.bat first.
  echo.
  pause
  popd
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"

echo  [1 of 2] Finding your 03-Deals folder...
.venv\Scripts\python.exe setup_docsync_helper.py
if errorlevel 1 (
  echo.
  pause
  popd
  exit /b 1
)
echo.

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"

echo  [2 of 2] Checking what would be copied (nothing is changed yet)...
echo.
.venv\Scripts\python.exe doc_sync.py --preview

echo.
echo  ===============================================
echo   SETUP FINISHED - nothing has been copied yet
echo  ===============================================
echo.
echo   If the list above looks right, double-click:
echo       run-docsync.bat
echo.
echo   That is the one that actually copies the files.
echo.
pause
popd
