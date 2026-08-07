@echo off
REM Eight Rock Data API launcher -- double-click to start (port 8600).
REM Thin launcher only (CLAUDE.md operator-loop rule 2): logic lives in
REM api_server.py. Local test: http://localhost:8600/v1/health
REM Public exposure is a Caddy route added at go-live, NOT this file.
cd /d "%~dp0"
uv run uvicorn api_server:app --host 127.0.0.1 --port 8600
pause
