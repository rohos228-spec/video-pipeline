# Скан кнопки Generate (сначала BROWSER-OUTSEE.cmd — окно Chrome + логин).
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Repo
Write-Host "Нужно: Chrome с CDP (Start-Chrome.cmd) и вход в outsee"
Write-Host "Сканирую кнопки Generate..."
.\.venv\Scripts\python.exe -m app.bots.outsee recon-generate video
Write-Host ""
Write-Host "Файлы в data\outsee_dumps\ — пришли .json и .png в чат агенту"
pause
