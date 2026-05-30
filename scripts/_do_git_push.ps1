$ErrorActionPreference = 'Continue'
$log = Join-Path $PSScriptRoot '..' '_git_push_log.txt' | Resolve-Path -ErrorAction SilentlyContinue
if (-not $log) { $log = Join-Path (Split-Path $PSScriptRoot -Parent) '_git_push_log.txt' }
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

function Log($msg) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Output $line
}

Remove-Item $log -ErrorAction SilentlyContinue
Log "=== GIT PUSH SCRIPT ==="
Log "REPO=$repo"

$cmds = @(
    'git rev-parse --show-toplevel',
    'git status -sb',
    'git diff --stat',
    'git log -3 --oneline',
    'git branch -vv',
    'git remote -v'
)
foreach ($c in $cmds) {
    Log ">>> $c"
    $out = cmd /c "$c 2>&1"
    Log ($out | Out-String)
}

Log '>>> git add -A'
$out = cmd /c 'git add -A 2>&1'
Log ($out | Out-String)

Log '>>> git commit'
$msg = @'
Add GPT Image 2 quality and 1K resolution options with screenshot defaults

- Add image_quality (low/medium/high) for GPT Image 1.5/2
- Add 1K image resolution option
- Wizard step, outsee clicks, pipeline passthrough
- Defaults: gpt_image_2, 16:9, 2K, medium
'@
$msgFile = Join-Path $env:TEMP 'vp_commit_msg.txt'
Set-Content -Path $msgFile -Value $msg -Encoding UTF8
$out = cmd /c "git commit -F `"$msgFile`" 2>&1"
Log ($out | Out-String)
Remove-Item $msgFile -ErrorAction SilentlyContinue

Log '>>> git rev-parse HEAD'
$hash = cmd /c 'git rev-parse HEAD 2>&1'
Log "HASH=$hash"

Log '>>> git push -u origin HEAD'
$out = cmd /c 'git push -u origin HEAD 2>&1'
Log ($out | Out-String)
if ($LASTEXITCODE -ne 0) {
    Log '>>> push failed, trying pull --rebase'
    $out = cmd /c 'git pull --rebase origin HEAD 2>&1'
    Log ($out | Out-String)
    $out = cmd /c 'git push -u origin HEAD 2>&1'
    Log ($out | Out-String)
}

Log "EXIT=$LASTEXITCODE"
Log '=== DONE ==='
