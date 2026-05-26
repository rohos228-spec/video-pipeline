# Retry pip install after network error (IncompleteRead)
$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot | Split-Path -Parent
if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
    Write-Host 'ERROR: run from video-pipeline root (CONTINUE-INSTALL.cmd)' -ForegroundColor Red
    exit 1
}
Set-Location -LiteralPath $Root
$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'ERROR: no .venv — run install.ps1 first' -ForegroundColor Red
    exit 1
}
$spec = (Resolve-Path -LiteralPath $Root).Path
Write-Host "Repo: $Root" -ForegroundColor Cyan
for ($i = 1; $i -le 6; $i++) {
    Write-Host "pip install -e (attempt $i/6)..." -ForegroundColor Yellow
    & $py -m pip install -e $spec
    if ($LASTEXITCODE -eq 0) {
        Write-Host '[OK] Dependencies installed' -ForegroundColor Green
        Write-Host 'Next: .\UPDATE-STUDIO.cmd' -ForegroundColor Green
        exit 0
    }
    Write-Host 'Failed — retry in 20 sec (check internet/VPN)' -ForegroundColor DarkYellow
    Start-Sleep -Seconds 20
}
Write-Host '[FAIL] After 6 attempts' -ForegroundColor Red
exit 1
