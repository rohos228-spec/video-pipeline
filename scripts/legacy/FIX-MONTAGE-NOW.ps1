# FIX montage: HF symlink + whisper + remount
# Run: powershell -ExecutionPolicy Bypass -File .\FIX-MONTAGE-NOW.ps1

param(
    [string]$Topic = "dozhd",
    [int]$ProjectId = 0,
    [switch]$SkipRemount,
    [switch]$SkipDownload
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

$Branch = "cursor/remount-video-fc98"
$Repo = "rohos228-spec/video-pipeline"
$BaseUrl = "https://raw.githubusercontent.com/$Repo/$Branch"

$HotfixFiles = @(
    "app/services/whisper.py",
    "app/settings.py",
    "app/orchestrator/steps/assemble.py",
    "app/orchestrator/steps/generate_audio.py",
    "app/services/mapper.py",
    "app/services/remount_video.py",
    "remount_video.py",
    "run-backend.ps1",
    "scripts/download_whisper.py"
)

function Ensure-EnvLine {
    param([string]$Path, [string]$Key, [string]$Value)
    $line = "$Key=$Value"
    if (-not (Test-Path $Path)) {
        Add-Content -Path $Path -Value $line -Encoding ASCII
        return
    }
    $raw = Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction SilentlyContinue
    $found = $false
    $out = @()
    foreach ($r in $raw) {
        if ($r -match "^\s*$([regex]::Escape($Key))\s*=") {
            $found = $true
            $out += $line
        } else {
            $out += $r
        }
    }
    if (-not $found) { $out += $line }
    Set-Content -LiteralPath $Path -Value $out -Encoding UTF8
}

Write-Host ""
Write-Host "=== FIX MONTAGE ===" -ForegroundColor Cyan
Write-Host "Repo: $Root" -ForegroundColor DarkGray
Write-Host ""

$env:HF_HUB_DISABLE_SYMLINKS = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "0"
$env:SUBTITLE_REWHISPER_ON_ASSEMBLE = "false"

$envPath = Join-Path $Root ".env"
Ensure-EnvLine -Path $envPath -Key "HF_HUB_DISABLE_SYMLINKS" -Value "1"
Ensure-EnvLine -Path $envPath -Key "SUBTITLE_REWHISPER_ON_ASSEMBLE" -Value "false"
Write-Host "[OK] .env updated" -ForegroundColor Green

if (-not $SkipDownload) {
    Write-Host ""
    Write-Host "Downloading hotfix from $Branch ..." -ForegroundColor Cyan
    $ok = 0
    foreach ($rel in $HotfixFiles) {
        $dest = Join-Path $Root ($rel -replace "/", "\")
        $parent = Split-Path -Parent $dest
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        $url = "$BaseUrl/$rel"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
            Write-Host "  + $rel" -ForegroundColor Gray
            $ok++
        } catch {
            Write-Host "  ! $rel" -ForegroundColor Yellow
        }
    }
    Write-Host "Downloaded: $ok files" -ForegroundColor Green
}

$hfBroken = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--Systran--faster-whisper-large-v3"
if (Test-Path $hfBroken) {
    Write-Host ""
    Write-Host "Clearing broken HF cache ..." -ForegroundColor Yellow
    Remove-Item -LiteralPath $hfBroken -Recurse -Force -ErrorAction SilentlyContinue
}

Get-ChildItem -Path (Join-Path $Root "app") -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    Write-Host ""
    Write-Host "Stopping backend ..." -ForegroundColor Cyan
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet
    Start-Sleep -Seconds 2
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: .venv missing. Run install.ps1" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Preloading whisper model (1-3 min) ..." -ForegroundColor Cyan
& $py scripts\download_whisper.py medium 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "Starting backend in new window ..." -ForegroundColor Cyan
$backendPs1 = Join-Path $Root "run-backend.ps1"
Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $backendPs1 -WorkingDirectory $Root

Write-Host "Waiting for http://127.0.0.1:8765/api/health ..." -ForegroundColor Gray
$ready = $false
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if ($ready) {
    Write-Host "[OK] Backend is up" -ForegroundColor Green
} else {
    Write-Host "[!] Backend still starting" -ForegroundColor Yellow
}

if ($SkipRemount) {
    Write-Host "Done. Open http://127.0.0.1:8765" -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "Running remount ..." -ForegroundColor Cyan
$remountArgs = @("-m", "remount_video")
if ($ProjectId -gt 0) {
    $remountArgs += "$ProjectId"
} else {
    $remountArgs += "--topic"
    if ($Topic -eq "dozhd") {
        $remountArgs += ([char]0x0434 + [char]0x043E + [char]0x0436 + [char]0x0434)
    } else {
        $remountArgs += $Topic
    }
}
& $py @remountArgs
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "DONE. Check run-backend window for ffmpeg / assembled" -ForegroundColor Green
} else {
    Write-Host "Remount exit code: $code" -ForegroundColor Yellow
}
exit $code
