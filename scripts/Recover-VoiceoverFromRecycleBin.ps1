# Восстановление voiceover.txt из корзины Windows для родительских проектов.
# Python unlink() НЕ кладёт файлы в Recycle Bin — скрипт ищет то, что удалили
# через Проводник / Shift+Delete с оригинальным путём data\videos\<slug>\.
#
# Запуск из корня репозитория:
#   powershell -ExecutionPolicy Bypass -File scripts\Recover-VoiceoverFromRecycleBin.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\Recover-VoiceoverFromRecycleBin.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File scripts\Recover-VoiceoverFromRecycleBin.ps1 -ThenRestoreDb
#
param(
    [switch]$DryRun,
    [switch]$ThenRestoreDb,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataDir) {
    $DataDir = Join-Path $RepoRoot "data"
}
$VideosDir = Join-Path $DataDir "videos"
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $Py = "python"
}

function Get-RecycleBinVoiceoverItems {
    $shell = New-Object -ComObject Shell.Application
    $bin = $shell.NameSpace(0xA)  # Recycle Bin
    if (-not $bin) { return @() }
    $items = @()
    foreach ($item in $bin.Items()) {
        $name = [string]$item.Name
        if ($name -notmatch "voiceover" -and $name -notne "voiceover.txt") { continue }
        $orig = ""
        try { $orig = [string]$item.ExtendedProperty("System.Recycle.DeletedFrom") } catch {}
        if (-not $orig) {
            try { $orig = [string]$item.Path } catch {}
        }
        $items += [PSCustomObject]@{
            Name = $name
            OriginalPath = $orig
            Size = $item.Size
            Item = $item
        }
    }
    return $items
}

function Resolve-SlugFromPath {
    param([string]$Path)
    if (-not $Path) { return $null }
    $norm = $Path -replace "\\", "/"
    if ($norm -match "videos/([^/]+)/voiceover\.txt$") {
        return $Matches[1]
    }
    return $null
}

Write-Host "=== Recover voiceover from Windows Recycle Bin ===" -ForegroundColor Cyan
Write-Host "Videos: $VideosDir"

$rbItems = Get-RecycleBinVoiceoverItems
Write-Host "Recycle Bin voiceover items: $($rbItems.Count)"

if ($rbItems.Count -eq 0) {
    Write-Host "В корзине нет voiceover.txt. Также проверьте:" -ForegroundColor Yellow
    Write-Host "  data\videos\<slug>\old\*_voiceover*.txt"
    Write-Host "  data\videos\<slug>\.trash\*voiceover*"
    Write-Host "  data\videos\<slug>\tmp_gpt\voiceover_*.txt"
    exit 0
}

$restored = 0
foreach ($rb in $rbItems) {
    $slug = Resolve-SlugFromPath $rb.OriginalPath
    if (-not $slug) {
        Write-Host "[skip] $($rb.Name) — неизвестный путь: $($rb.OriginalPath)" -ForegroundColor DarkYellow
        continue
    }
    $destDir = Join-Path $VideosDir $slug
    $dest = Join-Path $destDir "voiceover.txt"
    Write-Host "[match] slug=$slug <= $($rb.OriginalPath)" -ForegroundColor Green
    if ($DryRun) {
        Write-Host "  dry-run: would restore -> $dest"
        continue
    }
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    if (Test-Path $dest) {
        $oldDir = Join-Path $destDir "old"
        New-Item -ItemType Directory -Path $oldDir -Force | Out-Null
        $ts = Get-Date -Format "yyyyMMdd_HHmmss"
        Copy-Item $dest (Join-Path $oldDir "${ts}_voiceover_before_recycle_restore.txt") -Force
    }
    try {
        $rb.Item.InvokeVerb("RESTORE")
        Start-Sleep -Milliseconds 400
    } catch {
        Write-Host "  RESTORE verb failed: $_" -ForegroundColor Red
    }
    if (-not (Test-Path $dest)) {
        $candidates = Get-ChildItem -Path $VideosDir -Recurse -Filter "voiceover.txt" -ErrorAction SilentlyContinue |
            Where-Object { $_.DirectoryName -like "*$slug*" } |
            Sort-Object LastWriteTime -Descending
        if ($candidates) {
            Copy-Item $candidates[0].FullName $dest -Force
        }
    }
    if (Test-Path $dest) {
        $restored++
        Write-Host "  restored: $dest ($((Get-Item $dest).Length) bytes)" -ForegroundColor Green
    } else {
        Write-Host "  WARN: file not at $dest after restore — проверьте корзину вручную" -ForegroundColor Yellow
    }
}

Write-Host "Restored from Recycle Bin: $restored" -ForegroundColor Cyan

if ($ThenRestoreDb -and -not $DryRun -and $restored -gt 0) {
    Write-Host "Running DB restore (parents only)..." -ForegroundColor Cyan
    & $Py -m restore_original_voiceover --all-parents
}
