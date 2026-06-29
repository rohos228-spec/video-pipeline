# Worker: Tailscale + .env + web build + optional Studio start (reads fleet/manifest.json + fleet/secrets.env).
param(
    [switch]$SkipTailscale,
    [switch]$SkipBuild,
    [switch]$SkipVenvCheck,
    [switch]$StartStudio,
    [switch]$ForceBuild
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

Write-Step "Fleet worker bootstrap"
Write-Host "Repo: $Root"

$ManifestPath = Join-Path $Root "fleet\manifest.json"
$SecretsPath = Join-Path $Root "fleet\secrets.env"
if (-not (Test-Path $ManifestPath)) {
    throw "Missing fleet/manifest.json — on hub run: scripts/export-fleet-manifest.ps1 -Push"
}

$manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$secrets = Read-DotEnv -Path $SecretsPath
if ($secrets.Count -eq 0) {
    Write-Host "Warning: fleet/secrets.env missing — copy from hub or use fleet/secrets.env.example" -ForegroundColor Yellow
}

if (-not $SkipTailscale) {
    Write-Step "Tailscale"
    if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
        & (Join-Path $PSScriptRoot "install-tailscale.ps1")
    }
    $status = & tailscale status 2>$null
    $connected = $LASTEXITCODE -eq 0 -and $status
    if (-not $connected) {
        $authKey = $secrets["TAILSCALE_AUTH_KEY"]
        if (-not $authKey) {
            throw @"
Tailscale not connected and no TAILSCALE_AUTH_KEY in fleet/secrets.env.
On hub: create key at https://login.tailscale.com/admin/settings/keys
Add to fleet/secrets.env, git pull on this PC (or copy file), re-run bootstrap.
"@
        }
        Write-Host "Joining tailnet with auth key..."
        & tailscale up --auth-key=$authKey --accept-routes
        if ($LASTEXITCODE -ne 0) { throw "tailscale up failed" }
    }
    $workerIp = Get-TailscaleIp4
    if ($workerIp) {
        Write-Host "Worker Tailscale IP: $workerIp" -ForegroundColor Green
    }
}

$hubUrl = $manifest.hub.studio_url
if (-not $hubUrl) {
    $hip = $manifest.hub.tailscale_ip
    $port = $manifest.worker_defaults.studio_port
    $hubUrl = "http://${hip}:${port}"
}

$workerIp = Get-TailscaleIp4
$port = [string]$manifest.worker_defaults.studio_port
$selfUrl = if ($workerIp) { "http://${workerIp}:${port}" } else { "http://127.0.0.1:${port}" }

Write-Step "Writing .env"
$EnvPath = Join-Path $Root ".env"
$text = ""
if (Test-Path $EnvPath) { $text = Get-Content $EnvPath -Raw -Encoding UTF8 }
if (-not $text) {
    $example = Join-Path $Root ".env.example"
    if (Test-Path $example) { $text = Get-Content $example -Raw -Encoding UTF8 }
}

$token = $secrets["FLEET_AGENT_TOKEN"]
if (-not $token) { throw "FLEET_AGENT_TOKEN missing in fleet/secrets.env" }

Set-EnvLine -Text ([ref]$text) -Key "WEB_HOST" -Value $manifest.worker_defaults.web_host
Set-EnvLine -Text ([ref]$text) -Key "WEB_PORT" -Value $port
Set-EnvLine -Text ([ref]$text) -Key "WEB_ENABLED" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "ASR_BACKEND" -Value $manifest.worker_defaults.asr_backend
Set-EnvLine -Text ([ref]$text) -Key "FLEET_ENABLED" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_ROLE" -Value "agent"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_MONTAGE_HUB" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_AUTO_PULL" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_IS_MAIN" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_HUB_URL" -Value $hubUrl
Set-EnvLine -Text ([ref]$text) -Key "FLEET_PUBLIC_URL" -Value $selfUrl
Set-EnvLine -Text ([ref]$text) -Key "FLEET_AGENT_TOKEN" -Value $token
Set-EnvLine -Text ([ref]$text) -Key "FLEET_NODE_NAME" -Value $env:COMPUTERNAME
Set-EnvLine -Text ([ref]$text) -Key "TELEGRAM_ENABLED" -Value "false"

if ($secrets["WEB_AUTH_USER"]) {
    Set-EnvLine -Text ([ref]$text) -Key "WEB_AUTH_USER" -Value $secrets["WEB_AUTH_USER"]
}
if ($secrets["WEB_AUTH_PASSWORD"]) {
    Set-EnvLine -Text ([ref]$text) -Key "WEB_AUTH_PASSWORD" -Value $secrets["WEB_AUTH_PASSWORD"]
}

Set-Content -LiteralPath $EnvPath -Value $text.TrimEnd() -Encoding UTF8
Write-Host "  Hub:  $hubUrl" -ForegroundColor Green
Write-Host "  Self: $selfUrl" -ForegroundColor Green

if (-not $SkipVenvCheck) {
    $PyExe = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $PyExe)) {
        Write-Step "Python venv missing — run install.ps1 first"
        $install = Join-Path $Root "install.ps1"
        if (Test-Path $install) {
            & powershell -ExecutionPolicy Bypass -File $install -NonInteractive
        } else {
            throw "No .venv — clone repo and run install.ps1"
        }
    }
}

$WebOut = Join-Path $Root "web\out\index.html"
if (-not $SkipBuild -and ($ForceBuild -or -not (Test-Path $WebOut))) {
    Write-Step "Building web UI"
    Push-Location (Join-Path $Root "web")
    & npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    Pop-Location
}

Write-Step "Bootstrap complete"
Write-Host "Agent '$env:COMPUTERNAME' -> hub $hubUrl" -ForegroundColor Cyan
Write-Host "Check hub tab Network / Set after StartStudio." -ForegroundColor Cyan

if ($StartStudio) {
    Write-Step "Starting Studio"
    & (Join-Path $Root "RUN-STUDIO.ps1")
}
