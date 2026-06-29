# Hub: export fleet/manifest.json (+ fleet/secrets.env) for workers. Optional git push.
param(
    [switch]$Push,
    [switch]$PushSecrets,
    [string]$Branch = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

$EnvPath = Join-Path $Root ".env"
$EnvMap = Read-DotEnv -Path $EnvPath
$CredsPath = Join-Path $Root "data\fleet-hub-credentials.txt"

$hubIp = Get-TailscaleIp4
if (-not $hubIp) {
    $pub = $EnvMap["FLEET_PUBLIC_URL"]
    if ($pub -match '(\d+\.\d+\.\d+\.\d+)') { $hubIp = $Matches[1] }
}
if (-not $hubIp) {
    throw "Tailscale IP not found. Install Tailscale, log in, or set FLEET_PUBLIC_URL in .env"
}

$studioPort = if ($EnvMap["WEB_PORT"]) { $EnvMap["WEB_PORT"] } else { "8765" }
$hubUrl = "http://${hubIp}:${studioPort}"
$nodeName = if ($EnvMap["FLEET_NODE_NAME"]) { $EnvMap["FLEET_NODE_NAME"] } else { $env:COMPUTERNAME }

$manifest = @{
    version        = 1
    updated_at     = (Get-Date).ToUniversalTime().ToString("o")
    hub            = @{
        computer_name = $env:COMPUTERNAME
        node_name     = $nodeName
        tailscale_ip  = $hubIp
        studio_url    = $hubUrl
    }
    worker_defaults = @{
        studio_port         = [int]$studioPort
        web_host            = "0.0.0.0"
        asr_backend         = "whisper"
        fleet_role          = "agent"
        fleet_montage_hub   = $false
        fleet_auto_pull     = $false
        telegram_enabled    = $false
    }
    bootstrap = @{
        worker_script = "scripts/bootstrap-fleet-worker.ps1"
        docs          = "fleet/README.md"
    }
}

$ManifestPath = Join-Path $Root "fleet\manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
Write-Host "Wrote $ManifestPath" -ForegroundColor Green
Write-Host "  Hub mesh URL: $hubUrl" -ForegroundColor Cyan

$token = $EnvMap["FLEET_AGENT_TOKEN"]
$webUser = if ($EnvMap["WEB_AUTH_USER"]) { $EnvMap["WEB_AUTH_USER"] } else { "admin" }
$webPass = $EnvMap["WEB_AUTH_PASSWORD"]

if (-not $token -and (Test-Path $CredsPath)) {
    foreach ($line in Get-Content $CredsPath -Encoding UTF8) {
        if ($line -match '^FLEET_AGENT_TOKEN=(.+)$') { $token = $Matches[1].Trim() }
        if ($line -match '^WEB_AUTH_PASSWORD=(.+)$') { $webPass = $Matches[1].Trim() }
    }
}

$SecretsPath = Join-Path $Root "fleet\secrets.env"
$existingSecrets = Read-DotEnv -Path $SecretsPath
$tsKey = $existingSecrets["TAILSCALE_AUTH_KEY"]

$secretValues = @{
    FLEET_AGENT_TOKEN   = $(if ($token) { $token } else { "change-me-long-secret" })
    WEB_AUTH_USER       = $webUser
    WEB_AUTH_PASSWORD   = $(if ($webPass) { $webPass } else { "change-me" })
    TAILSCALE_AUTH_KEY  = $(if ($tsKey) { $tsKey } else { "" })
}
Write-DotEnv -Path $SecretsPath -Values $secretValues
Write-Host "Wrote $SecretsPath" -ForegroundColor Green

if (-not $tsKey) {
    Write-Host ""
    Write-Host "Add Tailscale auth key for headless worker setup:" -ForegroundColor Yellow
    Write-Host "  1) https://login.tailscale.com/admin/settings/keys" -ForegroundColor Yellow
    Write-Host "  2) Edit fleet/secrets.env -> TAILSCALE_AUTH_KEY=tskey-auth-..." -ForegroundColor Yellow
    Write-Host "  3) Re-run with -PushSecrets (private repo) or copy file to worker USB" -ForegroundColor Yellow
}

if ($Push -or $PushSecrets) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git not found"
    }
    git add "fleet/manifest.json"
    if ($PushSecrets) {
        Write-Host "Pushing fleet/secrets.env — only for PRIVATE repositories!" -ForegroundColor Red
        git add "fleet/secrets.env"
    }
    $msg = "Update fleet manifest for worker bootstrap"
    git commit -m $msg 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Nothing to commit or commit failed (maybe no changes)." -ForegroundColor Yellow
    } else {
        if ($Branch) {
            git push origin "HEAD:$Branch"
        } else {
            git push
        }
        Write-Host "Pushed to origin." -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Worker (second PC):" -ForegroundColor Cyan
Write-Host "  git pull" -ForegroundColor White
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-fleet-worker.ps1 -StartStudio" -ForegroundColor White
