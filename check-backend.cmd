@echo off
setlocal
cd /d "%~dp0"
title CHECK BACKEND

echo.
echo Проверка backend Studio на :8765
echo.

powershell -NoProfile -Command "try { $h=Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 5; if($h.status -eq 'ok'){ Write-Host 'OK - backend работает' -ForegroundColor Green; exit 0 } else { Write-Host 'FAIL - странный ответ health' -ForegroundColor Red; exit 1 } } catch { Write-Host 'FAIL - backend не запущен' -ForegroundColor Red; Write-Host 'Запусти GO.cmd и открой http://127.0.0.1:8765' -ForegroundColor Yellow; exit 1 }"
set ERR=%ERRORLEVEL%
echo.
pause
exit /b %ERR%
