# NVIDIA NeMo RU ASR (montage PC, CUDA)
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\install-nvidia-asr.ps1

param(
    [switch]$SkipNeMo,
    [switch]$TorchOnly
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$PyExe = Join-Path $Root ".venv\Scripts\python.exe"

function Invoke-Pip {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PyExe -m pip @Arguments
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

function Get-PipProxyArgs {
    foreach ($key in @("PIP_PROXY", "HTTPS_PROXY", "HTTP_PROXY")) {
        $val = [Environment]::GetEnvironmentVariable($key)
        if ($val) { return @("--proxy", $val) }
    }
    return @()
}

function Install-CudaTorch {
    $timeout = if ($env:PIP_DEFAULT_TIMEOUT) { $env:PIP_DEFAULT_TIMEOUT } else { "900" }
    $proxy = Get-PipProxyArgs
    $pipCommon = @(
        "install",
        "--default-timeout=$timeout",
        "--retries", "15"
    ) + $proxy

    Invoke-Pip @("uninstall", "-y", "torch", "torchaudio") | Out-Null

    $indexes = @(
        "https://download.pytorch.org/whl/cu128",
        "https://download.pytorch.org/whl/cu124",
        "https://download.pytorch.org/whl/cu121"
    )
    $pickScript = Join-Path $Root "scripts\pick-torch-cuda.ps1"
    if (Test-Path -LiteralPath $pickScript) {
        $tag = (& powershell -NoProfile -ExecutionPolicy Bypass -File $pickScript 2>$null | Select-Object -First 1)
        if ($tag -eq "cu128") {
            $indexes = @(
                "https://download.pytorch.org/whl/cu128",
                "https://download.pytorch.org/whl/cu124"
            )
            Write-Host "    GPU needs cu128 (RTX 50xx / sm_12x)" -ForegroundColor Yellow
        }
    }

    foreach ($index in $indexes) {
        Write-Host "==> pip install torch torchaudio from $index ..." -ForegroundColor Cyan
        $code = Invoke-Pip ($pipCommon + @("torch", "torchaudio", "--index-url", $index))
        if ($code -ne 0) {
            Write-Host "    failed ($index)" -ForegroundColor Yellow
            continue
        }
        $flag = & $PyExe -c "import torch; print(1 if torch.cuda.is_available() else 0)" 2>$null
        if ($flag -eq "1") { return $true }
        Write-Host "    installed but cuda=False, next index ..." -ForegroundColor Yellow
    }
    return $false
}

Write-Host "==> nvidia-smi ..." -ForegroundColor Cyan
& nvidia-smi
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: nvidia-smi" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $PyExe)) {
    Write-Host "==> no .venv, run install.ps1 ..." -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "install.ps1") -NonInteractive
}
if (-not (Test-Path -LiteralPath $PyExe)) {
    throw "No .venv at $PyExe"
}

Write-Host "==> pip upgrade ..." -ForegroundColor Cyan
$code = Invoke-Pip @("install", "--upgrade", "pip")
if ($code -ne 0) { throw "pip upgrade failed" }

if (-not (Install-CudaTorch)) {
    Write-Host "FAIL: CUDA torch not installed (CDN timeout?)." -ForegroundColor Red
    Write-Host 'Retry: $env:PIP_PROXY="http://user:pass@host:port"' -ForegroundColor Yellow
    exit 1
}

$info = & $PyExe -c "import torch; print(torch.__version__); print(torch.cuda.get_device_name(0))" 2>$null
Write-Host "    CUDA OK: $info" -ForegroundColor Green

if ($TorchOnly) { exit 0 }

Write-Host "==> NeMo ASR (large download) ..." -ForegroundColor Cyan
$timeout = if ($env:PIP_DEFAULT_TIMEOUT) { $env:PIP_DEFAULT_TIMEOUT } else { "900" }
$proxy = Get-PipProxyArgs
$code = Invoke-Pip (@(
    "install",
    "--default-timeout=$timeout",
    "--retries", "15"
) + $proxy + @("-e", ".[nvidia-asr]"))
if ($code -ne 0) { throw "nvidia-asr install failed" }

if ($SkipNeMo) { exit 0 }

Write-Host "==> preload NeMo RU ASR ($env:NVIDIA_ASR_MODEL) ..." -ForegroundColor Cyan
$env:ASR_BACKEND = "nvidia"
if (-not $env:NVIDIA_ASR_MODEL) {
    $env:NVIDIA_ASR_MODEL = "nvidia/stt_ru_fastconformer_hybrid_large_pc"
}
$code = & $PyExe (Join-Path $Root "scripts\download_whisper.py") 2>&1
if ($LASTEXITCODE -ne 0) { throw "model preload failed: $code" }

Write-Host "OK. Run: .\GO-MONTAGE-15.cmd" -ForegroundColor Green
