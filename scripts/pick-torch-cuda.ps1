# Pick PyTorch CUDA wheel tag from nvidia-smi (before torch is installed).
# Output: cu128 | cu124
# Exit 1 if nvidia-smi fails.

$ErrorActionPreference = "Stop"
$line = (& nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
if (-not $line) { exit 1 }
$parts = $line -split ',\s*', 2
$name = $parts[0].Trim()
$cap = if ($parts.Length -gt 1) { $parts[1].Trim() } else { "" }
$major = 0
if ($cap -match '^(\d+)') { $major = [int]$Matches[1] }

# Blackwell RTX 50xx = compute capability 12.x (sm_120) -> cu128 only
if ($major -ge 12 -or $name -match 'RTX 50') {
    Write-Output "cu128"
    exit 0
}
Write-Output "cu124"
exit 0
