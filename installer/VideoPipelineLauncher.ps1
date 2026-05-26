# Video Pipeline Studio GUI launcher (ASCII-only for Windows PowerShell 5.x)
# Double-click VideoPipelineStudio.cmd in repo root
# LAUNCHER_UPDATE_ID=launcher-ps51-core-v84

$script:LAUNCHER_UPDATE_ID = "launcher-ps51-core-v84"
# Единственная ветка, с которой кнопка * Update + Start синхронизирует проект.
$script:StudioUpdateBranch = "cursor/fix-launcher-update-start-977b"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = (Get-Location).Path
}
Set-Location $Root

$script:StudioCorePath = Join-Path $Root "scripts\StudioUpdateCore.ps1"
if (Test-Path $script:StudioCorePath) {
    . $script:StudioCorePath
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    if ($machine -or $user) {
        $env:Path = "$machine;$user"
    }
}

Refresh-Path

$script:LogBox = $null
$script:StatusLbl = $null
$script:LauncherLogFile = Join-Path $Root "data\launcher.log"

function Write-LogCore([string]$line, [string]$Color) {
    if (-not $script:LogBox) {
        Write-Host $line
        return
    }
    $hadSelection = $script:LogBox.SelectionLength -gt 0
    $script:LogBox.SelectionColor = $Color
    $script:LogBox.AppendText("$line`r`n")
    if (-not $hadSelection) {
        $script:LogBox.ScrollToCaret()
    }
}

function Write-Log([string]$Text, [string]$Color = "Black") {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $Text"
    try {
        $logDir = Split-Path -Parent $script:LauncherLogFile
        if (-not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        Add-Content -Path $script:LauncherLogFile -Value $line -Encoding UTF8
    } catch { }
    Write-LogCore $line $Color
    [System.Windows.Forms.Application]::DoEvents()
}

function Write-CommandOutput($Output, [string]$Color = "Gray") {
    foreach ($line in @($Output)) {
        if ($null -ne $line -and "$line".Length -gt 0) {
            Write-Log "$line" $Color
        }
    }
}

function Invoke-GitLogged([string]$Label, [string[]]$GitArgs) {
    Write-Log "> $Label" "DarkBlue"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & git -C $Root @GitArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    Write-CommandOutput $out
    if ($code -ne 0) {
        Write-Log "FAIL $Label (git exit $code)" "DarkRed"
        return $false
    }
    Write-Log "OK $Label" "DarkGreen"
    return $true
}

function Invoke-PythonLogged([string]$Label, [string]$PyExe, [string[]]$PyArgs) {
    Write-Log "> $Label" "DarkBlue"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $PyExe @PyArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    Write-CommandOutput $out
    if ($code -ne 0) {
        Write-Log "FAIL $Label (exit $code)" "DarkRed"
        return $false
    }
    Write-Log "OK $Label" "DarkGreen"
    return $true
}

function Invoke-NpmLogged([string]$Label, [string]$NpmExe, [string[]]$NpmArgs, [string]$WorkDir) {
    Write-Log "> $Label" "DarkBlue"
    $argLine = $NpmArgs -join " "
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$NpmExe`" $argLine" `
        -WorkingDirectory $WorkDir -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $env:TEMP "vp-npm-out.txt") `
        -RedirectStandardError (Join-Path $env:TEMP "vp-npm-err.txt")
    if (Test-Path (Join-Path $env:TEMP "vp-npm-out.txt")) {
        Get-Content (Join-Path $env:TEMP "vp-npm-out.txt") -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Log $_ "Gray" }
    }
    if (Test-Path (Join-Path $env:TEMP "vp-npm-err.txt")) {
        Get-Content (Join-Path $env:TEMP "vp-npm-err.txt") -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Log $_ "DarkOrange" }
    }
    if ($proc.ExitCode -ne 0) {
        Write-Log "FAIL $Label (npm exit $($proc.ExitCode))" "DarkRed"
        return $false
    }
    Write-Log "OK $Label" "DarkGreen"
    return $true
}

function Stop-PortListener([int]$Port) {
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        foreach ($c in $conns) {
            if ($c.OwningProcess -gt 0) {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
                Write-Log "Stopped PID $($c.OwningProcess) on port $Port" "Gray"
            }
        }
    } catch {
        Write-Log "Port ${Port}: could not use Get-NetTCPConnection ($($_.Exception.Message))" "Gray"
    }
}

function Stop-AllBackendProcesses {
    Write-Log "Stopping old backend (port 8765, python, run-backend windows)..." "Gray"
    $stopScript = Join-Path $Root "scripts\stop-backend.ps1"
    if (Test-Path $stopScript) {
        try {
            & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stopScript -Quiet 2>&1 |
                ForEach-Object { if ("$_") { Write-Log "$_" "Gray" } }
        } catch {
            Write-Log "stop-backend.ps1: $($_.Exception.Message)" "DarkOrange"
        }
    } else {
        Stop-PortListener -Port 8765
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Root*" } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
    }
    Start-Sleep -Seconds 2
    Stop-PortListener -Port 8765
}

function Unlock-SharedBackendLog {
    $log = Join-Path $Root "data\backend.log"
    $dataDir = Join-Path $Root "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    }
    if (-not (Test-Path $log)) {
        return $true
    }
    try {
        $fs = [System.IO.File]::Open(
            $log,
            [System.IO.FileMode]::Append,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        $fs.Close()
        Write-Log "backend.log writable" "Gray"
        return $true
    } catch {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $bak = Join-Path $dataDir "backend.log.locked_$stamp"
        try {
            Move-Item -Path $log -Destination $bak -Force
            Write-Log "backend.log was locked — moved to $(Split-Path $bak -Leaf)" "DarkOrange"
            return $true
        } catch {
            Write-Log "backend.log locked (Notepad/other backend?) — new run uses data\backend-PID.log" "DarkOrange"
            return $false
        }
    }
}

function Test-Port8765Free {
    try {
        $conns = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop)
        if ($conns.Count -gt 0) {
            foreach ($c in $conns) {
                if ($c.OwningProcess -gt 0) {
                    Write-Log "Port 8765 still busy (PID $($c.OwningProcess))" "DarkRed"
                }
            }
            return $false
        }
    } catch {
        return $true
    }
    return $true
}

function Test-RunBackendScriptCurrent {
    $rb = Join-Path $Root "run-backend.ps1"
    if (-not (Test-Path $rb)) {
        Write-Log "WARN: run-backend.ps1 missing" "DarkRed"
        return $false
    }
    $text = Get-Content $rb -Raw -ErrorAction SilentlyContinue
    if ($text -match "RUN_BACKEND_ID=session-log-v2" -and $text -match "Write-BackendLogLine") {
        Write-Log "run-backend.ps1 OK (session-log-v2)" "Gray"
        return $true
    }
    if ($text -match "Out-File.*backend\.log" -or $text -match "Tee-Object.*backend\.log") {
        Write-Log "WARN: OLD run-backend.ps1 (log lock bug) — repairing from git..." "DarkRed"
        return $false
    }
    Write-Log "WARN: run-backend.ps1 outdated" "DarkOrange"
    return $false
}

function Repair-CriticalScriptsFromGit {
  $files = @(
        "run-backend.ps1",
        "scripts/stop-backend.ps1",
        "stop-backend.cmd",
        "installer/VideoPipelineLauncher.ps1"
    )
    $branch = Get-GitBranch
    $candidates = @()
    if ($branch -and $branch -ne "?") { $candidates += $branch }
    $candidates += @($script:StudioUpdateBranch, "devin/windows-installer")
    $seen = @{}
    foreach ($br in $candidates) {
        if ($seen[$br]) { continue }
        $seen[$br] = $true
        Write-Log "Trying to restore scripts from origin/$br ..." "Gray"
        if (-not (Invoke-GitLogged "git fetch origin $br" @("fetch", "origin", $br))) {
            continue
        }
        $checkoutArgs = @("checkout", "origin/$br", "--") + $files
        $ok = Invoke-GitLogged "git checkout scripts from $br" $checkoutArgs
        if ($ok -and (Test-RunBackendScriptCurrent)) {
            Write-Log "Scripts restored from origin/$br" "DarkGreen"
            return $true
        }
    }
    Write-Log "Could not restore run-backend.ps1 — run fix-update-files.cmd" "DarkRed"
    return $false
}

function Show-BackendFailureDiagnostics {
    Write-Log "--- backend diagnostics ---" "DarkOrange"
    if (-not (Test-Port8765Free)) {
        Write-Log "Fix: click 4 Stop, then * Update + Start again" "DarkOrange"
    }
    $dataDir = Join-Path $Root "data"
    if (Test-Path $dataDir) {
        $logs = Get-ChildItem $dataDir -Filter "backend*.log" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($logs) {
            Write-Log "Newest log: $($logs.Name)" "DarkOrange"
            try {
                Get-Content $logs.FullName -Tail 12 -ErrorAction Stop | ForEach-Object {
                    Write-Log "  $_" "Gray"
                }
            } catch {
                Write-Log "  (cannot read log — file locked)" "Gray"
            }
        }
    }
    Write-Log "--- end diagnostics ---" "DarkOrange"
}

function Test-LauncherScriptChanged([string]$BeforeHead, [string]$AfterHead) {
    if (-not $BeforeHead -or -not $AfterHead -or $BeforeHead -eq $AfterHead) {
        return $false
    }
    $names = @(git -C $Root diff --name-only "$BeforeHead" "$AfterHead" 2>$null)
    return ($names | Where-Object { $_ -eq "installer/VideoPipelineLauncher.ps1" }).Count -gt 0
}

function Restart-LauncherGui {
    $cmd = Join-Path $Root "VideoPipelineStudio.cmd"
    if (Test-Path $cmd) {
        Start-Process $cmd -WorkingDirectory $Root
    } else {
        Start-Process powershell -ArgumentList @(
            "-ExecutionPolicy", "Bypass", "-NoProfile", "-File",
            (Join-Path $Root "installer\VideoPipelineLauncher.ps1")
        ) -WorkingDirectory $Root
    }
    if ($script:MainForm) {
        $script:MainForm.Close()
    }
}

function Invoke-ButtonAction([string]$Label, [scriptblock]$Action) {
    Write-Log "=== $Label ===" "DarkBlue"
    if ($script:MainForm) {
        $script:MainForm.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
    }
    try {
        & $Action
        Update-StatusLabel
    } catch {
        Write-Log "ERROR ${Label}: $($_.Exception.Message)" "DarkRed"
        if ($_.Exception.InnerException) {
            Write-Log $_.Exception.InnerException.Message "DarkRed"
        }
    } finally {
        if ($script:MainForm) {
            $script:MainForm.Cursor = [System.Windows.Forms.Cursors]::Default
        }
    }
}

function Get-NpmCmd {
    Refresh-Path
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npm) { return $npm.Source }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) { return $npm.Source }
    $candidates = @(
        (Join-Path ${env:ProgramFiles} "nodejs\npm.cmd"),
        (Join-Path ${env:ProgramFiles(x86)} "nodejs\npm.cmd"),
        (Join-Path $env:LOCALAPPDATA "Programs\nodejs\npm.cmd")
    )
    foreach ($guess in $candidates) {
        if ($guess -and (Test-Path $guess)) { return $guess }
    }
    return $null
}

function Remove-WebBuildArtifacts {
    $targets = @(
        (Join-Path $Root "web\out"),
        (Join-Path $Root "web\.next")
    )
    foreach ($t in $targets) {
        if (Test-Path $t) {
            Write-Log "Removing $t (clean UI rebuild)..." "Gray"
            Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-BackendReady([int]$TimeoutSec = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $n = 0
    while ((Get-Date) -lt $deadline) {
        [System.Windows.Forms.Application]::DoEvents()
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        $n++
        if (($n % 10) -eq 0) {
            Write-Log "still waiting for :8765 ..." "Gray"
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-StudioVersionLabel {
    $vf = Join-Path $Root "web\STUDIO_VERSION"
    if (-not (Test-Path $vf)) { return "?" }
    $lines = Get-Content $vf -ErrorAction SilentlyContinue
    if (-not $lines -or $lines.Count -lt 1) { return "?" }
    $build = $lines[0].Trim()
    $sha = "dev"
    if ($lines.Count -gt 1 -and $lines[1].Trim()) {
        $raw = $lines[1].Trim()
        $sha = if ($raw.Length -gt 7) { $raw.Substring(0, 7) } else { $raw }
    }
    if ($sha -eq "dev") { return "v$build" }
    return "v$build · $sha"
}

function Get-BuiltStudioVersionLabel {
    $idx = Join-Path $Root "web\out\index.html"
    if (-not (Test-Path $idx)) { return $null }
    try {
        $text = Get-Content $idx -Raw -ErrorAction Stop
    } catch {
        return $null
    }
    if ($text -match 'v(\d+)\s*[·\.]\s*([0-9a-fA-F]{4,})') {
        $sha = $Matches[2]
        if ($sha.Length -gt 7) { $sha = $sha.Substring(0, 7) }
        return "v$($Matches[1]) · $sha"
    }
    if ($text -match 'v(\d+)') {
        return "v$($Matches[1])"
    }
    return $null
}

function Get-StudioVersionBuildNumber {
    $vf = Join-Path $Root "web\STUDIO_VERSION"
    if (-not (Test-Path $vf)) { return 0 }
    $line = (Get-Content $vf -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($line -match '^\s*(\d+)') { return [int]$Matches[1] }
    return 0
}

function Get-BuiltStudioVersionBuildNumber {
    $built = Get-BuiltStudioVersionLabel
    if (-not $built) { return 0 }
    if ($built -match '^v(\d+)') { return [int]$Matches[1] }
    return 0
}

function Test-BuiltUiMatchesVersionFile {
    if (-not (Test-WebUiBuilt)) { return $false }
    $fileBuild = Get-StudioVersionBuildNumber
    $outBuild = Get-BuiltStudioVersionBuildNumber
    if ($outBuild -le 0) {
        Write-Log "web/out собран, но номер версии не найден" "DarkRed"
        return $false
    }
    if ($fileBuild -ne $outBuild) {
        Write-Log "UI устарел: STUDIO_VERSION=v$fileBuild, web/out=v$outBuild" "DarkRed"
        return $false
    }
    return $true
}

function Test-WebUiBuilt {
    return Test-Path (Join-Path $Root "web\out\index.html")
}

function Warn-WebUiMissing {
    if (Test-WebUiBuilt) { return $true }
    Write-Log "web/out/index.html missing - run button 5 Update all or 6 Build Web UI" "DarkOrange"
    return $false
}

function Test-WebBuildStale {
    $out = Join-Path $Root "web\out\index.html"
    if (-not (Test-Path $out)) { return $true }
    $outTime = (Get-Item $out).LastWriteTimeUtc
    $verFile = Join-Path $Root "web\STUDIO_VERSION"
    if ((Test-Path $verFile) -and (Get-Item $verFile).LastWriteTimeUtc -gt $outTime) {
        return $true
    }
    $srcRoot = Join-Path $Root "web\src"
    if (Test-Path $srcRoot) {
        $newestSrc = Get-ChildItem $srcRoot -Recurse -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
        if ($newestSrc -and $newestSrc.LastWriteTimeUtc -gt $outTime) {
            return $true
        }
    }
    return $false
}

function Ensure-WebBuilt {
    if ((Test-WebUiBuilt) -and (-not (Test-WebBuildStale)) -and (Test-BuiltUiMatchesVersionFile)) {
        return $true
    }
    Write-Log "Web UI missing or stale — rebuilding..." "DarkOrange"
    return Build-WebUiFromSources
}

function Start-BackendWindow {
    $cmd = Join-Path $Root "start-backend.cmd"
    if (Test-Path $cmd) {
        Write-Log "Starting backend: start-backend.cmd (new window)" "Gray"
        Start-Process -FilePath $cmd -WorkingDirectory $Root
        return
    }
    Write-Log "Starting backend: run-backend.ps1 (new window)" "Gray"
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-NoProfile", "-File",
        (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root -WindowStyle Normal
}

function Copy-LauncherLogs {
    if (-not $script:LogBox) { return }
    $text = if ($script:LogBox.SelectionLength -gt 0) {
        $script:LogBox.SelectedText
    } else {
        $script:LogBox.Text
    }
    if ([string]::IsNullOrWhiteSpace($text)) {
        [System.Windows.Forms.MessageBox]::Show(
            "Log is empty.",
            "Copy logs",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        return
    }
    try {
        [System.Windows.Forms.Clipboard]::Clear()
        [System.Windows.Forms.Clipboard]::SetText($text)
        [System.Windows.Forms.MessageBox]::Show(
            "Copied to clipboard.`n`nBackup file:`n$($script:LauncherLogFile)",
            "Copy logs",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            "Clipboard failed: $($_.Exception.Message)`n`nOpen log file instead:`n$($script:LauncherLogFile)",
            "Copy logs",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
    }
}

function Open-LauncherLogFile {
    if (-not (Test-Path $script:LauncherLogFile)) {
        Write-Log "Log file not created yet" "DarkOrange"
        return
    }
    Start-Process notepad $script:LauncherLogFile
}

function Invoke-Cmd([string]$Label, [scriptblock]$Block) {
    Write-Log "> $Label" "DarkBlue"
    try {
        & $Block
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            throw "exit code $LASTEXITCODE"
        }
        Write-Log "OK $Label" "DarkGreen"
        return $true
    }
    catch {
        Write-Log "FAIL $Label`: $($_.Exception.Message)" "DarkRed"
        return $false
    }
}

function Invoke-ExternalLog([string]$Label, [string]$FileName, [string[]]$ArgList) {
    Write-Log "> $Label" "DarkBlue"
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FileName
        $psi.Arguments = ($ArgList -join " ")
        $psi.WorkingDirectory = $Root
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        while (-not $p.StandardOutput.EndOfStream) {
            $line = $p.StandardOutput.ReadLine()
            if ($line) { Write-Log $line "Gray" }
        }
        while (-not $p.StandardError.EndOfStream) {
            $line = $p.StandardError.ReadLine()
            if ($line) { Write-Log $line "DarkOrange" }
        }
        $p.WaitForExit()
        if ($p.ExitCode -ne 0) { throw "exit code $($p.ExitCode)" }
        Write-Log "OK $Label" "DarkGreen"
        return $true
    }
    catch {
        Write-Log "FAIL $Label`: $($_.Exception.Message)" "DarkRed"
        return $false
    }
}

function Get-VenvPython {
    $p = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $p) { return $p }
    return $null
}

function Test-Installed {
    return (Test-Path (Get-VenvPython)) -and (Test-Path (Join-Path $Root ".env"))
}

function Get-GitBranch {
    try {
        $b = git -C $Root rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return $b.Trim() }
    } catch { }
    return "?"
}

function Get-GitHead {
    try {
        $h = git -C $Root rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return $h.Trim() }
    } catch { }
    return "?"
}

function Update-StatusLabel {
    if (-not $script:StatusLbl) { return }
    $installed = Test-Installed
    $branch = Get-GitBranch
    $uiVer = Get-StudioVersionLabel
    $webBuilt = Test-Path (Join-Path $Root "web\out\index.html")
    $webStale = Test-WebBuildStale
    $status = if ($installed) { "Installed" } else { "Need install" }
    if ($webBuilt -and $webStale) {
        $web = "UI stale - rebuild"
    } elseif ($webBuilt) {
        $web = "UI $uiVer"
    } else {
        $web = "UI not built"
    }
    $script:StatusLbl.Text = "Status: $status | Git: $branch | $web"
    $script:StatusLbl.ForeColor = if ($installed) {
        [System.Drawing.Color]::DarkGreen
    } else {
        [System.Drawing.Color]::DarkOrange
    }
}

function Sync-ProjectFromGit {
    $branch = $script:StudioUpdateBranch
    $script:LastGitBeforeHead = Get-GitHead
    $beforeHead = $script:LastGitBeforeHead
    Write-Log "Git update -> origin/$branch (was: $(Get-GitBranch) $beforeHead) | UI file=$(Get-StudioVersionLabel)" "Gray"

    $dirty = git -C $Root status --porcelain 2>$null
    if ($dirty) {
        Write-Log "Uncommitted files — auto-stash (restore: git stash list)" "DarkOrange"
        if (-not (Invoke-GitLogged "git stash (auto)" @("stash", "push", "-u", "-m", "studio-update-auto"))) {
            return $false
        }
    }

    if (-not (Invoke-GitLogged "git fetch origin $branch" @("fetch", "origin", $branch))) {
        return $false
    }
    if (-not (Invoke-GitLogged "git checkout $branch" @("checkout", "-B", $branch, "origin/$branch"))) {
        return $false
    }
    $localH = (git -C $Root rev-parse HEAD 2>$null)
    $remoteH = (git -C $Root rev-parse "origin/$branch" 2>$null)
    if ($localH -and $remoteH -and ($localH.Trim() -ne $remoteH.Trim())) {
        if (-not (Invoke-GitLogged "git reset --hard origin/$branch" @("reset", "--hard", "origin/$branch"))) {
            return $false
        }
    }

    $afterHead = Get-GitHead
    $script:LastGitAfterHead = $afterHead
    if ($beforeHead -eq $afterHead) {
        Write-Log "Git synced ($afterHead) | STUDIO_VERSION=$(Get-StudioVersionLabel)" "Gray"
    } else {
        Write-Log "Git: $beforeHead -> $afterHead | STUDIO_VERSION=$(Get-StudioVersionLabel)" "DarkGreen"
    }
    if (Test-LauncherScriptChanged $beforeHead $afterHead) {
        Write-Log "Launcher updated — window will reopen after this run" "DarkOrange"
        $script:LauncherNeedsRestart = $true
    }
    Repair-CriticalScriptsFromGit | Out-Null
    return $true
}

function Test-PrebuiltUiFromGit {
    if (-not (Test-WebUiBuilt)) { return $false }
    if (Test-BuiltUiMatchesVersionFile) {
        Write-Log "Prebuilt UI from git: v$(Get-StudioVersionBuildNumber) (no npm needed)" "DarkGreen"
        return $true
    }
    return $false
}

function Build-WebUiFromSources {
  param([switch]$Force)
    if ((-not $Force) -and (Test-PrebuiltUiFromGit)) {
        return $true
    }
    Remove-WebBuildArtifacts
    $npm = Get-NpmCmd
    if (-not $npm) {
        Write-Log "Node.js/npm not found. Button 1 Full install, or install Node.js LTS" "DarkRed"
        return $false
    }
    Write-Log "npm: $npm" "Gray"
    $webDir = Join-Path $Root "web"
    if (-not (Invoke-NpmLogged "npm install (web)" $npm @("install") $webDir)) {
        return $false
    }
    if (-not (Invoke-NpmLogged "npm run build (web/out)" $npm @("run", "build") $webDir)) {
        return $false
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-Log "FAIL web/out/index.html missing after build" "DarkRed"
        return $false
    }
    $built = Get-BuiltStudioVersionLabel
    Write-Log "UI built: file=v$(Get-StudioVersionBuildNumber) web/out=$built" "DarkGreen"
    if (-not (Test-BuiltUiMatchesVersionFile)) {
        return $false
    }
    return $true
}

function Sync-PythonAndWeb {
    param(
        [switch]$AlwaysBuildUi
    )
    $py = Get-VenvPython
    if (-not $py) {
        Write-Log "No venv - run button 1 Full install" "DarkOrange"
        return $false
    }
    if (Test-Path $script:StudioCorePath) {
        if (-not (Invoke-StudioPipInstall $Root)) { return $false }
    } else {
        $pipSpec = (Resolve-Path -LiteralPath $Root).Path + '[dev]'
        if (-not (Invoke-PythonLogged "pip install -e" $py @("-m", "pip", "install", "-e", $pipSpec))) {
            return $false
        }
    }
    if (Test-PrebuiltUiFromGit) {
        return $true
    }
    $needBuild = $AlwaysBuildUi -or (Test-WebBuildStale) -or (-not (Test-WebUiBuilt)) `
        -or (-not (Test-BuiltUiMatchesVersionFile))
    if (-not $needBuild) {
        Write-Log "Web UI OK: $(Get-BuiltStudioVersionLabel)" "Gray"
        return $true
    }
    return Build-WebUiFromSources -Force
}

function Invoke-StudioOneClickUpdate {
    Write-Log "=== One-click update ($($script:LAUNCHER_UPDATE_ID)) ===" "DarkBlue"
    Stop-AllBackendProcesses
    Unlock-SharedBackendLog | Out-Null

    if (-not (Test-Installed)) {
        $ok = Invoke-ExternalLog "Install" "powershell.exe" @(
            "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
        )
        if (-not $ok) { return $false }
    }

    if (Test-Path $script:StudioCorePath) {
        if (-not (Invoke-StudioUpdateOnly $Root)) {
            Write-Log "Update FAILED — run UPDATE-STUDIO.cmd (console log)" "DarkRed"
            return $false
        }
    } else {
        if (-not (Sync-ProjectFromGit)) {
            Write-Log "Git update failed — check internet" "DarkRed"
            return $false
        }
        if (-not (Sync-PythonAndWeb -AlwaysBuildUi)) {
            Write-Log "Python/UI update failed" "DarkRed"
            return $false
        }
    }

    if (-not (Test-RunBackendScriptCurrent)) {
        Repair-CriticalScriptsFromGit | Out-Null
    }
    Update-StatusLabel
    return $true
}

function Show-StudioVersionFromApi {
    try {
        $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
        Write-Log "API studio-version: $($sv.label) backend=$($sv.backend_attach)" "DarkGreen"
    } catch {
        Write-Log "API studio-version недоступен (бэкенд ещё стартует?)" "DarkOrange"
    }
    Write-Log "Файл web/STUDIO_VERSION: $(Get-StudioVersionLabel)" "Gray"
    $built = Get-BuiltStudioVersionLabel
    if ($built) {
        Write-Log "Сборка web/out (бейдж в браузере): $built" "Gray"
    } else {
        Write-Log "web/out не собран — в браузере будет старая версия (v102?)" "DarkRed"
    }
}

function Do-FullUpdate {
    if (Invoke-StudioOneClickUpdate) {
        Write-Log "Update done. Press * Update + Start or 2 Start Studio" "DarkGreen"
    }
}

function Do-UpdateAndRun {
    $script:LauncherNeedsRestart = $false
    if (-not (Invoke-StudioOneClickUpdate)) {
        return
    }

    Stop-AllBackendProcesses
    Unlock-SharedBackendLog | Out-Null

    $started = Start-StudioBackend
    if (-not $started) {
        Show-BackendFailureDiagnostics
    }

    if ($script:LauncherNeedsRestart) {
        Write-Log "Launcher updated from git — reopening window..." "DarkOrange"
        Start-Sleep -Seconds 1
        Restart-LauncherGui
    }
}

function Do-QuickStart {
    Do-UpdateAndRun
}

function Do-Install {
    Invoke-ExternalLog "Install" "powershell.exe" @(
        "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
    )
    Update-StatusLabel
}

function Start-StudioBackend {
    if (-not (Get-VenvPython)) {
        Write-Log "No .venv — button 1 Full install" "DarkRed"
        return $false
    }
    if (-not (Test-WebUiBuilt)) {
        Write-Log "web/out missing — building UI first..." "DarkOrange"
        if (-not (Ensure-WebBuilt)) {
            Write-Log "UI build failed — API may still work, browser UI incomplete" "DarkOrange"
        }
    }
    Stop-AllBackendProcesses
    Unlock-SharedBackendLog | Out-Null
    if (-not (Test-Port8765Free)) {
        Write-Log "Port 8765 busy — stopping again..." "DarkOrange"
        Stop-AllBackendProcesses
        Start-Sleep -Seconds 3
    }
    Test-RunBackendScriptCurrent | Out-Null
    Start-BackendWindow
    Write-Log "Waiting for http://127.0.0.1:8765 (120s) ..." "Gray"
    if (Test-BackendReady -TimeoutSec 120) {
        Show-StudioVersionFromApi
        Start-Process "http://127.0.0.1:8765"
        Write-Log "Studio OK — keep backend window OPEN" "DarkGreen"
        return $true
    }
    Write-Log "No connection to :8765" "DarkRed"
    Show-BackendFailureDiagnostics
    return $false
}

function Do-StartStudio {
    Start-StudioBackend | Out-Null
}

function Do-StartTelegram {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start.ps1")
    ) -WorkingDirectory $Root
    Write-Log "Telegram + Studio in new window" "DarkGreen"
}

function Do-Stop {
    Stop-AllBackendProcesses
    Write-Log "Stop done" "DarkGreen"
}

function Do-BuildWeb {
    if (Build-WebUiFromSources) {
        Update-StatusLabel
        Write-Log "Web UI rebuilt" "DarkGreen"
    }
}

function Do-DevUi {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command", "Set-Location '$Root\web'; npm run dev"
    )
}

function Do-Tests {
    $py = Get-VenvPython
    if (-not $py) { throw "Run install first" }
    & $py -m pytest (Join-Path $Root "tests") -q --tb=short 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-Lint {
    ruff check $Root 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-Seed {
    $py = Get-VenvPython
    if (-not $py) { throw "Run install first" }
    & $py -m app.seed_pilot 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-OpenEnv {
    $envPath = Join-Path $Root ".env"
    if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root ".env.example") $envPath }
    Start-Process notepad $envPath
}

function Do-OpenData {
    $d = Join-Path $Root "data"
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
    Start-Process explorer $d
}

function Do-ResetDb {
    $db = Join-Path $Root "data\state.db"
    if (Test-Path $db) {
        $ans = [System.Windows.Forms.MessageBox]::Show(
            "Delete state.db?", "Confirm", "YesNo", "Warning"
        )
        if ($ans -eq "Yes") { Remove-Item $db -Force; Write-Log "state.db deleted" "DarkOrange" }
    } else {
        Write-Log "state.db not found" "Gray"
    }
}

function Do-ChromeHint {
    Write-Log "chrome.exe --remote-debugging-port=29229 --user-data-dir=%TEMP%\vp-chrome" "DarkMagenta"
}

function Do-OpenBrowser {
    if (-not (Test-BackendReady -TimeoutSec 3)) {
        Write-Log "Backend not running - click 2 Start Studio first" "DarkOrange"
        return
    }
    Start-Process "http://127.0.0.1:8765"
}

$commands = @(
    @{ Label = "* Update + Start"; Tip = "git+pip+npm (full) then :8765 — only button you need daily"; Fn = { Do-UpdateAndRun } }
    @{ Label = "1. Full install"; Tip = "Python, venv, FFmpeg, Node, .env"; Fn = { Do-Install } }
    @{ Label = "2. Start Studio"; Tip = "http://127.0.0.1:8765"; Fn = { Do-StartStudio } }
    @{ Label = "3. Telegram mode"; Tip = "Bot + API (token in .env)"; Fn = { Do-StartTelegram } }
    @{ Label = "4. Stop"; Tip = "Stop python for this project"; Fn = { Do-Stop } }
    @{ Label = "5. Update all"; Tip = "Same as * Update without starting Studio"; Fn = { Do-FullUpdate } }
    @{ Label = "6. Build Web UI"; Tip = "Rebuild web/out only (no git)"; Fn = { Do-BuildWeb } }
    @{ Label = "7. Dev UI :3000"; Tip = "npm run dev"; Fn = { Do-DevUi } }
    @{ Label = "8. Tests"; Tip = "pytest"; Fn = { Do-Tests } }
    @{ Label = "9. Lint"; Tip = "ruff check"; Fn = { Do-Lint } }
    @{ Label = "10. Seed demo"; Tip = "Demo project"; Fn = { Do-Seed } }
    @{ Label = "11. Open .env"; Tip = "Settings file"; Fn = { Do-OpenEnv } }
    @{ Label = "12. Open data/"; Tip = "Database folder"; Fn = { Do-OpenData } }
    @{ Label = "13. Chrome CDP"; Tip = "Port 29229 hint"; Fn = { Do-ChromeHint } }
    @{ Label = "14. Reset DB"; Tip = "Delete state.db"; Fn = { Do-ResetDb } }
    @{ Label = "15. Open browser"; Tip = "Open Studio URL"; Fn = { Do-OpenBrowser } }
)

$form = New-Object System.Windows.Forms.Form
$script:MainForm = $form
$form.Text = "Video Pipeline Studio $(Get-StudioVersionLabel) git:$(Get-GitHead)"
$form.Size = New-Object System.Drawing.Size(740, 720)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Video Pipeline - install, update, run"
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(16, 12)
$title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$pathLbl = New-Object System.Windows.Forms.Label
$pathLbl.Text = "Folder: $Root"
$pathLbl.AutoSize = $true
$pathLbl.Location = New-Object System.Drawing.Point(16, 36)
$pathLbl.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($pathLbl)

$StatusLbl = New-Object System.Windows.Forms.Label
$StatusLbl.AutoSize = $true
$StatusLbl.Location = New-Object System.Drawing.Point(16, 54)
$form.Controls.Add($StatusLbl)
$script:StatusLbl = $StatusLbl

$hintLbl = New-Object System.Windows.Forms.Label
$hintLbl.Text = "Daily: * Update + Start. If broken: double-click UPDATE-STUDIO.cmd in repo root."
$hintLbl.AutoSize = $true
$hintLbl.Location = New-Object System.Drawing.Point(16, 72)
$hintLbl.ForeColor = [System.Drawing.Color]::DimGray
$hintLbl.MaximumSize = New-Object System.Drawing.Size(700, 0)
$form.Controls.Add($hintLbl)

$LogBox = New-Object System.Windows.Forms.RichTextBox
$LogBox.Location = New-Object System.Drawing.Point(16, 418)
$LogBox.Size = New-Object System.Drawing.Size(700, 212)
$LogBox.ReadOnly = $true
$LogBox.ShortcutsEnabled = $true
$LogBox.HideSelection = $false
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 252)
$logMenu = New-Object System.Windows.Forms.ContextMenuStrip
$copyMenuItem = $logMenu.Items.Add("Copy (Ctrl+C)")
$copyMenuItem.Add_Click({ Copy-LauncherLogs })
$LogBox.ContextMenuStrip = $logMenu
$LogBox.Add_KeyDown({
    param($sender, $e)
    if ($e.Control -and $e.KeyCode -eq [System.Windows.Forms.Keys]::C) {
        Copy-LauncherLogs
        $e.Handled = $true
    }
})
$form.Controls.Add($LogBox)
$script:LogBox = $LogBox

$copyLogsBtn = New-Object System.Windows.Forms.Button
$copyLogsBtn.Text = "Copy logs"
$copyLogsBtn.Size = New-Object System.Drawing.Size(88, 26)
$copyLogsBtn.Location = New-Object System.Drawing.Point(16, 392)
$copyLogsBtn.Add_Click({ Copy-LauncherLogs })
$form.Controls.Add($copyLogsBtn)

$openLogBtn = New-Object System.Windows.Forms.Button
$openLogBtn.Text = "Open log file"
$openLogBtn.Size = New-Object System.Drawing.Size(88, 26)
$openLogBtn.Location = New-Object System.Drawing.Point(110, 392)
$openLogBtn.Add_Click({ Open-LauncherLogFile })
$form.Controls.Add($openLogBtn)

$logHintLbl = New-Object System.Windows.Forms.Label
$logHintLbl.Text = "Ctrl+C / Copy logs / data\launcher.log — backend must stay open on :8765"
$logHintLbl.AutoSize = $true
$logHintLbl.Location = New-Object System.Drawing.Point(206, 396)
$logHintLbl.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($logHintLbl)

$tip = New-Object System.Windows.Forms.ToolTip
$y = 98
$col = 0
for ($i = 0; $i -lt $commands.Count; $i++) {
    $cmd = $commands[$i]
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $cmd.Label
    $btn.Size = New-Object System.Drawing.Size(228, 36)
    $btn.Location = New-Object System.Drawing.Point((16 + $col * 234), $y)
    $fn = $cmd.Fn
    $actionName = $cmd.Label
    $btn.Add_Click({
        param($sender, $e)
        $sender.Enabled = $false
        try {
            Invoke-ButtonAction $actionName $fn
        } finally {
            $sender.Enabled = $true
        }
    }.GetNewClosure())
    $tip.SetToolTip($btn, $cmd.Tip)
    $form.Controls.Add($btn)
    $col++
    if ($col -ge 3) { $col = 0; $y += 44 }
}

Update-StatusLabel
Write-Log "Ready. Use * Update + Start only ($($script:LAUNCHER_UPDATE_ID))" "DarkGreen"
[void]$form.ShowDialog()
