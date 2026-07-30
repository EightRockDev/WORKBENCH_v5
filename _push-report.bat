@echo off
REM ===================================================================
REM  Shared helper: commit a run report (and optional extra file) and
REM  push to GitHub so Claude reads full results straight from the
REM  repo - no screenshots. Called by run-phase0 / pull-muni /
REM  discover-feeds. Shows git's real errors; never blocks the run.
REM  Usage: _push-report.bat <report-file> "<label>" [extra-file]
REM ===================================================================
setlocal
set "FILE=%~1"
set "LABEL=%~2"
set "EXTRA=%~3"

if not exist "%FILE%" (
  echo [report] %FILE% was not written - nothing to publish this run.
  goto :done
)

REM Multi-account host: trust the folder and give THIS repo a committer
REM identity regardless of which Windows account runs the tool.
set "REPO_FWD=%CD:\=/%"
git config --global --add safe.directory "%REPO_FWD%" >nul 2>&1
git config user.name "Workbench Host" >nul 2>&1
git config user.email "host@eight-rock.local" >nul 2>&1

echo [report] Publishing results to GitHub...
REM Current repo name + persistent credential store (sign in once, ever).
git remote set-url origin https://github.com/EightRockDev/WORKBENCH_v5.git >nul 2>&1
git config credential.helper manager >nul 2>&1

REM Clear EVERY kind of wedged git state debris left by earlier failures.
git rebase --abort >nul 2>&1
git cherry-pick --abort >nul 2>&1
git merge --abort >nul 2>&1
if exist ".git\rebase-merge" rmdir /s /q ".git\rebase-merge" >nul 2>&1
if exist ".git\rebase-apply" rmdir /s /q ".git\rebase-apply" >nul 2>&1
git symbolic-ref -q HEAD >nul 2>&1
if errorlevel 1 git checkout -B main

git add -f "%FILE%"
if not "%EXTRA%"=="" git add -f "%EXTRA%"
git commit -m "host report: %LABEL%"
if errorlevel 1 (
  echo [report] Nothing new to publish - report unchanged at %FILE%.
  goto :done
)
git pull --rebase --autostash origin main
git push origin main
if errorlevel 1 (
  git pull --rebase --autostash origin main
  git push origin main
)
if errorlevel 1 (
  echo.
  echo [report] PUSH FAILED - the git error is printed above this line.
  echo [report] Results are saved at %FILE%.
  echo [report] Screenshot this window for Claude - the error above is the fix.
) else (
  echo [report] Full results pushed to GitHub - Claude can read them now.
)
:done
endlocal
