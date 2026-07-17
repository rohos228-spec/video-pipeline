# Shared helper: after git reset --hard, put dirty prompts/* back from a studio stash.
# No data/ copy, no overlay. Call: powershell -File Return-PromptsFromStash.ps1 -Root ... -StashRef ...
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [string]$StashRef = "stash@{0}",
    [switch]$Quiet
)

function Write-Rpfs {
    param([string]$Text, [string]$Color = "Gray")
    if ($Quiet) { return }
    Write-Host $Text -ForegroundColor $Color
}

if ([string]::IsNullOrWhiteSpace($StashRef)) {
    Write-Rpfs "Return-PromptsFromStash: empty stash ref — skip" "DarkGray"
    exit 0
}

# quotepath=false: Cyrillic / spaces in prompt names must stay literal paths
git -C $Root -c core.quotepath=false rev-parse --verify $StashRef 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Rpfs "Return-PromptsFromStash: $StashRef not found — skip" "Yellow"
    exit 0
}

Write-Rpfs "==> Return local prompts/ edits from $StashRef" "Cyan"
$count = 0

$tracked = @(
    git -C $Root -c core.quotepath=false stash show --name-only $StashRef 2>$null |
        Where-Object { $_ -and ($_ -replace '\\', '/') -match '^prompts/' }
)
foreach ($rel in $tracked) {
    git -C $Root -c core.quotepath=false checkout $StashRef -- $rel 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $count++ }
}

git -C $Root -c core.quotepath=false rev-parse --verify "$StashRef^3" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    $untracked = @(
        git -C $Root -c core.quotepath=false ls-tree -r --name-only "$StashRef^3" -- prompts 2>$null
    )
    foreach ($rel in $untracked) {
        if (-not $rel) { continue }
        $norm = $rel -replace '\\', '/'
        if ($norm -notmatch '^prompts/') { continue }
        git -C $Root -c core.quotepath=false checkout "$StashRef^3" -- $rel 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $count++ }
    }
}

if ($count -gt 0) {
    Write-Rpfs "OK: restored prompts/ files: $count" "Green"
} else {
    Write-Rpfs "No prompts/ edits in stash." "DarkGray"
}
exit 0
