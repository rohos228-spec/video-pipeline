# Download + install PyTorch with correct CUDA tag for this GPU.
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\download-torch-cuda.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$PyExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) {
    throw "No .venv at $PyExe"
}

function Invoke-Pip {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PyExe -m pip @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        return [int]$code
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PyExe @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        return [int]$code
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

function Test-CudaKernels {
    $code = Invoke-Python @(
        "-c",
        @"
import torch
assert torch.cuda.is_available(), 'cuda not available'
x = torch.zeros(1, device='cuda')
y = x + 1
torch.cuda.synchronize()
print('OK', torch.__version__, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))
"@
    )
    return ($code -eq 0)
}

Write-Host "=== nvidia-smi ===" -ForegroundColor Cyan
& nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv
if ($LASTEXITCODE -ne 0) { throw "nvidia-smi failed" }

$pickScript = Join-Path $Root "scripts\pick-torch-cuda.ps1"
$tag = (& powershell -NoProfile -ExecutionPolicy Bypass -File $pickScript | Select-Object -First 1).Trim()
if ($tag -eq "cu128") {
    $torchVer = "2.7.1"
    $wheelDir = Join-Path $Root "data\wheels\torch-cu128"
} else {
    $torchVer = "2.6.0"
    $wheelDir = Join-Path $Root "data\wheels\torch-cu124"
}

New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null

$torchName = "torch-${torchVer}+${tag}-cp311-cp311-win_amd64.whl"
$audioName = "torchaudio-${torchVer}+${tag}-cp311-cp311-win_amd64.whl"
$torchPath = Join-Path $wheelDir $torchName
$audioPath = Join-Path $wheelDir $audioName

$torchUrl = "https://download.pytorch.org/whl/$tag/torch-${torchVer}%2B${tag}-cp311-cp311-win_amd64.whl"
$audioUrl = "https://download.pytorch.org/whl/$tag/torchaudio-${torchVer}%2B${tag}-cp311-cp311-win_amd64.whl"

Write-Host ""
Write-Host "Selected: $torchName" -ForegroundColor Green
Write-Host "Folder:   $wheelDir"
if ($tag -eq "cu128") {
    Write-Host "RTX 50xx / sm_12x needs cu128 (cu124 will NOT run kernels)." -ForegroundColor Yellow
}
Write-Host ""

if (Test-CudaKernels) {
    Write-Host "=== CUDA already OK ===" -ForegroundColor Green
    Write-Host "=== pin fsspec for NeMo ===" -ForegroundColor Cyan
    Invoke-Pip @("install", "fsspec==2024.12.0") | Out-Null
    Write-Host "OK. Next: INSTALL-NVIDIA-ASR.cmd" -ForegroundColor Green
    exit 0
}

function Get-FileSizeMB([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    return [math]::Round((Get-Item -LiteralPath $Path).Length / 1MB, 1)
}

function Download-Wheel {
    param(
        [string]$Url,
        [string]$OutPath,
        [string]$Label,
        [double]$MinSizeMB
    )
    $sizeMb = Get-FileSizeMB $OutPath
    if ($sizeMb -gt 0 -and $sizeMb -lt $MinSizeMB) {
        Write-Host "  removing broken partial ($sizeMb MB): $OutPath" -ForegroundColor Yellow
        Remove-Item -LiteralPath $OutPath -Force
    }
    if ((Get-FileSizeMB $OutPath) -ge $MinSizeMB) {
        Write-Host "  already OK ($sizeMb MB): $(Split-Path -Leaf $OutPath)" -ForegroundColor DarkGray
        return
    }
    Write-Host "  downloading $Label ..." -ForegroundColor Cyan
    Write-Host "  $Url" -ForegroundColor DarkGray
    curl.exe -L --connect-timeout 60 --max-time 0 -o $OutPath $Url
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed for $Label (exit $LASTEXITCODE). VPN on and retry."
    }
    $sizeMb = Get-FileSizeMB $OutPath
    if ($sizeMb -lt $MinSizeMB) {
        Remove-Item -LiteralPath $OutPath -Force -ErrorAction SilentlyContinue
        throw "Download too small ($sizeMb MB) - bad URL or CDN block. Expected >= $MinSizeMB MB."
    }
    Write-Host "  saved $sizeMb MB" -ForegroundColor Green
}

Write-Host "=== uninstall old torch ===" -ForegroundColor Cyan
Invoke-Pip @("uninstall", "-y", "torch", "torchaudio") | Out-Null

Download-Wheel -Url $torchUrl -OutPath $torchPath -Label "torch" -MinSizeMB 2000
Download-Wheel -Url $audioUrl -OutPath $audioPath -Label "torchaudio" -MinSizeMB 1

Write-Host "=== pip install torch wheel ===" -ForegroundColor Cyan
if ((Invoke-Pip @("install", "--force-reinstall", $torchPath)) -ne 0) {
    throw "pip install torch failed (see output above)"
}

Write-Host "=== pip install torchaudio wheel ===" -ForegroundColor Cyan
if ((Invoke-Pip @("install", "--force-reinstall", "--no-deps", $audioPath)) -ne 0) {
    throw "pip install torchaudio failed (see output above)"
}

Write-Host "=== pin fsspec for NeMo ===" -ForegroundColor Cyan
Invoke-Pip @("install", "fsspec==2024.12.0") | Out-Null

Write-Host "=== GPU kernel test ===" -ForegroundColor Cyan
if (-not (Test-CudaKernels)) {
    throw "CUDA kernel test failed (see output above)"
}

Write-Host ""
Write-Host "OK. Next: INSTALL-NVIDIA-ASR.cmd" -ForegroundColor Green
