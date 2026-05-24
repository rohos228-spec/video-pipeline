@echo off
REM Check backend + web UI, open browser
cd /d "%~dp0"
set "OK=1"

echo === Video Pipeline web check ===
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [FAIL] .venv not found - run VideoPipelineStudio.cmd, button 1
    set "OK=0"
)

if not exist "web\out\index.html" (
    echo [FAIL] web\out\index.html missing - run button 6 Build Web UI
    echo        Or: cd web ^& npm install ^& npm run build
    set "OK=0"
) else (
    echo [OK] web\out\index.html exists
)

powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 -UseBasicParsing; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if errorlevel 1 (
    echo [FAIL] Backend not running on http://127.0.0.1:8765
    echo        Run VideoPipelineStudio.cmd - button 2 Start Studio
    set "OK=0"
) else (
    echo [OK] Backend responds on :8765
)

echo.
if "%OK%"=="0" (
    echo Fix issues above, then open http://127.0.0.1:8765
    pause
    exit /b 1
)

echo Opening http://127.0.0.1:8765 ...
start http://127.0.0.1:8765
pause
