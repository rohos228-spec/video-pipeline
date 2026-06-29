# Commit + push full Studio update to feature/fleet-montage-queue-v161
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Branch = "feature/fleet-montage-queue-v161"
$LogFile = Join-Path $Root "data\push-11labs.log"
$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}
if (Test-Path $LogFile) { Remove-Item -LiteralPath $LogFile -Force }

function Log($m, $c = "Gray") {
    Write-Host $m -ForegroundColor $c
    Add-Content -LiteralPath $LogFile -Value $m -Encoding UTF8
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
        throw "git identity missing. Run: git config --global user.name `"Your Name`" ; git config --global user.email you@example.com"
    }
    $env:GIT_AUTHOR_NAME = $name
    $env:GIT_AUTHOR_EMAIL = $email
    $env:GIT_COMMITTER_NAME = $name
    $env:GIT_COMMITTER_EMAIL = $email
}

Log "=== push full studio update ===" Cyan
Log "> branch: $Branch"

$current = (git.exe branch --show-current 2>$null).Trim()
if ($current -ne $Branch) {
    if (git.exe show-ref --verify --quiet "refs/heads/$Branch" 2>$null) {
        Invoke-Git checkout $Branch
    } else {
        Invoke-Git checkout -b $Branch
    }
}

Invoke-Git status -sb

Log "> npm run build" Cyan
Push-Location (Join-Path $Root "web")
try {
    if (-not (Test-Path "node_modules")) {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    if (-not (Test-Path "out\index.html")) { throw "web/out missing after build" }
} finally {
    Pop-Location
}

Log "> stage project files" Cyan
Invoke-Git add app/ web/ tests/ scripts/ docs/ prompts/ fleet/ templates/
Invoke-Git add pyproject.toml run-backend.ps1 check-backend.cmd start-backend.cmd BUILD-WEB.cmd GO.cmd .env.example .env.fleet.example .gitignore
Invoke-Git add *.cmd

Invoke-Git -AllowFail reset -- data/ .env .env.local .env.bak .env.bak-* .cursor/ _loc_count.py browser_profile/ fleet/secrets.env

$pending = @(Invoke-Git -Quiet diff --cached --name-only)
if (-not $pending.Count) {
    throw "nothing staged - no changes to commit"
}
Log "> staged $($pending.Count) files" Green

Ensure-GitIdentity

$msgFile = Join-Path $env:TEMP ("studio-update-" + [guid]::NewGuid().ToString("n") + ".txt")
@(
    "Studio update: fleet montage, ElevenLabs Lab, montage ASR, and UI."
    ""
    "Fleet hub/worker handoff, montage queue UI, NVIDIA ASR timestamps, ElevenLabs"
    "voice library with gender/language/accent filters, proxy diagnostics, and 11Labs tab."
) -join "`n" | ForEach-Object {
    [System.IO.File]::WriteAllText($msgFile, $_, (New-Object System.Text.UTF8Encoding $false))
}
try {
    Invoke-Git commit -F $msgFile
} finally {
    Remove-Item -LiteralPath $msgFile -Force -ErrorAction SilentlyContinue
}

$sha = (git.exe rev-parse --short HEAD).Trim()
$vf = Join-Path $Root "web\STUDIO_VERSION"
if (Test-Path $vf) {
    $old = Get-Content $vf -Encoding UTF8
    $build = if ($old.Count -gt 0 -and $old[0]) { $old[0].Trim() } else { "161" }
    $attach = if ($old.Count -gt 2 -and $old[2]) { $old[2].Trim() } else { "attach-guard-v84-download-fast" }
    $orch = if ($old.Count -gt 3 -and $old[3]) { $old[3].Trim() } else { "xlsx_step_runners-v73" }
    $versionText = (@([string]$build, $sha, $attach, $orch) -join "`n") + "`n"
    [System.IO.File]::WriteAllText($vf, $versionText, (New-Object System.Text.UTF8Encoding $false))
    Invoke-Git add web/STUDIO_VERSION
    Invoke-Git commit --amend --no-edit
    $sha = (git.exe rev-parse --short HEAD).Trim()
    Log "STUDIO_VERSION v$build $sha" Green
}

Log "> git push origin $Branch" Cyan
Invoke-Git push -u origin $Branch
Log "DONE - pushed to origin/$Branch ($sha)" Green
Log "Log: $LogFile" DarkGray
