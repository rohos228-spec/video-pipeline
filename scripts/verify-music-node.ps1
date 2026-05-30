# Verify music node UI + API. Run after BUILD-WEB.cmd and backend restart.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== Music node verification ===" -ForegroundColor Cyan
Write-Host "Repo: $Root`n"

$fail = 0

function Fail($msg) {
    Write-Host "[FAIL] $msg" -ForegroundColor Red
    $script:fail++
}
function Ok($msg) {
    Write-Host "[OK]   $msg" -ForegroundColor Green
}
function Warn($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

# 1. Source
$src = Get-Content "web\src\lib\node-prompts.ts" -Raw -Encoding UTF8
if ($src -match 'music:\s*\[') {
    if ($src -match 'id:\s*"voiceover"' -and $src -match 'id:\s*"gpt_text"') {
        Ok "source: music has voiceover + gpt_text slots"
    } else {
        Fail "source: music slots incomplete"
    }
    if ($src -match 'nodeType === "music"\) \{\s*return raw\.filter') {
        Ok "source: resolvePromptSlots keeps music slots"
    } else {
        Fail "source: resolvePromptSlots music fix missing"
    }
} else {
    Fail "source: music BASE missing"
}

# 2. Built bundle
$bundle = Get-ChildItem "web\out\_next\static\chunks\app\page-*.js" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $bundle) {
    Fail "web/out: page bundle missing - run BUILD-WEB.cmd"
} else {
    $js = Get-Content $bundle.FullName -Raw -Encoding UTF8
    foreach ($needle in @("music-note", "Открыть GPT", "Прикрепляемые файлы", "gpt_text")) {
        if ($js -match [regex]::Escape($needle)) {
            Ok "bundle contains: $needle"
        } else {
            Fail "bundle missing: $needle (run BUILD-WEB.cmd)"
        }
    }
    Write-Host "       bundle: $($bundle.Name)  $($bundle.LastWriteTime)" -ForegroundColor DarkGray
}

# 3. STUDIO_VERSION
if (Test-Path "web\STUDIO_VERSION") {
    $ver = Get-Content "web\STUDIO_VERSION" -TotalCount 3
    Ok "STUDIO_VERSION: v$($ver[0]) $($ver[1]) $($ver[2])"
} else {
    Warn "web/STUDIO_VERSION missing"
}

# 4. Backend Python
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $py) {
    $out = & $py -c @"
from app.services import gpt_text_builder as gtb
from pathlib import Path
class P:
    data_dir = Path('.')
    topic = 'test'
    prompt_overrides = {}
    gpt_text_overrides = {}
items = gtb.list_step_attachments(P(), 'music')
assert len(items)==1 and items[0]['label']=='voiceover.txt', items
print('attachments_ok')
"@ 2>&1
    if ($out -match "attachments_ok") {
        Ok "backend: list_step_attachments(music) -> voiceover.txt"
    } else {
        Fail "backend gpt_text_builder: $out"
    }

    $gm = Get-Content "app\orchestrator\steps\generate_music.py" -Raw
    if ($gm -match "ask_with_files" -and $gm -match "generate_music") {
        Ok "backend: generate_music GPT then Outsee"
    } else {
        Fail "backend: generate_music flow broken"
    }
} else {
    Warn ".venv missing - skip Python checks"
}

# 5. Live API
try {
    $health = Invoke-RestMethod "http://127.0.0.1:8765/api/health" -TimeoutSec 3
    Ok "API health: $($health.status)"
    $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
    Write-Host "       studio-version: $($sv.label)" -ForegroundColor DarkGray
    if ($sv.label -notmatch "v11[2-9]|v1[2-9]\d") {
        Warn "UI version may be old ($($sv.label)) - restart backend after BUILD-WEB"
    }
    $projects = Invoke-RestMethod "http://127.0.0.1:8765/api/projects?limit=1" -TimeoutSec 5
    if ($projects -and $projects.Count -gt 0) {
        $pid = $projects[0].id
        $gpt = Invoke-RestMethod "http://127.0.0.1:8765/api/prompt-studio/projects/$pid/gpt-text/music" -TimeoutSec 10
        if ($gpt.supported -eq $true) {
            Ok "API gpt-text/music supported for project #$pid"
            $att = @($gpt.attachments)
            if ($att.Count -ge 1 -and $att[0].label -eq "voiceover.txt") {
                Ok "API attachments: voiceover.txt"
            } else {
                Fail "API attachments empty or wrong: $($gpt.attachments | ConvertTo-Json -Compress)"
            }
        } else {
            Warn "gpt-text/music not supported (project #$pid)"
        }
    } else {
        Warn "no projects - skip gpt-text API test"
    }
} catch {
    Warn "backend not running on :8765 - start run-backend.ps1"
}

Write-Host ""
if ($fail -gt 0) {
    Write-Host "FAILED: $fail check(s). Run BUILD-WEB.cmd then restart backend." -ForegroundColor Red
    exit 1
}
Write-Host "All checks passed." -ForegroundColor Green
exit 0
