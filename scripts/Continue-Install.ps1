$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Run install.ps1 first"; exit 1 }
$spec = (Resolve-Path -LiteralPath $Root).Path
$env:PIP_DEFAULT_TIMEOUT = "300"
Write-Host "pip install (timeout 300s, 6 tries)..." -ForegroundColor Cyan
for ($i = 1; $i -le 6; $i++) {
    & $py -m pip install --default-timeout=300 -e $spec
    if ($LASTEXITCODE -eq 0) { Write-Host "OK"; exit 0 }
    Write-Host "retry $i in 30 sec..." -ForegroundColor Yellow
    Start-Sleep -Seconds 30
}
exit 1
