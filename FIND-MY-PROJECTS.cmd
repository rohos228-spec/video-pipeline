@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
echo.
echo === Где лежат проекты (файл state.db) ===
echo.
echo Эта копия программы:
if exist "data\state.db" (
    echo   %CD%\data\state.db
    dir "data\state.db"
    if exist ".venv\Scripts\python.exe" (
        echo   Projects in this DB:
        .venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/state.db'); print('  count=', c.execute('select count(*) from projects').fetchone()[0])"
    )
) else (
    echo   %CD%\data\state.db  -- НЕТ (пустая установка)
)
echo.
echo Поиск других state.db на диске (профиль пользователя)...
echo.
powershell -NoProfile -Command "Get-ChildItem -LiteralPath $env:USERPROFILE -Filter state.db -Recurse -Force -ErrorAction SilentlyContinue | Select-Object FullName, @{N='KB';E={[math]::Round($_.Length/1KB,1)}}, LastWriteTime | Format-Table -AutoSize"
echo.
echo Если нашёл старый state.db не в этой папке - скопируй сюда в data\state.db
echo (см. RESTORE-PROJECTS.cmd). Не работай из второй копии на диске.
echo.
echo Чтобы вернуть проекты: RESTORE-PROJECTS.cmd
echo.
pause
