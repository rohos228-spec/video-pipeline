# One-shot: branch + commit + push for fleet montage queue feature.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$branch = "feature/fleet-montage-queue-v161"
$current = (git branch --show-current).Trim()
if ($current -eq $branch) {
    Write-Host "Already on branch $branch"
} elseif (git show-ref --verify --quiet "refs/heads/$branch") {
    Write-Host "Checking out existing branch $branch ..."
    git checkout $branch
} else {
    Write-Host "Creating branch $branch ..."
    git checkout -b $branch
}

$authorLine = (git log -1 --format="%an|%ae").Trim()
if (-not $authorLine -or $authorLine -notmatch "\|") {
    throw "Cannot detect git author from last commit."
}
$parts = $authorLine -split "\|", 2
$authorName = $parts[0]
$authorEmail = $parts[1]
Write-Host "Author: $authorName <$authorEmail>"

$files = @(
    "app/fleet/montage_queue.py",
    "app/fleet/pull_loop.py",
    "app/web/routers/fleet.py",
    "app/web/api.py",
    "app/settings.py",
    "app/services/node_step_params.py",
    "app/orchestrator/steps/generate_music.py",
    "app/services/artifact_recovery.py",
    "web/src/lib/node-step-params.ts",
    "web/src/components/studio/node-step-params-panel.tsx",
    "web/src/components/fleet/fleet-project-status.tsx",
    "web/src/components/fleet/fleet-panel.tsx",
    ".env.fleet.example"
)

foreach ($f in $files) {
    if (Test-Path $f) {
        git add $f
        Write-Host "  staged $f"
    } else {
        Write-Host "  skip (missing) $f"
    }
}

$staged = @(git diff --cached --name-only)
if ($staged.Count -eq 0) {
    Write-Host "Nothing staged - commit skipped."
    exit 0
}

$commitMsg = "Add fleet montage queue and send_to_main_pc toggle" + [Environment]::NewLine + [Environment]::NewLine +
    "Route hub montage through a queue instead of direct assembling, expose queue" + [Environment]::NewLine +
    "status in fleet UI, and add an assemble-node option to send projects to the" + [Environment]::NewLine +
    "main PC without changing assemble/ASR/FFmpeg logic."

$env:GIT_AUTHOR_NAME = $authorName
$env:GIT_AUTHOR_EMAIL = $authorEmail
$env:GIT_COMMITTER_NAME = $authorName
$env:GIT_COMMITTER_EMAIL = $authorEmail

& git.exe commit -m $commitMsg
if ($LASTEXITCODE -ne 0) { throw "git commit failed with exit code $LASTEXITCODE" }

$hash = (git rev-parse HEAD).Trim()
Write-Host "Commit: $hash"

& git.exe push -u origin $branch
if ($LASTEXITCODE -ne 0) { throw "git push failed with exit code $LASTEXITCODE" }

Write-Host "Pushed branch: $branch"
& git.exe show --name-only --pretty=format: HEAD
