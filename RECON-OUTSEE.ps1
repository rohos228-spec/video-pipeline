# Скан кнопки Generate (сначала BROWSER-OUTSEE.cmd — окно Chrome + логин).
cd "C:\Users\Love Space\video-pipeline"
Write-Host "Нужно: BROWSER-OUTSEE.cmd уже запускал и ты вошёл в outsee"
Write-Host "Сканирую кнопки Generate..."
.\.venv\Scripts\python.exe -m app.bots.outsee recon-generate video
Write-Host ""
Write-Host "Файлы в data\outsee_dumps\ — пришли .json и .png в чат агенту"
pause
