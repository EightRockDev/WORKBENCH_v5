@echo off
REM ===================================================================
REM  Where is the app reading your deal folders from?
REM
REM  Your hand-verified sale history lives in a sales.json inside each
REM  property folder. v2 and v5 both look "one folder above the app" -
REM  which is a DIFFERENT place for each, because they sit in different
REM  folders. That is why sale history that worked in v2 went missing.
REM
REM  This prints which folder the app settled on, how many deals it
REM  found, and how many carry sale history. Read-only.
REM ===================================================================
cd /d "%~dp0"
title Where are my deal folders?

call "%~dp0_find-uv.bat" || (pause & exit /b 1)

"%UV%" run python -c "import sys; sys.path.insert(0,'.'); ^
from data.property_io import PROPERTIES_ROOT, discover_property_folders; ^
from pathlib import Path; ^
print('Reading deal folders from:'); print('   ', PROPERTIES_ROOT); ^
fs=list(discover_property_folders()); print(); ^
print('   deal folders found:', len(fs)); ^
n=sum(1 for f in fs if (Path(f.path)/'sales.json').exists()); ^
print('   with sale history :', n); print(); ^
print('   ' + ('If deal folders found is 0, the app is pointing at the wrong place.' if not fs else 'Looks right.')); ^
print('   To pin it explicitly, set ER_PROPERTIES_ROOT to the folder that'); ^
print('   contains your property folders, then restart the app.')"

echo.
pause
