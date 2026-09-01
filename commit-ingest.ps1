# Commits the ingest files into the WORKBENCH_V5 repo so a git pull reinforces
# them instead of stripping the switch-on line out of api_server.py.
#
#   commit-ingest.bat          PREVIEW - shows what it would commit, changes nothing
#   commit-ingest.bat GO       actually commit and push
#
# Never stages .env. That file holds the ingest password and must stay local.

$AppDir = 'C:\WORKBENCH_V5'
$Go     = ($args -contains 'GO')

function Section($t) { "`n===== $t =====" }

Section "WHEN"
Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'
"mode: $(if ($Go) {'COMMIT AND PUSH'} else {'PREVIEW - nothing will change'})"

Set-Location $AppDir

# --- 0. Is this actually a git repo, and is git available -------------------
Section "0. CHECKS"
$git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $git) { "STOPPED: git is not installed or not on PATH."; exit 1 }
"git: $git"

if (-not (Test-Path "$AppDir\.git")) {
    "STOPPED: $AppDir is not a git repository."; exit 1
}

$branch = (& git rev-parse --abbrev-ref HEAD 2>&1 | Out-String).Trim()
$remote = (& git remote get-url origin 2>&1 | Out-String).Trim()
"branch: $branch"
"origin: $remote"

# --- 1. The .env must never be committed -----------------------------------
Section "1. PROTECTING THE PASSWORD FILE"
$envTracked = (& git ls-files --error-unmatch .env 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -eq 0) {
    "WARNING: .env is ALREADY TRACKED by git - your ingest password may be in"
    "the repo history. Removing it from tracking now (the file stays on disk)."
    if ($Go) { & git rm --cached .env 2>&1 | ForEach-Object { "  $_" } }
} else {
    ".env is not tracked. Good."
}

$gitignore = "$AppDir\.gitignore"
$ignoreLines = if (Test-Path $gitignore) { Get-Content $gitignore } else { @() }
# Normalise line endings once so git stops warning on every add.
& git config core.autocrlf true 2>&1 | Out-Null

$needed = @('.env', 'logs/', 'data/deal_docs/', '*.bak-*', 'my-deals.txt',
            'schema-report.txt', 'ingest-report.txt', 'ingest-fix-report.txt',
            'ingest-harden-report.txt', 'cloudflare-report.txt',
            'ingest-service-report.txt')
$missing = $needed | Where-Object { $ignoreLines -notcontains $_ }
if ($missing) {
    "Adding to .gitignore: $($missing -join ', ')"
    if ($Go) {
        Add-Content $gitignore ("`n# Eight Rock ingest - local only`n" + ($missing -join "`n"))
    }
} else {
    ".gitignore already covers the local-only files."
}

# --- 2. What we intend to commit -------------------------------------------
Section "2. FILES TO COMMIT"
$wanted = @(
    'api_server.py',              # carries the two-line switch-on
    'ingest_routes.py',
    'ingest_client.py',
    'promote_deals.py',
    'doc_sync.py',
    'show_my_deals.py',
    'setup_ingest_helper.py',
    'setup_docsync_helper.py',
    'setup-ingest.bat',
    'check-ingest.bat',
    'setup-docsync.bat',
    'run-docsync.bat',
    'promote-deals.bat',
    'promote-deals-GO.bat',
    'show-my-deals.bat',
    'install-ingest-service.bat',
    'install-ingest-service.ps1',
    'commit-ingest.bat',
    'commit-ingest.ps1',
    'test_ingest_routes.py',
    'INGEST-API-README.md',
    'API-REFERENCE.md'
)
$present = $wanted | Where-Object { Test-Path (Join-Path $AppDir $_) }
$absent  = $wanted | Where-Object { -not (Test-Path (Join-Path $AppDir $_)) }

foreach ($f in $present) {
    $tracked = (& git ls-files --error-unmatch $f 2>&1 | Out-String)
    $state = if ($LASTEXITCODE -eq 0) { 'tracked' } else { 'NEW' }
    "  {0,-8} {1}" -f $state, $f
}
if ($absent) { "`n  not present, skipping: $($absent -join ', ')" }

# The switch-on line is the whole point - confirm it is there before committing.
Section "3. THE SWITCH-ON LINE"
if (Select-String -Path "$AppDir\api_server.py" -Pattern 'include_ingest_routes' -Quiet) {
    "api_server.py HAS the switch-on line - this is what we are protecting."
} else {
    "STOPPED: api_server.py does NOT have the switch-on line."
    "Run install-ingest-service.bat first, then come back."
    exit 1
}

# --- 4. Show the diff summary ----------------------------------------------
Section "4. WHAT CHANGES"
& git add --intent-to-add $present 2>&1 | Out-Null
$stat = (& git diff --stat -- $present 2>&1 |
         Where-Object { $_ -notmatch '^\s*warning:.*LF will be replaced' } |
         Out-String).Trim()
if ($stat) { $stat } else { "  (no textual changes - files may already be committed)" }

if (-not $Go) {
    Section "PREVIEW ONLY"
    "Nothing was committed."
    "If that list looks right, double-click:  commit-ingest-GO.bat"
    exit 0
}

# --- 5. Commit --------------------------------------------------------------
Section "5. COMMITTING"
& git add $present 2>&1 |
    Where-Object { $_ -notmatch '^\s*warning:.*LF will be replaced' } |
    ForEach-Object { "  $_" }
if (Test-Path $gitignore) { & git add .gitignore 2>&1 | Out-Null }

$msg = @"
Add Eight Rock deal ingest to the Workbench

Receives deals from the daily SharePoint 03-Deals sweep and updates the
property list automatically.

- ingest_routes.py   /v1/ingest/* mounted into api_server.py on 8600
- promote_deals.py   adds new deals to `properties`, fills in existing rows
- doc_sync.py        copies the rent rolls, T-12s and OMs onto this machine
- ingest_client.py   client other Workbench modules import

Committing these so a pull reinforces the two-line include in api_server.py
rather than stripping it - that wipe took the ingest down 8/27 to 9/01.

The ingest password lives in .env and is deliberately NOT committed.
"@
$msg | Out-File -FilePath "$env:TEMP\ingest-commit-msg.txt" -Encoding utf8
& git commit -F "$env:TEMP\ingest-commit-msg.txt" 2>&1 |
    Where-Object { $_ -notmatch '^\s*warning:.*LF will be replaced' } |
    ForEach-Object { "  $_" }

Section "6. PUSHING"
$push = (& git push origin $branch 2>&1 | Out-String)
$push -split "`n" | ForEach-Object { if ($_.Trim()) { "  $($_.Trim())" } }
if ($LASTEXITCODE -ne 0) {
    ""
    "PUSH FAILED - but the commit is saved locally, and that alone stops the"
    "next pull from wiping api_server.py (git merges instead of overwriting)."
    "Send the message above to Claude if you want the push sorted out."
} else {
    "Pushed to origin/$branch."
}

# --- 7. Prove it ------------------------------------------------------------
Section "7. CONFIRMING"
foreach ($f in @('api_server.py','ingest_routes.py','promote_deals.py','doc_sync.py')) {
    & git ls-files --error-unmatch $f 2>&1 | Out-Null
    "  {0,-8} {1}" -f $(if ($LASTEXITCODE -eq 0) {'tracked'} else {'MISSING'}), $f
}
& git ls-files --error-unmatch .env 2>&1 | Out-Null
"  {0,-8} .env  (must say 'not tracked')" -f $(if ($LASTEXITCODE -eq 0) {'TRACKED!'} else {'not tracked'})

Section "DONE"
"The ingest files are now part of the repo. A Workbench update will keep the"
"switch-on line in api_server.py instead of removing it."
