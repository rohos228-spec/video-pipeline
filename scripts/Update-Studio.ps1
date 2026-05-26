# Update = git pull + restart Studio. Run: UPDATE-STUDIO.cmd
$ErrorActionPreference = "Continue"
$core = Join-Path $PSScriptRoot "StudioUpdateCore.ps1"
. $core
$Root = Get-StudioRepoRoot -StartDir (Split-Path -Parent $PSScriptRoot)
if (-not $Root) { $Root = Get-StudioRepoRoot -StartDir $PSScriptRoot }
if (-not $Root) {
    Write-Host "ERROR: open folder with pyproject.toml" -ForegroundColor Red
    exit 1
}
Set-Location -LiteralPath $Root
Write-Host "Repo: $Root" -ForegroundColor DarkGray
$ok = Invoke-StudioFullUpdate -Root $Root
exit $(if ($ok) { 0 } else { 1 })
