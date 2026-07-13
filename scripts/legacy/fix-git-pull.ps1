# Докачать git после ошибки Unlink pack (Windows: закрой Studio / Cursor в этой папке).
# powershell -ExecutionPolicy Bypass -File .\fix-git-pull.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Stop python in this repo (if any)" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$PSScriptRoot*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 2

Write-Host "==> git checkout + pull" -ForegroundColor Cyan
git checkout devin/windows-installer
if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }

git pull origin devin/windows-installer
if ($LASTEXITCODE -ne 0) {
    Write-Host "Retry after git gc..." -ForegroundColor Yellow
    git gc --prune=now
    git pull origin devin/windows-installer
}
if ($LASTEXITCODE -ne 0) { throw "git pull failed — close Cursor/Studio and retry" }

Write-Host "==> pip install" -ForegroundColor Cyan
$root = (Get-Location).Path
$spec = (Resolve-Path -LiteralPath $root).Path + '[dev]'
& ".\.venv\Scripts\python.exe" -m pip install -e $spec

Write-Host "==> npm build" -ForegroundColor Cyan
Push-Location web
npm install
npm run build
Pop-Location

Write-Host ""
Write-Host "OK commit: $(git rev-parse --short HEAD)" -ForegroundColor Green
Write-Host "Next: Launcher 4 Stop -> 2 Start Studio" -ForegroundColor Green
Write-Host "Check: Invoke-RestMethod http://127.0.0.1:8765/api/studio-version" -ForegroundColor Gray
