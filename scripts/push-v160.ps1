# Base: 5ab9f8d (Studio v159). Add prompt-history, build, STUDIO_VERSION=160, push.
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$UpdateBranch = "fix/text-save-persistence-v153"
$BaseCommit = "5ab9f8d"
$LogFile = Join-Path $Root "data\push-v160.log"

$FeatureFiles = @(
    "app/services/prompt_history.py",
    "app/web/routers/prompt_files.py",
    "web/src/components/studio/prompt-files-panel.tsx",
    "web/src/components/ui/dropdown-menu.tsx",
    "web/src/lib/api.ts",
    "tests/test_prompt_history.py",
    "scripts/push-v160.ps1",
    "scripts/StudioUpdateCore.ps1",
    "FORCE-UPDATE.cmd",
    "VideoPipelineStudio.cmd",
    "installer/VideoPipelineLauncher.ps1",
    "PUSH-V160.cmd",
    "UPDATE-V160.cmd"
)

function Log($m, $c = "Gray") {
    Write-Host $m -ForegroundColor $c
    if ($LogFile) {
        $logDir = Split-Path -Parent $LogFile
        if ($logDir -and -not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        Add-Content -LiteralPath $LogFile -Value $m -Encoding UTF8
    }
}

function Invoke-Git {
    param(
        [switch]$Quiet,
        [switch]$AllowFail,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs
    )
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $out = @(& git.exe @GitArgs 2>&1)
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if (-not $Quiet) {
        foreach ($line in $out) {
            if ($null -ne $line -and "$line".Trim()) { Log $line }
        }
    }
    if ($code -ne 0 -and -not $AllowFail) {
        $detail = ($out | ForEach-Object { "$_" }) -join " | "
        if (-not $detail) { $detail = "(no git output)" }
        throw ("git failed ({0}): git {1} :: {2}" -f $code, ($GitArgs -join " "), $detail)
    }
    return $out
}

function Ensure-GitIdentity {
    $name = (git.exe config user.name 2>$null)
    $email = (git.exe config user.email 2>$null)
    if ($name -and $email) { return }

    Log "> git user.name/email not set - using author from last commit" Yellow
    $name = (git.exe log -1 --format="%an" 2>$null).Trim()
    $email = (git.exe log -1 --format="%ae" 2>$null).Trim()
    if (-not $name -or -not $email) {
        throw "git identity missing. Run once: git config --global user.name `"Your Name`" ; git config --global user.email you@example.com"
    }
    $env:GIT_AUTHOR_NAME = $name
    $env:GIT_AUTHOR_EMAIL = $email
    $env:GIT_COMMITTER_NAME = $name
    $env:GIT_COMMITTER_EMAIL = $email
}

function Backup-FeatureFiles {
    param([string]$Dest)
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    foreach ($rel in $FeatureFiles) {
        $src = Join-Path $Root ($rel -replace "/", "\")
        if (Test-Path -LiteralPath $src) {
            $dst = Join-Path $Dest ($rel -replace "/", "\")
            $parent = Split-Path -Parent $dst
            if ($parent -and -not (Test-Path $parent)) {
                New-Item -ItemType Directory -Force -Path $parent | Out-Null
            }
            Copy-Item -LiteralPath $src -Destination $dst -Force
        }
    }
}

function Restore-FeatureFiles {
    param([string]$Src)
    foreach ($rel in $FeatureFiles) {
        $srcFile = Join-Path $Src ($rel -replace "/", "\")
        if (Test-Path -LiteralPath $srcFile) {
            $dst = Join-Path $Root ($rel -replace "/", "\")
            $parent = Split-Path -Parent $dst
            if ($parent -and -not (Test-Path $parent)) {
                New-Item -ItemType Directory -Force -Path $parent | Out-Null
            }
            Copy-Item -LiteralPath $srcFile -Destination $dst -Force
        }
    }
}

if (Test-Path $LogFile) { Remove-Item -LiteralPath $LogFile -Force -ErrorAction SilentlyContinue }
Log "=== push-v160: base $BaseCommit (v159) branch $UpdateBranch ===" Cyan

$backupDir = Join-Path $env:TEMP ("video-pipeline-v160-" + [guid]::NewGuid().ToString("n"))
Backup-FeatureFiles -Dest $backupDir
Log "> backed up v160 files to temp" DarkGray

Invoke-Git fetch origin $UpdateBranch

Invoke-Git -AllowFail merge --abort
Invoke-Git -AllowFail reset --merge

$lock = Join-Path $Root ".git\index.lock"
if (Test-Path $lock) { Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }

Log "> discard local START.ps1 noise" Yellow
foreach ($f in @("START.ps1", "start.ps1")) {
    if (Test-Path (Join-Path $Root $f)) {
        Invoke-Git -AllowFail restore --staged --worktree -- $f
    }
}

Log "> checkout v159 base (no stash)" Cyan
Invoke-Git checkout -B $UpdateBranch $BaseCommit

Restore-FeatureFiles -Src $backupDir
Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction SilentlyContinue

$vf = Join-Path $Root "web\STUDIO_VERSION"
if (Test-Path $vf) {
    $lines = Get-Content $vf -TotalCount 4
    Log "Base STUDIO_VERSION: v$($lines[0]) $($lines[1]) $($lines[2])" Green
}

Invoke-Git -AllowFail restore --staged --worktree -- START.ps1 start.ps1

if (-not (Test-Path (Join-Path $Root ".venv\Scripts\python.exe"))) {
    throw ".venv missing - run install.ps1"
}

Log "> npm run build" Cyan
Push-Location (Join-Path $Root "web")
try {
    if (-not (Test-Path "node_modules")) {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    if (-not (Test-Path "out\index.html")) { throw "web/out missing" }
} finally {
    Pop-Location
}

$build = 160
$attach = "attach-guard-v84-download-fast"
$orch = "xlsx_step_runners-v73"
if (Test-Path $vf) {
    $old = Get-Content $vf -Encoding UTF8
    if ($old.Count -gt 2 -and $old[2]) { $attach = $old[2].Trim() }
    if ($old.Count -gt 3 -and $old[3]) { $orch = $old[3].Trim() }
}

Invoke-Git add app/ web/ tests/test_prompt_history.py scripts/ FORCE-UPDATE.cmd VideoPipelineStudio.cmd installer/VideoPipelineLauncher.ps1 PUSH-V160.cmd UPDATE-V160.cmd
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& git.exe reset -- data/ prompts/ .env START.ps1 start.ps1 2>$null | Out-Null
$ErrorActionPreference = $prevEap

$pending = @(Invoke-Git -Quiet diff --cached --name-only)
if (-not $pending.Count) { throw "nothing staged - prompt-history files missing?" }
Log "> staged $($pending.Count) files" Green

Ensure-GitIdentity

$msgFile = Join-Path $env:TEMP ("v160-commit-" + [guid]::NewGuid().ToString("n") + ".txt")
@(
    "Studio v160: prompt file history per prompt row."
    ""
    "History dropdown beside each .md; auto-archive on save; rename/restore versions."
    ""
    "Base: v159 ($BaseCommit)."
) | Set-Content -LiteralPath $msgFile -Encoding UTF8
try {
    Invoke-Git commit -F $msgFile
} finally {
    Remove-Item -LiteralPath $msgFile -Force -ErrorAction SilentlyContinue
}

$sha = (git.exe rev-parse --short HEAD).Trim()
@([string]$build, $sha, $attach, $orch) -join "`n" | Set-Content -LiteralPath $vf -Encoding UTF8 -NoNewline
Add-Content -LiteralPath $vf -Value "" -Encoding UTF8
Invoke-Git add web/STUDIO_VERSION web/out
Invoke-Git commit --amend --no-edit

Log "STUDIO_VERSION v$build $sha" Green
Get-Content $vf | ForEach-Object { Log "  $_" DarkGray }

Log "> git push origin $UpdateBranch" Cyan
Invoke-Git push -u origin $UpdateBranch

Log "DONE v160 pushed to origin/$UpdateBranch" Green
Log "Log file: $LogFile" DarkGray
