<#
  One-time setup for Module D (Connect my inbox).

  Writes two values into .env:
    ER_TOKEN_KEY        - encryption key for mailbox OAuth tokens at rest.
                          Generated automatically. NEVER regenerated if one
                          already exists (that would orphan connected mailboxes).
    MS_GRAPH_CLIENT_ID  - Microsoft Entra "Application (client) ID".
                          Prompted for; press Enter to keep the current value.

  ASCII only - Windows PowerShell 5.1 reads .ps1 as ANSI.
  Run via the double-click wrapper setup-inbox.bat.
#>
param([string]$AppDir = "C:\WORKBENCH_V5")
$ErrorActionPreference = "Stop"
Set-Location $AppDir
$envPath = Join-Path $AppDir ".env"

function Get-EnvValue([string]$name) {
    if (-not (Test-Path $envPath)) { return "" }
    $line = Get-Content $envPath | Where-Object { $_ -match ("^" + $name + "=") } | Select-Object -First 1
    if ($line) { return $line.Substring($name.Length + 1).Trim() }
    return ""
}

Write-Host ""
Write-Host "=== Inbox setup (Module D) ===" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------- token key
$tokenKey = Get-EnvValue "ER_TOKEN_KEY"
if ($tokenKey) {
    Write-Host "ER_TOKEN_KEY: already set - keeping it (regenerating would" -ForegroundColor Green
    Write-Host "              disconnect any mailbox already connected)." -ForegroundColor Green
} else {
    Write-Host "ER_TOKEN_KEY: generating a new encryption key..." -ForegroundColor Yellow
    $tokenKey = (uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") | Select-Object -Last 1
    $tokenKey = $tokenKey.Trim()
    if (-not $tokenKey) {
        Write-Host "Could not generate a key. Is uv installed and did 'uv sync' run?" -ForegroundColor Red
        exit 1
    }
    Write-Host "ER_TOKEN_KEY: created." -ForegroundColor Green
}

# ----------------------------------------------------------------- client id
$clientId = Get-EnvValue "MS_GRAPH_CLIENT_ID"
Write-Host ""
if ($clientId) {
    Write-Host ("Current MS_GRAPH_CLIENT_ID: " + $clientId)
    Write-Host "Press Enter to keep it, or paste a new Application (client) ID."
} else {
    Write-Host "Paste your Microsoft Entra 'Application (client) ID'."
    Write-Host "Do not have one yet? See docs\INBOX-SETUP.md - it takes ~3 minutes."
    Write-Host "Press Enter to skip for now (the inbox will run on demo data)."
}
$entered = Read-Host "Application (client) ID"
if ($entered) { $clientId = $entered.Trim() }

# --------------------------------------------------------------- write .env
$keep = @()
if (Test-Path $envPath) {
    $keep = Get-Content $envPath | Where-Object { $_ -notmatch '^(ER_TOKEN_KEY|MS_GRAPH_CLIENT_ID)=' }
}
$out = @($keep) + ("ER_TOKEN_KEY=" + $tokenKey)
if ($clientId) { $out = $out + ("MS_GRAPH_CLIENT_ID=" + $clientId) }
Set-Content -Path $envPath -Value $out -Encoding ASCII

Write-Host ""
Write-Host ("Saved to " + $envPath + " :") -ForegroundColor Green
Write-Host "   ER_TOKEN_KEY=****(hidden)"
if ($clientId) {
    Write-Host ("   MS_GRAPH_CLIENT_ID=" + $clientId)
} else {
    Write-Host "   MS_GRAPH_CLIENT_ID=(not set - inbox will use demo data)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "NEXT:" -ForegroundColor Yellow
Write-Host "  1. Close the black app window, then double-click start-workbench.bat." -ForegroundColor Yellow
Write-Host "  2. Open CRM and Sourcing -> Inbox to Deal -> Connect my Outlook mailbox." -ForegroundColor Yellow
Write-Host ""
Write-Host "Your mail stays private to your own login. Teammates and admins" -ForegroundColor Cyan
Write-Host "cannot read it; only the deals you create from it are shared." -ForegroundColor Cyan
