# Остановить бэкенд Studio (порт 8765 + python + окна run-backend)
# Launcher вызывает без паузы. Вручную: .\stop-backend.cmd

param(
    [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = (Get-Location).Path
}

function Write-StopMsg([string]$Text, [string]$Color = "Gray") {
    if ($Quiet) { return }
    Write-Host $Text -ForegroundColor $Color
}

Write-StopMsg "stop-backend: $Root" "Cyan"

try {
    $conns = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop)
    foreach ($c in $conns) {
        if ($c.OwningProcess -gt 0) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-StopMsg "  stopped port 8765 PID $($c.OwningProcess)" "Green"
        }
    }
} catch {
    Write-StopMsg "  port 8765 not listening" "Gray"
}

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and (
            ($_.CommandLine -like "*$Root*") -or
            ($_.CommandLine -like "*app.main*")
        )
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-StopMsg "  stopped python PID $($_.ProcessId)" "Green"
    }

Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        ($_.CommandLine -like "*run-backend.ps1*") -and
        ($_.CommandLine -like "*$Root*" -or $_.CommandLine -like "*video-pipeline*")
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-StopMsg "  stopped backend shell PID $($_.ProcessId)" "Green"
    }

Start-Sleep -Milliseconds 800
Write-StopMsg "stop-backend: done" "Green"
