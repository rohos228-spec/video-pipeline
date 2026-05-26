# Скан кнопки Generate на outsee (нужен Chrome CDP :29229 + ты залогинен в outsee).
cd "C:\Users\Love Space\video-pipeline"
Write-Host "1) Открой Chrome с CDP (обычно уже из Studio/launcher)"
Write-Host "2) Вручную зайди на outsee.io/video и залогинься"
Write-Host "3) Сканирую кнопки Generate..."
.\.venv\Scripts\python.exe -m app.bots.outsee recon-generate video
Write-Host ""
Write-Host "Файлы в data\outsee_dumps\ — пришли .json и .png в чат агенту"
pause
