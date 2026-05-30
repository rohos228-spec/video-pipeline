# Полный бэкап video-pipeline для переноса на другой ПК.
# Включает: код, data/ (проекты, видео, картинки, state.db), .env, prompts, web/out
#
#   scripts\EXPORT-FULL-BACKUP.cmd
#   scripts\EXPORT-FULL-BACKUP.cmd -OutDir D:\Backups
#
# На новом ПК: распаковать → setup-new-pc.ps1 (см. scripts\restore-new-pc.ps1)

param(
    [string]$OutDir = "$env:USERPROFILE\Desktop"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Stamp = Get-Date -Format "yyyyMMdd-HHmm"
$DestFolder = Join-Path $OutDir "video-pipeline-full-$Stamp"
$ZipPath = "$DestFolder.zip"

Write-Host "==> Full backup: $Root"
Write-Host "    -> $ZipPath"

$ExcludeDirs = @(
    ".venv", "venv", "env",
    "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git",
    ".cursor",
    ".ai-orchestra-mcp\node_modules"
)

function Should-SkipDir([string]$rel) {
    foreach ($x in $ExcludeDirs) {
        if ($rel -eq $x -or $rel.StartsWith("$x\")) { return $true }
    }
    return $false
}

New-Item -ItemType Directory -Force -Path $DestFolder | Out-Null
$Staging = Join-Path $DestFolder "video-pipeline"

# robocopy: быстро, с зеркалированием структуры
$robocopyArgs = @(
    $Root, $Staging,
    "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/nc", "/ns", "/np",
    "/XD", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", ".cursor"
)
& robocopy @robocopyArgs | Out-Null
# robocopy exit 0-7 = OK
if ($LASTEXITCODE -gt 7) { throw "robocopy failed: $LASTEXITCODE" }

# Манифест
$manifest = @"
video-pipeline full backup
created: $(Get-Date -Format o)
source: $Root

Included:
  - data/          projects, videos, images, state.db
  - app/, web/, prompts/, scripts/
  - .env           secrets (keep private!)
  - web/out/       built Studio UI

Excluded (reinstall on new PC):
  - .venv/
  - node_modules/
  - .git/

Restore on new PC:
  1. Unzip anywhere, e.g. C:\Projects\video-pipeline
  2. Run: scripts\restore-new-pc.ps1
  3. Copy browser profile / Chrome CDP if using outsee automation
"@
$manifest | Out-File -FilePath (Join-Path $DestFolder "BACKUP-README.txt") -Encoding utf8

# .env — явно (может быть скрыт)
if (Test-Path (Join-Path $Root ".env")) {
    Copy-Item (Join-Path $Root ".env") (Join-Path $Staging ".env") -Force
}

Write-Host "==> Compressing (may take several minutes for large videos)..."
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path $DestFolder -DestinationPath $ZipPath -CompressionLevel Optimal

$sizeGb = [math]::Round((Get-Item $ZipPath).Length / 1GB, 2)
Write-Host ""
Write-Host "DONE: $ZipPath"
Write-Host "Size: $sizeGb GB"
Write-Host ""
Write-Host "Upload this ZIP to Google Drive / Yandex Disk / external drive."
Write-Host "Git repo alone does NOT include data/ (videos) — use this archive."

# cleanup staging folder (keep zip only)
Remove-Item $DestFolder -Recurse -Force
