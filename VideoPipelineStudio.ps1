# Точка входа — работает из любой подпапки репозитория
$Root = $PSScriptRoot
while ($Root -and -not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $parent = Split-Path -Parent $Root
    if (-not $parent -or $parent -eq $Root) { break }
    $Root = $parent
}
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Host "ERROR: pyproject.toml not found. cd to video-pipeline root." -ForegroundColor Red
    exit 1
}
& (Join-Path $Root "installer\VideoPipelineLauncher.ps1")
