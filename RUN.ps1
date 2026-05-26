cd "C:\Users\Love Space\video-pipeline"
$env:TELEGRAM_ENABLED = "false"
Write-Host "DO NOT CLOSE - wait: Uvicorn running on http://127.0.0.1:8765"
.\.venv\Scripts\python.exe -m app.main
pause
