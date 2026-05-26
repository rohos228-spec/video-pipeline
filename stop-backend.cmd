@echo off
REM Stop backend on port 8765 and project python processes
cd /d "%~dp0"
echo Stopping backend on port 8765 ...
powershell.exe -ExecutionPolicy Bypass -NoProfile -Command ^
  "try { Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop | ForEach-Object { if ($_.OwningProcess -gt 0) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host ('Stopped PID ' + $_.OwningProcess) } } } catch { Write-Host 'Port 8765 not listening' }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*video-pipeline*' -or $_.CommandLine -like '*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('Stopped python ' + $_.ProcessId) }"
echo Done.
pause
