@echo off
cd /d "%~dp0"
echo === Backend check ===
echo Folder: %CD%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [FAIL] .venv missing - Launcher button 1 Full install
    goto end
)
echo [OK] .venv

if not exist "web\out\index.html" (
    echo [WARN] web\out\index.html missing - button 6 Build Web UI
) else (
    echo [OK] web UI built
)

findstr /C:"Copy-LauncherLogs" "installer\VideoPipelineLauncher.ps1" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Old launcher - run: update-launcher.cmd or git pull
) else (
    echo [OK] Launcher has Copy logs feature
)

echo.
echo Git:
git rev-parse --short HEAD 2>nul
echo.

powershell -NoProfile -Command "try { $r = Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 3; Write-Host '[OK] Backend on :8765' $r.status } catch { Write-Host '[FAIL] Backend NOT running on :8765'; Write-Host '       Start: start-backend.cmd or Launcher 2 Start Studio'; Write-Host '       Log: data\backend.log' }"

echo.
netstat -ano | findstr ":8765" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
    echo Port 8765: not listening
) else (
    echo Port 8765: listening
)

:end
echo.
echo Run diagnose: powershell -ExecutionPolicy Bypass -File diagnose-backend.ps1
echo.
pause
