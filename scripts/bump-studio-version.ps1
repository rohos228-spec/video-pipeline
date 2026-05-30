# Bump web/STUDIO_VERSION after local npm build.
param(
    [string]$Tag = "local-build"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$vf = Join-Path $Root "web\STUDIO_VERSION"
$attach = "anim-pr-two-phase-v79"
$orch = "xlsx_step_runners-v70"
$build = 113
if (Test-Path $vf) {
    $lines = Get-Content $vf -Encoding UTF8
    if ($lines.Count -gt 0 -and $lines[0] -match '(\d+)') {
        $n = [int]$Matches[1]
        if ($n -ge $build) { $build = $n + 1 }
    }
    if ($lines.Count -gt 2 -and $lines[2]) { $attach = $lines[2].Trim() }
    if ($lines.Count -gt 3 -and $lines[3]) { $orch = $lines[3].Trim() }
}
$sha = "local"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $g = (git -C $Root rev-parse --short HEAD 2>$null)
    if ($g) { $sha = $g.Trim() }
}
$content = @(
    [string]$build
    $sha
    $attach
    $orch
) -join "`n"
[System.IO.File]::WriteAllText($vf, $content + "`n", (New-Object System.Text.UTF8Encoding $false))
Write-Host "STUDIO_VERSION -> v$build  $sha  ($Tag)" -ForegroundColor Green
