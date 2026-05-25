# update-web.ps1 — обновление video-pipeline + веб-UI
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> git pull (devin/windows-installer)" -ForegroundColor Cyan
git fetch origin
git checkout devin/windows-installer
git pull origin devin/windows-installer

Write-Host "==> pip" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"

Write-Host "==> web npm install + build" -ForegroundColor Cyan
Push-Location web
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
  pnpm install
  pnpm run build
} else {
  npm install
  npm run build
}
Pop-Location

Write-Host "Готово. Перезапустите Studio: Stop -> Start Studio. URL: http://127.0.0.1:8765" -ForegroundColor Green
