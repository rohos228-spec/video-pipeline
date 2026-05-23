# update-web.ps1 — обновление video-pipeline + веб-UI
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> git pull" -ForegroundColor Cyan
git fetch origin
git pull origin devin/windows-installer

Write-Host "==> pip" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install -e .

Write-Host "==> web npm" -ForegroundColor Cyan
Push-Location web
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
  pnpm install
} else {
  npm install
}
Pop-Location

Write-Host "Готово. Окно 1: .\start.ps1 | Окно 2: cd web; npm run dev" -ForegroundColor Green
