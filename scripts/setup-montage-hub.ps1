# One-shot setup: montage hub + fleet + web build + optional CUDA torch
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\setup-montage-hub.ps1

param(
    [switch]$SkipBuild,
    [switch]$SkipCudaFix,
    [switch]$StartStudio
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

$PyExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) {
    throw "No .venv - run: powershell -ExecutionPolicy Bypass -File .\install.ps1 -NonInteractive"
}

# --- Tailscale IP ---
$TailscaleIp = $null
$TsCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if ($TsCmd) {
    try {
        $TailscaleIp = (& tailscale ip -4 2>$null | Select-Object -First 1).ToString().Trim()
    } catch { }
}
if (-not $TailscaleIp) {
    $TailscaleIp = "127.0.0.1"
    Write-Host "    Tailscale not found - using 127.0.0.1 (change FLEET_PUBLIC_URL in .env later)" -ForegroundColor Yellow
} else {
    Write-Host "    Tailscale IP: $TailscaleIp" -ForegroundColor Green
}

$FleetUrl = "http://${TailscaleIp}:8765"
$Token = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
$WebPass = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })

# --- Patch .env ---
Write-Step "Updating .env ..."
$EnvPath = Join-Path $Root ".env"
$EnvText = Get-Content -LiteralPath $EnvPath -Raw -ErrorAction SilentlyContinue
if (-not $EnvText) { $EnvText = "" }

function Set-EnvLine {
    param([string]$Key, [string]$Value)
    $script:EnvText = [regex]::Replace(
        $script:EnvText,
        "(?m)^$([regex]::Escape($Key))=.*$",
        "$Key=$Value"
    )
    if ($script:EnvText -notmatch "(?m)^$([regex]::Escape($Key))=") {
        $script:EnvText += "`n$Key=$Value"
    }
}

Set-EnvLine "ASR_BACKEND" "nvidia"
Set-EnvLine "NVIDIA_ASR_MODEL" "nvidia/stt_ru_fastconformer_hybrid_large_pc"
Set-EnvLine "WHISPER_MODEL" "large-v3"
Set-EnvLine "WHISPER_DEVICE" "cuda"
Set-EnvLine "WHISPER_COMPUTE_TYPE" "float16"
Set-EnvLine "FLEET_ENABLED" "true"
Set-EnvLine "FLEET_ROLE" "hub"
Set-EnvLine "FLEET_MONTAGE_HUB" "true"
Set-EnvLine "FLEET_AUTO_PULL" "true"
Set-EnvLine "FLEET_PUBLIC_URL" $FleetUrl
Set-EnvLine "FLEET_HUB_URL" $FleetUrl
Set-EnvLine "FLEET_AGENT_TOKEN" $Token
Set-EnvLine "FLEET_NODE_NAME" $env:COMPUTERNAME
Set-EnvLine "WEB_AUTH_USER" "admin"
Set-EnvLine "WEB_AUTH_PASSWORD" $WebPass
Set-EnvLine "WEB_HOST" "0.0.0.0"
Set-EnvLine "WEB_PORT" "8765"
Set-EnvLine "WEB_ENABLED" "true"

$EnvText = $EnvText.TrimEnd() + "`n"
Set-Content -LiteralPath $EnvPath -Value $EnvText -Encoding UTF8

$CredsPath = Join-Path $Root "data\fleet-hub-credentials.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $CredsPath) | Out-Null
@"
Fleet hub credentials (keep secret)
Generated: $(Get-Date -Format o)

Studio URL (local):  http://127.0.0.1:8765
Studio URL (mesh):   $FleetUrl

WEB_AUTH_USER=admin
WEB_AUTH_PASSWORD=$WebPass

FLEET_AGENT_TOKEN=$Token

Worker start:
  powershell -ExecutionPolicy Bypass -File .\scripts\start-fleet-agent.ps1 -HubUrl "$FleetUrl" -Token "$Token"
"@ | Set-Content -LiteralPath $CredsPath -Encoding UTF8

Write-Host "    Credentials saved: data\fleet-hub-credentials.txt" -ForegroundColor Green

# --- CUDA torch ---
if (-not $SkipCudaFix) {
    Write-Step "Checking PyTorch CUDA ..."
    $CudaCheck = & $PyExe -c "import torch; print(torch.cuda.is_available())" 2>&1
    if ($CudaCheck -ne "True") {
        Write-Host "    CUDA not available - installing torch with CUDA 12.4 ..." -ForegroundColor Yellow
        & $PyExe -m pip install --upgrade pip
        & $PyExe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
        $CudaCheck2 = & $PyExe -c "import torch; print(torch.cuda.is_available())" 2>&1
        Write-Host "    cuda after fix: $CudaCheck2"
    } else {
        Write-Host "    CUDA OK" -ForegroundColor Green
    }
}

# --- Web build ---
if (-not $SkipBuild) {
    Write-Step "Building web UI ..."
    $Npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "npm not found - install Node.js LTS"
    }
    Push-Location (Join-Path $Root "web")
    & npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    Pop-Location
}

Write-Step "Done."
Write-Host ""
Write-Host "  Open:  http://127.0.0.1:8765  (tab: Network / Set)" -ForegroundColor Green
Write-Host "  Login: admin / (see data\fleet-hub-credentials.txt)" -ForegroundColor Green
Write-Host ""

if ($StartStudio) {
    Write-Step "Starting Studio ..."
    & (Join-Path $Root "RUN-STUDIO.ps1")
}
