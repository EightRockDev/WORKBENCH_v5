@echo off
REM ===================================================================
REM  Shared helper: commit a run report (and optional extra file) and
REM  push to GitHub so Claude reads full results straight from the
REM  repo - no screenshots. Called by run-phase0 / pull-muni /
REM  discover-feeds. Never blocks the run: on any git failure it just
REM  says where the file is.
REM  Usage: _push-report.bat <report-file> "<label>" [extra-file]
REM ===================================================================
setlocal
set "FILE=%~1"
set "LABEL=%~2"
set "EXTRA=%~3"

REM Same dubious-ownership guard as update-workbench.bat (multi-account host).
set "REPO_FWD=%CD:\=/%"
git config --global --add safe.directory "%REPO_FWD%" >nul 2>&1

git add -f "%FILE%" >nul 2>&1
if not "%EXTRA%"=="" git add -f "%EXTRA%" >nul 2>&1
git -c user.name="Workbench Host" -c user.email="host@eight-rock.local" ^
    commit -m "host report: %LABEL%" >nul 2>&1
if errorlevel 1 (
  echo [report] Nothing new to push - report unchanged at %FILE%.
  goto :done
)
git pull --rebase --autostash origin main >nul 2>&1
git push origin main >nul 2>&1
if errorlevel 1 (
  REM One retry after re-syncing - covers a push that raced a new commit.
  git pull --rebase --autostash origin main >nul 2>&1
  git push origin main >nul 2>&1
)
if errorlevel 1 (
  echo [report] Could not push to GitHub - results are saved at %FILE%.
  echo [report] Tell Claude the push failed; screenshot as a fallback.
) else (
  echo [report] Full results pushed to GitHub - Claude can read them now.
)
:done
endlocal
