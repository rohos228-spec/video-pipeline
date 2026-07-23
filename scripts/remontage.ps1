# Повторный монтаж после замены voice_full в audio/
# Usage:
#   .\scripts\remontage.ps1
#   .\scripts\remontage.ps1 -ProjectId 15
#   .\scripts\remontage.ps1 -ProjectId 15 -SkipAssemble

param(
    [int]$ProjectId = 15,
    [switch]$SkipAssemble
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$env:ASR_BACKEND = "nvidia"
$env:NVIDIA_ASR_MODEL = "nvidia/stt_ru_fastconformer_hybrid_large_pc"

Write-Host "=== remontage project #$ProjectId ===" -ForegroundColor Cyan

& $Py scripts\remontage_prep.py $ProjectId
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($SkipAssemble) {
    Write-Host "SkipAssemble: подготовка OK. Запусти: .\GO-MONTAGE-15.cmd" -ForegroundColor Yellow
    exit 0
}

Write-Host "`nCUDA check..." -ForegroundColor Cyan
& $Py -c "import torch; x=torch.zeros(1,device='cuda'); torch.cuda.synchronize(); print('CUDA OK:', torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: нужен torch+cu128 и драйвер NVIDIA" -ForegroundColor Red
    exit 1
}

Write-Host "`nAssemble (Excel R15 only)..." -ForegroundColor Cyan
& $Py scripts\assemble_r15_direct.py
exit $LASTEXITCODE
