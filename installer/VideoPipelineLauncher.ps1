# Video Pipeline Studio — GUI: установка, обновление, запуск
# Двойной клик: VideoPipelineStudio.cmd в корне репозитория

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = (Get-Location).Path
}
Set-Location $Root

$script:LogBox = $null
$script:StatusLbl = $null

function Write-Log([string]$Text, [string]$Color = "Black") {
    if (-not $script:LogBox) { return }
    $script:LogBox.SelectionColor = $Color
    $script:LogBox.AppendText("$(Get-Date -Format 'HH:mm:ss')  $Text`r`n")
    $script:LogBox.ScrollToCaret()
}

function Invoke-Cmd([string]$Label, [scriptblock]$Block) {
    Write-Log "▶ $Label" "DarkBlue"
    try {
        & $Block
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            throw "код выхода $LASTEXITCODE"
        }
        Write-Log "✓ $Label" "DarkGreen"
        return $true
    }
    catch {
        Write-Log "✗ $Label`: $($_.Exception.Message)" "DarkRed"
        return $false
    }
}

function Invoke-ExternalLog([string]$Label, [string]$FileName, [string[]]$ArgList) {
    Write-Log "▶ $Label" "DarkBlue"
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
        if ($p.ExitCode -ne 0) { throw "код выхода $($p.ExitCode)" }
        Write-Log "✓ $Label" "DarkGreen"
        return $true
    }
    catch {
        Write-Log "✗ $Label`: $($_.Exception.Message)" "DarkRed"
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

function Update-StatusLabel {
    if (-not $script:StatusLbl) { return }
    $installed = Test-Installed
    $branch = Get-GitBranch
    $webBuilt = Test-Path (Join-Path $Root "web\out\index.html")
    $status = if ($installed) { "Установлено" } else { "Нужна установка" }
    $web = if ($webBuilt) { "UI собран" } else { "UI не собран" }
    $script:StatusLbl.Text = "Статус: $status · Git: $branch · $web"
    $script:StatusLbl.ForeColor = if ($installed) {
        [System.Drawing.Color]::DarkGreen
    } else {
        [System.Drawing.Color]::DarkOrange
    }
}

function Do-FullUpdate {
    $branch = Get-GitBranch
    if ($branch -eq "?") {
        Write-Log "Git не найден — пропускаю pull" "DarkOrange"
    } else {
        Invoke-Cmd "git pull origin $branch" {
            git -C $Root pull origin $branch 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        }
    }
    $py = Get-VenvPython
    if (-not $py) {
        Write-Log "venv нет — сначала «1. Полная установка»" "DarkOrange"
        return
    }
    Invoke-Cmd "pip install -e .[dev]" {
        & $py -m pip install -e ".[dev]" 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    }
    Invoke-Cmd "npm install + build" {
        Push-Location (Join-Path $Root "web")
        npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
        Pop-Location
    }
    Update-StatusLabel
    Write-Log "Готово. Перезапустите Studio: «4. Остановить» → «2. Запустить»" "DarkGreen"
}

function Do-QuickStart {
    if (-not (Test-Installed)) {
        $ok = Invoke-ExternalLog "Установка" "powershell.exe" @(
            "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
        )
        if (-not $ok) { return }
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Invoke-Cmd "Сборка UI" {
            Push-Location (Join-Path $Root "web")
            npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
            Pop-Location
        }
    }
    Update-StatusLabel
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8765"
    Write-Log "Studio запущена. Это меню можно оставить открытым." "DarkGreen"
}

function Do-Install {
    Invoke-ExternalLog "Установка" "powershell.exe" @(
        "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$(Join-Path $Root 'install.ps1')`"", "-NonInteractive"
    )
    Update-StatusLabel
}

function Do-StartStudio {
    if (-not (Get-VenvPython)) { throw "Сначала установка (кнопка 1)" }
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Write-Log "Studio запущена в новом окне" "DarkGreen"
}

function Do-StartTelegram {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start.ps1")
    ) -WorkingDirectory $Root
    Write-Log "Telegram + Studio в новом окне" "DarkGreen"
}

function Do-Stop {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Root*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Log "Процессы остановлены" "DarkGreen"
}

function Do-BuildWeb {
    Push-Location (Join-Path $Root "web")
    npm install 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    npm run build 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
    Pop-Location
    Update-StatusLabel
}

function Do-DevUi {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command", "Set-Location '$Root\web'; npm run dev"
    )
}

function Do-Tests {
    $py = Get-VenvPython
    if (-not $py) { throw "Сначала установка" }
    & $py -m pytest (Join-Path $Root "tests") -q --tb=short 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-Lint {
    ruff check $Root 2>&1 | ForEach-Object { Write-Log "$_" "Gray" }
}

function Do-Seed {
    $py = Get-VenvPython
    if (-not $py) { throw "Сначала установка" }
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
            "Удалить state.db?", "Подтверждение", "YesNo", "Warning"
        )
        if ($ans -eq "Yes") { Remove-Item $db -Force; Write-Log "state.db удалён" "DarkOrange" }
    } else {
        Write-Log "state.db не найден" "Gray"
    }
}

$commands = @(
    @{ Label = "★ Быстрый старт"; Tip = "Установка + UI + Studio + браузер"; Fn = { Do-QuickStart } }
    @{ Label = "1. Полная установка"; Tip = "Python, venv, FFmpeg, Node, .env"; Fn = { Do-Install } }
    @{ Label = "2. Запустить Studio"; Tip = "http://127.0.0.1:8765"; Fn = { Do-StartStudio } }
    @{ Label = "3. Telegram + Studio"; Tip = "Нужен токен в .env"; Fn = { Do-StartTelegram } }
    @{ Label = "4. Остановить"; Tip = "Остановить python проекта"; Fn = { Do-Stop } }
    @{ Label = "5. Обновить всё"; Tip = "git pull + pip + npm build"; Fn = { Do-FullUpdate } }
    @{ Label = "6. Собрать Web UI"; Tip = "npm run build"; Fn = { Do-BuildWeb } }
    @{ Label = "7. Dev UI :3000"; Tip = "npm run dev"; Fn = { Do-DevUi } }
    @{ Label = "8. Тесты"; Tip = "pytest"; Fn = { Do-Tests } }
    @{ Label = "9. Lint"; Tip = "ruff"; Fn = { Do-Lint } }
    @{ Label = "10. Seed demo"; Tip = "Демо-проект"; Fn = { Do-Seed } }
    @{ Label = "11. .env"; Tip = "Настройки"; Fn = { Do-OpenEnv } }
    @{ Label = "12. Папка data"; Tip = "БД"; Fn = { Do-OpenData } }
    @{ Label = "13. Chrome CDP"; Tip = "Порт 29229"; Fn = {
        Write-Log "chrome.exe --remote-debugging-port=29229 --user-data-dir=%TEMP%\vp-chrome" "DarkMagenta"
    }}
    @{ Label = "14. Сброс БД"; Tip = "Удалить state.db"; Fn = { Do-ResetDb } }
    @{ Label = "15. Браузер"; Tip = "Открыть Studio"; Fn = { Start-Process "http://127.0.0.1:8765" } }
)

$form = New-Object System.Windows.Forms.Form
$form.Text = "Video Pipeline Studio"
$form.Size = New-Object System.Drawing.Size(740, 680)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Video Pipeline — всё через это меню"
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(16, 12)
$title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$pathLbl = New-Object System.Windows.Forms.Label
$pathLbl.Text = "Папка: $Root"
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
$hintLbl.Text = "Первый раз: «★ Быстрый старт». Обновления: «5. Обновить всё» → «4» → «2»."
$hintLbl.AutoSize = $true
$hintLbl.Location = New-Object System.Drawing.Point(16, 72)
$hintLbl.ForeColor = [System.Drawing.Color]::DimGray
$hintLbl.MaximumSize = New-Object System.Drawing.Size(700, 0)
$form.Controls.Add($hintLbl)

$LogBox = New-Object System.Windows.Forms.RichTextBox
$LogBox.Location = New-Object System.Drawing.Point(16, 400)
$LogBox.Size = New-Object System.Drawing.Size(700, 230)
$LogBox.ReadOnly = $true
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 252)
$form.Controls.Add($LogBox)
$script:LogBox = $LogBox

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
    $btn.Add_Click({
        param($sender, $e)
        try {
            & $fn
            Update-StatusLabel
        } catch {
            Write-Log $_.Exception.Message "DarkRed"
        }
    }.GetNewClosure())
    $tip.SetToolTip($btn, $cmd.Tip)
    $form.Controls.Add($btn)
    $col++
    if ($col -ge 3) { $col = 0; $y += 44 }
}

Update-StatusLabel
Write-Log "Меню готово. Запуск: двойной клик VideoPipelineStudio.cmd" "DarkGreen"
[void]$form.ShowDialog()
