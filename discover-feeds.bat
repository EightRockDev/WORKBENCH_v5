@echo off
REM ===================================================================
REM  Find unit-bearing parcel layers for the uncovered Hampton Roads
REM  cities (VB, Chesapeake, Hampton, Portsmouth, Suffolk, Norfolk) by
REM  probing their public GIS portals. Writes data\feeds_extra.json.
REM  Double-click; takes a few minutes; read-only on the internet.
REM  The output AND the discovered feed list are pushed to GitHub so
REM  Claude reads the results directly - no screenshots.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
if not exist reports mkdir reports
"%UV%" run python -u scripts/discover_feeds.py 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -Variable out; $out | Set-Content -Path 'reports\discover-latest.txt' -Encoding UTF8"
call "%~dp0_push-report.bat" reports\discover-latest.txt "discover-feeds" data\feeds_extra.json
echo.
pause
