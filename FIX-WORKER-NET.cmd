@echo off
cd /d "%~dp0"
title Fix worker network (FRONTENDGMK / fleet agent)
echo === Worker: Studio network for fleet ===
echo Run this ON the worker PC (not hub).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-second-pc.ps1" -SkipTailscaleLogin -SkipGit
echo.
echo === Firewall (Admin) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\allow-studio-firewall.ps1"
echo.
echo === Local health ===
powershell -NoProfile -Command "try { (Invoke-RestMethod http://127.0.0.1:8765/api/health -TimeoutSec 5) | ConvertTo-Json } catch { Write-Host FAIL: $_ -ForegroundColor Red }"
echo.
echo === Listen address ===
netstat -an | findstr ":8765"
echo.
echo If not LISTENING on 0.0.0.0:8765 - restart Studio:
echo   powershell -ExecutionPolicy Bypass -File .\apply-local.ps1
pause
