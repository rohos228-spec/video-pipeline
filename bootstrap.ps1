# video-pipeline: bootstrap для нового ПК
#   iwr https://raw.githubusercontent.com/rohos228-spec/video-pipeline/refs/heads/fix/text-save-persistence-v153/bootstrap.ps1 -UseBasicParsing | iex

$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:USERPROFILE "video-pipeline"
$Branch = "fix/text-save-persistence-v153"
$RepoUrl = "https://github.com/rohos228-spec/video-pipeline.git"

function Have-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "==> bootstrap | user=$env:USERNAME | dir=$InstallDir | branch=$Branch" -ForegroundColor Cyan

if (-not (Have-Cmd winget)) {
    Write-Host "ERROR: winget not found" -ForegroundColor Red
    exit 1
}
if (-not (Have-Cmd git)) {
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
}

if (Test-Path (Join-Path $InstallDir ".git")) {
    git -C $InstallDir fetch origin $Branch
    git -C $InstallDir reset --hard "origin/$Branch"
} else {
    git clone --branch $Branch $RepoUrl $InstallDir
}

Set-Location -LiteralPath $InstallDir
& powershell -ExecutionPolicy Bypass -File ".\install.ps1" -NonInteractive
Write-Host "==> Done. v160 branch. Run: .\run-backend.ps1" -ForegroundColor Green
