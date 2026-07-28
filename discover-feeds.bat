@echo off
REM ===================================================================
REM  Find unit-bearing parcel layers for the uncovered Hampton Roads
REM  cities (VB, Chesapeake, Hampton, Portsmouth, Suffolk) by probing
REM  their public GIS portals. Writes data\feeds_extra.json.
REM  Double-click; takes a few minutes; read-only on the internet.
REM ===================================================================
cd /d "%~dp0"
call "%~dp0_find-uv.bat" || (pause & exit /b 1)
"%UV%" run python scripts/discover_feeds.py
echo.
pause
