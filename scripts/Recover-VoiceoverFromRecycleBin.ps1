# ВОССТАНОВЛЕНИЕ voiceover.txt ИЗ КОРЗИНЫ WINDOWS (все диски).
# Копирует $R* файлы напрямую в data\videos\<slug>\voiceover.txt
# (не полагается на InvokeVerb RESTORE).
#
# ОДНА КОМАНДА из корня репозитория:
#   powershell -ExecutionPolicy Bypass -File scripts\Recover-VoiceoverFromRecycleBin.ps1
#
param(
    [switch]$DryRun,
    [switch]$SkipDb,
    [string]$RepoRoot = "",
    [string]$DataDir = ""
)

$ErrorActionPreference = "Continue"

if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
if (-not $DataDir) {
    $DataDir = Join-Path $RepoRoot "data"
}
$VideosDir = Join-Path $DataDir "videos"
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

function Normalize-PathSlash([string]$p) {
    if (-not $p) { return "" }
    return ($p -replace "\\", "/")
}

function Get-SlugFromOriginalPath([string]$orig) {
    $norm = Normalize-PathSlash $orig
    if ($norm -match "videos/([^/]+)/voiceover\.txt$") { return $Matches[1] }
    if ($norm -match "videos/([^/]+)/script\.txt$")   { return $Matches[1] }
    return $null
}

function Read-RecycleInfoPath([string]$infoFile) {
    # Windows 10+ $I файл: UTF-16 путь с offset 0x1C
    try {
        $bytes = [System.IO.File]::ReadAllBytes($infoFile)
        if ($bytes.Length -lt 0x20) { return $null }
        $pathLen = [BitConverter]::ToUInt32($bytes, 0x18)
        if ($pathLen -le 0 -or $pathLen -gt 4096) { return $null }
        $start = 0x1C
        $end = $start + ($pathLen * 2)
        if ($end -gt $bytes.Length) { return $null }
        return [System.Text.Encoding]::Unicode.GetString($bytes, $start, $pathLen * 2)
    } catch {
        return $null
    }
}

function Get-RecycleBinEntries {
    $entries = @()
    $drives = [System.IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq "Fixed" -and $_.IsReady }

    foreach ($drive in $drives) {
        $rb = Join-Path $drive.RootDirectory.FullName '$Recycle.Bin'
        if (-not (Test-Path -LiteralPath $rb)) { continue }

        Get-ChildItem -LiteralPath $rb -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $sidDir = $_.FullName
            Get-ChildItem -LiteralPath $sidDir -Filter '$I*' -Force -ErrorAction SilentlyContinue | ForEach-Object {
                $infoPath = $_.FullName
                $orig = Read-RecycleInfoPath $infoPath
                if (-not $orig) { return }

                $norm = Normalize-PathSlash $orig
                if ($norm -notmatch "voiceover\.txt$" -and $norm -notmatch "script\.txt$") { return }
                if ($norm -notmatch "video-pipeline|/videos/|\\videos\\") { return }

                $rName = '$R' + $_.Name.Substring(2)
                $rPath = Join-Path $sidDir $rName
                if (-not (Test-Path -LiteralPath $rPath)) { return }

                $entries += [PSCustomObject]@{
                    OriginalPath = $orig
                    RecycleDataPath = $rPath
                    Slug = Get-SlugFromOriginalPath $orig
                    Size = (Get-Item -LiteralPath $rPath -Force).Length
                }
            }
        }
    }

    # Fallback: Shell COM (если $I парсер не нашёл)
    if ($entries.Count -eq 0) {
        try {
            $shell = New-Object -ComObject Shell.Application
            $bin = $shell.NameSpace(0xA)
            if ($bin) {
                foreach ($item in $bin.Items()) {
                    $orig = ""
                    try { $orig = [string]$item.ExtendedProperty("System.Recycle.DeletedFrom") } catch {}
                    if (-not $orig) { continue }
                    $norm = Normalize-PathSlash $orig
                    if ($norm -notmatch "voiceover\.txt$" -and $norm -notmatch "script\.txt$") { continue }
                    $slug = Get-SlugFromOriginalPath $orig
                    $entries += [PSCustomObject]@{
                        OriginalPath = $orig
                        RecycleDataPath = [string]$item.Path
                        Slug = $slug
                        Size = [int64]$item.Size
                    }
                }
            }
        } catch {}
    }

    return $entries
}

Write-Host ""
Write-Host "=== RECYCLE BIN -> voiceover.txt (parents) ===" -ForegroundColor Cyan
Write-Host "Repo:    $RepoRoot"
Write-Host "Videos:  $VideosDir"
Write-Host ""

$entries = Get-RecycleBinEntries | Where-Object { $_.Slug } | Sort-Object OriginalPath -Unique
Write-Host "Found in Recycle Bin: $($entries.Count) voiceover/script file(s)" -ForegroundColor Yellow

if ($entries.Count -eq 0) {
    Write-Host ""
    Write-Host "В корзине НЕТ voiceover.txt из video-pipeline." -ForegroundColor Red
    Write-Host "Python unlink() НЕ кладёт файлы в корзину — если удалил бэкенд," -ForegroundColor Red
    Write-Host "файл уничтожен навсегда (только old/ tmp_gpt/ xlsx/ кадры)." -ForegroundColor Red
    Write-Host ""
    Write-Host "Попробуйте глубокий поиск:" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python.exe -m restore_original_voiceover --all-parents --scan"
    exit 1
}

$restored = 0
foreach ($e in $entries) {
    $destDir = Join-Path $VideosDir $e.Slug
    $dest = Join-Path $destDir "voiceover.txt"
    Write-Host ""
    Write-Host "[#$($restored+1)] slug=$($e.Slug)" -ForegroundColor Green
    Write-Host "  from: $($e.OriginalPath)"
    Write-Host "  bin:  $($e.RecycleDataPath) ($($e.Size) bytes)"

    if ($DryRun) {
        Write-Host "  DRY-RUN: copy -> $dest" -ForegroundColor DarkYellow
        continue
    }

    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    if (Test-Path -LiteralPath $dest) {
        $oldDir = Join-Path $destDir "old"
        New-Item -ItemType Directory -Path $oldDir -Force | Out-Null
        $ts = Get-Date -Format "yyyyMMdd_HHmmss"
        Copy-Item -LiteralPath $dest -Destination (Join-Path $oldDir "${ts}_voiceover_before_recycle.txt") -Force
    }

    Copy-Item -LiteralPath $e.RecycleDataPath -Destination $dest -Force
    if (Test-Path -LiteralPath $dest) {
        $restored++
        $text = Get-Content -LiteralPath $dest -Raw -Encoding UTF8
        $preview = if ($text.Length -gt 400) { $text.Substring(0, 400) + "..." } else { $text }
        Write-Host "  OK -> $dest" -ForegroundColor Green
        Write-Host "  --- TEXT ($($text.Length) chars) ---" -ForegroundColor DarkCyan
        Write-Host $preview
        Write-Host "  --- END ---" -ForegroundColor DarkCyan
    } else {
        Write-Host "  FAILED copy" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Restored files: $restored / $($entries.Count)" -ForegroundColor Cyan

if (-not $SkipDb -and -not $DryRun -and $restored -gt 0) {
    Write-Host "Syncing script_text in DB (parents only)..." -ForegroundColor Cyan
    Push-Location $RepoRoot
    & $Py -m restore_original_voiceover --all-parents --force
    Pop-Location
}

if ($restored -eq 0 -and -not $DryRun) { exit 1 }
exit 0
