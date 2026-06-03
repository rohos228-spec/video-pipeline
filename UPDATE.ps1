# Обновление + опционально запуск в ЭТОМ ЖЕ окне
# powershell -ExecutionPolicy Bypass -File "C:\Users\Love Space\video-pipeline\UPDATE.ps1" -SkipPull -Run

[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipPull,
    [switch]$Run
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-OK($msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: pyproject.toml ne naiden" -ForegroundColor Red
    exit 1
}

if (-not $SkipPull) {
    Write-Step "git pull origin main"
    git fetch origin main
    git pull --ff-only origin main
    Write-OK "kod obnovlen"
} else {
    Write-Host "==> git skip (-SkipPull)" -ForegroundColor Yellow
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "ERROR: no .venv - run install.ps1" -ForegroundColor Red
    exit 1
}

Write-Step "pip install (editable)"
$pipSpec = $Root + '[dev,whisper]'
& $venvPython -m pip install -e $pipSpec -q
Write-OK "python deps"

Get-ChildItem -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

if (-not $SkipWeb) {
    if (Test-Path "web\package.json") {
        Write-Step "STUDIO_VERSION +1"
        & $venvPython (Join-Path $Root "scripts\bump_studio_version.py") --no-build
        Write-OK "versiya obnovlena"

        Write-Step "npm run build (web, 1-3 min)"
        $webDir = Join-Path $Root "web"
        Push-Location -LiteralPath $webDir
        try {
            if (-not (Test-Path "node_modules")) {
                Write-Host "    npm install (pervy raz)..." -ForegroundColor Gray
                npm install
                if ($LASTEXITCODE -ne 0) { throw "npm install exit $LASTEXITCODE" }
            }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build exit $LASTEXITCODE" }
            if (-not (Test-Path "out\index.html")) { throw "net web/out/index.html posle build" }
        } finally {
            Pop-Location
        }
        Write-OK "web sobran"
        $verFile = Join-Path $Root "web\STUDIO_VERSION"
        if (Test-Path $verFile) {
            $build = (Get-Content $verFile -TotalCount 1).Trim()
            $sha = (Get-Content $verFile | Select-Object -Skip 1 -First 1).Trim()
            Write-Host "    Studio v$build $sha" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Gotovo." -ForegroundColor Green

if ($Run) {
    & (Join-Path $Root "RUN-STUDIO.ps1")
    exit $LASTEXITCODE
}

Write-Host "Zapusk (odno okno):" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Root\RUN-STUDIO.ps1`"" -ForegroundColor Yellow
