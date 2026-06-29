# =============================================================================
# SECOND PC (worker): Tailscale + git + Studio agent
# Run in PowerShell from repo folder (after git clone).
#
#   cd C:\Users\AiCreator\Desktop\video-pipeline
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup-second-pc.ps1
#
# Admin once for firewall (optional separate step):
#   powershell -ExecutionPolicy Bypass -File .\scripts\allow-studio-firewall.ps1
# =============================================================================

param(
    [string]$HubUrl = "http://100.72.202.35:8765",
    [string]$Token = "vpHub_k7Nx9mQ2pL5wR8tY4zA1bC6dE3fG0hJ",
    [string]$Branch = "feature/fleet-montage-queue-v161",
    [string]$NodeName = "",
    [switch]$SkipTailscaleLogin,
    [switch]$SkipGit,
    [switch]$SkipBuild,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

if (-not $NodeName) {
    $NodeName = $env:COMPUTERNAME
}

function Write-Step([string]$Msg) {
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor Cyan
}

function Invoke-GitQuiet {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & git -C $Root @GitArgs 2>&1 | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

Write-Host "============================================" -ForegroundColor Green
Write-Host " video-pipeline worker setup" -ForegroundColor Green
Write-Host " PC:     $NodeName" -ForegroundColor White
Write-Host " Hub:    $HubUrl" -ForegroundColor White
Write-Host " Branch: $Branch" -ForegroundColor White
Write-Host "============================================" -ForegroundColor Green

# --- 1 Tailscale ---
Write-Step "Tailscale install"
& (Join-Path $PSScriptRoot "install-tailscale.ps1")

if (-not $SkipTailscaleLogin) {
    $ip = Get-TailscaleIp4
    if (-not $ip) {
        Write-Host "Log in to Tailscale (SAME account as hub)..." -ForegroundColor Yellow
        Write-Host "  Start menu -> Tailscale -> Log in" -ForegroundColor Yellow
        $null = Invoke-Tailscale up
        Start-Sleep -Seconds 3
        $ip = Get-TailscaleIp4
    }
    if (-not $ip) {
        Write-Host ""
        Write-Host "Tailscale IP not ready yet." -ForegroundColor Red
        Write-Host "Finish login in Tailscale app, then run again:" -ForegroundColor Yellow
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\setup-second-pc.ps1 -SkipTailscaleLogin" -ForegroundColor White
        exit 1
    }
    Write-Host "Tailscale IP: $ip" -ForegroundColor Green
    $status = & (Get-TailscaleExe) status 2>&1
    Write-Host $status
}

# --- 2 Git ---
if (-not $SkipGit) {
    Write-Step "Git sync $Branch"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git not installed. Install Git for Windows first."
    }
    $fc = Invoke-GitQuiet @("fetch", "origin", $Branch)
    if ($fc -ne 0) { throw "git fetch failed. Is branch on GitHub?" }
    $co = Invoke-GitQuiet @("checkout", "-B", $Branch, "origin/$Branch")
    if ($co -ne 0) {
        Invoke-GitQuiet @("checkout", "-B", $Branch) | Out-Null
        Invoke-GitQuiet @("reset", "--hard", "origin/$Branch") | Out-Null
    }
    $head = (git -C $Root rev-parse --short HEAD).Trim()
    Write-Host "Git HEAD: $head" -ForegroundColor Green
}

# --- 3 Python venv ---
Write-Step "Python environment"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $install = Join-Path $Root "install.ps1"
    if (-not (Test-Path $install)) {
        throw "No .venv and no install.ps1"
    }
    & powershell -ExecutionPolicy Bypass -File $install -NonInteractive
    if (-not (Test-Path $Py)) { throw "install.ps1 did not create .venv" }
}
Write-Host "Python OK" -ForegroundColor Green

# --- 4 Web build ---
if (-not $SkipBuild) {
    Write-Step "npm build"
    Push-Location (Join-Path $Root "web")
    & npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    Pop-Location
    Write-Host "web/out OK" -ForegroundColor Green
}

# --- 5 .env worker ---
Write-Step "Configure .env (agent)"
$workerIp = Get-TailscaleIp4
$selfUrl = if ($workerIp) { "http://${workerIp}:8765" } else { "http://127.0.0.1:8765" }

$EnvPath = Join-Path $Root ".env"
$text = if (Test-Path $EnvPath) { Get-Content $EnvPath -Raw -Encoding UTF8 } else { "" }
Set-EnvLine -Text ([ref]$text) -Key "WEB_HOST" -Value "0.0.0.0"
Set-EnvLine -Text ([ref]$text) -Key "WEB_PORT" -Value "8765"
Set-EnvLine -Text ([ref]$text) -Key "WEB_ENABLED" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "TELEGRAM_ENABLED" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "ASR_BACKEND" -Value "whisper"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_ENABLED" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_ROLE" -Value "agent"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_MONTAGE_HUB" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_AUTO_PULL" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_IS_MAIN" -Value "false"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_HUB_URL" -Value $HubUrl
Set-EnvLine -Text ([ref]$text) -Key "FLEET_PUBLIC_URL" -Value $selfUrl
Set-EnvLine -Text ([ref]$text) -Key "FLEET_AGENT_TOKEN" -Value $Token
Set-EnvLine -Text ([ref]$text) -Key "FLEET_NODE_NAME" -Value $NodeName
Set-EnvLine -Text ([ref]$text) -Key "WEB_AUTH_USER" -Value "admin"
Set-EnvLine -Text ([ref]$text) -Key "WEB_AUTH_PASSWORD" -Value "MontageHub2026"
Set-Content -LiteralPath $EnvPath -Value $text.TrimEnd() -Encoding UTF8
Write-Host "FLEET_HUB_URL=$HubUrl" -ForegroundColor Green
Write-Host "FLEET_PUBLIC_URL=$selfUrl" -ForegroundColor Green

# --- 6 Firewall (best effort) ---
$fw = Join-Path $Root "scripts\allow-studio-firewall.ps1"
if (Test-Path $fw) {
    Write-Step "Firewall 8765 (needs Admin - skip if fails)"
    try {
        & powershell -ExecutionPolicy Bypass -File $fw
    } catch {
        Write-Host "Run as Admin later: .\scripts\allow-studio-firewall.ps1" -ForegroundColor Yellow
    }
}

Write-Step "Done"
Write-Host "Test hub from this PC:" -ForegroundColor Cyan
Write-Host "  curl $HubUrl/api/health" -ForegroundColor White

if (-not $NoStart) {
    Write-Step "Starting Studio agent (keep window open)"
    & (Join-Path $Root "scripts\stop-backend.ps1") -Quiet
    Start-Sleep -Seconds 2
    & (Join-Path $Root "RUN-STUDIO.ps1")
}
