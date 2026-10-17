# ASCII-only. Windows PowerShell 5.1 cannot parse UTF-8 Cyrillic without BOM.
# STUDIO.cmd runs this BEFORE -File scripts\studio.ps1.
param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)
$ErrorActionPreference = "Continue"
if (-not (Test-Path -LiteralPath $Path)) { exit 0 }
$bytes = [System.IO.File]::ReadAllBytes($Path)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)
if ($hasBom) { exit 0 }
$out = New-Object byte[] ($bytes.Length + 3)
$out[0] = 239
$out[1] = 187
$out[2] = 191
[System.Array]::Copy($bytes, 0, $out, 3, $bytes.Length)
[System.IO.File]::WriteAllBytes($Path, $out)
Write-Host "STUDIO_HEALED: UTF-8 BOM restored for PowerShell 5.1"
