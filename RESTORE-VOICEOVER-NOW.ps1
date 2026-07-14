# Одна кнопка: корзина Windows -> voiceover.txt -> БД
# powershell -ExecutionPolicy Bypass -File RESTORE-VOICEOVER-NOW.ps1

$Root = $PSScriptRoot
powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\Recover-VoiceoverFromRecycleBin.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Корзина пуста / не найдено. Глубокий поиск по диску:" -ForegroundColor Yellow
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    & $py -m restore_original_voiceover --all-parents --scan
    & $py -m restore_original_voiceover --all-parents --force
}
