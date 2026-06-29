@echo off
cd /d "%~dp0"
set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo === MONTAGE #16 pshologiya-pochemu-ya-1-video ===
echo Параллельно с Outsee — Studio не трогать.
echo.
"%PY%" scripts\assemble_r15_direct.py 16
if errorlevel 1 (
  echo FAIL — см. вывод выше
  pause
  exit /b 1
)
if exist "%PY%" "%PY%" scripts\verify_final_engine.py 16
echo.
echo OK — data\videos\pshologiya-pochemu-ya-1-video\final\pshologiya-pochemu-ya-1-video.mp4
pause
