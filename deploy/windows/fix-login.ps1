# =====================================================================
#  Sign-in returns "Internal Server Error" -> point Caddy at the app
#  instances that are ACTUALLY RUNNING, and reload.
#
#  Owner locked out twice today (2026-08-15). Caddy forwards to a
#  blue/green PAIR - 127.0.0.1:8501 and :8502 - because both are meant
#  to run as services. Those services are blocked from starting by an
#  Application Control policy, so the app runs as ONE instance from
#  start-workbench.bat on 8501. Caddy still offers 8502 to the load
#  balancer, so a share of requests - including the /oauth2callback that
#  completes sign-in - are routed to a dead port and 500.
#
#  This rewrites the upstream list in the LIVE config (Caddyfile.active)
#  to exactly the ports that answer right now, validates it, and hot
#  reloads. It does not touch the repo template, so a later
#  install-caddy run still restores the full pair once both colours run.
# =====================================================================
param(
    [string]$AppDir = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
)
$ErrorActionPreference = 'Stop'

function Say($m) { Write-Host $m }

$active = Join-Path $AppDir "deploy\windows\Caddyfile.active"
if (-not (Test-Path $active)) {
    Say "Caddyfile.active not found - Caddy was never configured from this"
    Say "repo. Run install-caddy.ps1 first."
    exit 1
}

# --- which instances actually answer -------------------------------
$live = @()
foreach ($p in 8501, 8502) {
    if (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) {
        $live += "127.0.0.1:$p"
        Say "  port $p : LISTENING"
    } else {
        Say "  port $p : down"
    }
}
if ($live.Count -eq 0) {
    Say ""
    Say "Neither instance is running, so there is nothing for Caddy to"
    Say "forward to and every request will fail. Start the app first:"
    Say "   start-workbench.bat"
    exit 1
}

# --- rewrite the upstream list -------------------------------------
$text = Get-Content $active -Raw
$pattern = 'reverse_proxy\s+127\.0\.0\.1:\d+(\s+127\.0\.0\.1:\d+)*'
if ($text -notmatch $pattern) {
    Say ""
    Say "Could not find the reverse_proxy line in the live config."
    Say "Run install-caddy.ps1 to regenerate it."
    exit 1
}
$replacement = "reverse_proxy " + ($live -join " ")
$new = [regex]::Replace($text, $pattern, $replacement)

# With a single upstream, cookie affinity is meaningless and Caddy warns.
if ($live.Count -eq 1) {
    $new = $new -replace '(?m)^\s*lb_policy cookie er_upstream\s*\r?\n', ''
}

if ($new -eq $text) {
    Say ""
    Say "Live config already points at exactly the running instances."
} else {
    Copy-Item $active "$active.bak" -Force
    Set-Content -Path $active -Value $new -Encoding UTF8
    Say ""
    Say "Updated: $replacement"
    Say "(previous config saved alongside as Caddyfile.active.bak)"
}

# --- validate, then reload ------------------------------------------
$caddy = Get-Command caddy -ErrorAction SilentlyContinue
if (-not $caddy) {
    $guess = Join-Path $AppDir "deploy\windows\caddy.exe"
    if (Test-Path $guess) { $caddy = $guess } else {
        Say ""
        Say "caddy.exe not found - config updated but NOT reloaded."
        Say "Restart the Caddy service to pick it up."
        exit 1
    }
}
$caddyPath = if ($caddy -is [string]) { $caddy } else { $caddy.Source }

& $caddyPath validate --config $active --adapter caddyfile 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Say ""
    Say "The rewritten config FAILED validation - restoring the previous one."
    if (Test-Path "$active.bak") { Copy-Item "$active.bak" $active -Force }
    exit 1
}

$out = & $caddyPath reload --config $active --adapter caddyfile 2>&1
if ($LASTEXITCODE -ne 0) {
    Say ""
    Say "Reload failed: $out"
    Say "Restart the Caddy service to apply the change."
    exit 1
}

Say ""
Say "Caddy reloaded. Try signing in again - it should no longer be a"
Say "coin flip between a live instance and a dead one."
