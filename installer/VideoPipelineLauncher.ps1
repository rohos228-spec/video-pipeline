# Video Pipeline Studio — GUI launcher & installer for Windows
# Run: powershell -ExecutionPolicy Bypass -File .\installer\VideoPipelineLauncher.ps1
#
# One-click install + top commands without typing in PowerShell.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    $Root = Get-Location
}
Set-Location $Root

function Write-Log([string]$Text, [string]$Color = "Black") {
    $script:LogBox.SelectionColor = $Color
    $script:LogBox.AppendText("$(Get-Date -Format 'HH:mm:ss')  $Text`r`n")
    $script:LogBox.ScrollToCaret()
}

function Invoke-Cmd([string]$Label, [scriptblock]$Block) {
    Write-Log "▶ $Label" "DarkBlue"
    try {
        & $Block
        Write-Log "✓ $Label" "DarkGreen"
    }
    catch {
        Write-Log "✗ $Label`: $($_.Exception.Message)" "DarkRed"
    }
}

function Get-VenvPython {
    $p = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $p) { return $p }
    return $null
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Video Pipeline Studio"
$form.Size = New-Object System.Drawing.Size(720, 640)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Video Pipeline — установка и управление"
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(16, 12)
$title.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$pathLbl = New-Object System.Windows.Forms.Label
$pathLbl.Text = "Папка: $Root"
$pathLbl.AutoSize = $true
$pathLbl.Location = New-Object System.Drawing.Point(16, 38)
$pathLbl.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($pathLbl)

$LogBox = New-Object System.Windows.Forms.RichTextBox
$LogBox.Location = New-Object System.Drawing.Point(16, 380)
$LogBox.Size = New-Object System.Drawing.Size(680, 210)
$LogBox.ReadOnly = $true
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 252)
$form.Controls.Add($LogBox)

$commands = @(
    @{ Label = "1. Полная установка"; Tip = "Python, venv, зависимости, .env"; Action = {
        Invoke-Cmd "Установка" { & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "install.ps1") -NonInteractive }
    }},
    @{ Label = "2. Запустить Studio"; Tip = "API :8765 без Telegram"; Action = {
        Invoke-Cmd "Studio" { Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start-studio.ps1") -WorkingDirectory $Root }
    }},
    @{ Label = "3. Запустить с Telegram"; Tip = "Полный бот + API"; Action = {
        Invoke-Cmd "Main" { Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "start.ps1") -WorkingDirectory $Root }
    }},
    @{ Label = "4. Остановить процессы"; Tip = "python app.main в этой папке"; Action = {
        Invoke-Cmd "Stop" {
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                Where-Object { $_.CommandLine -like "*$Root*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }},
    @{ Label = "5. Обновить из Git"; Tip = "git pull + pip install"; Action = {
        Invoke-Cmd "Git pull" { git -C $Root pull }
        $py = Get-VenvPython
        if ($py) { Invoke-Cmd "pip install" { & $py -m pip install -e ".[dev]" } }
    }},
    @{ Label = "6. Собрать Web UI"; Tip = "npm run build в web/"; Action = {
        Invoke-Cmd "npm build" {
            Push-Location (Join-Path $Root "web")
            npm install
            npm run build
            Pop-Location
        }
    }},
    @{ Label = "7. Dev UI (:3000)"; Tip = "npm run dev"; Action = {
        Invoke-Cmd "npm dev" {
            Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$Root\web'; npm run dev" -WorkingDirectory $Root
        }
    }},
    @{ Label = "8. Тесты pytest"; Tip = "python -m pytest tests/"; Action = {
        $py = Get-VenvPython
        if (-not $py) { throw "Сначала выполните установку" }
        Invoke-Cmd "pytest" { & $py -m pytest (Join-Path $Root "tests") -v --tb=short }
    }},
    @{ Label = "9. Lint (ruff)"; Tip = "ruff check ."; Action = {
        Invoke-Cmd "ruff" { ruff check $Root }
    }},
    @{ Label = "10. Seed pilot"; Tip = "Демо-проект"; Action = {
        $py = Get-VenvPython
        if (-not $py) { throw "Сначала выполните установку" }
        Invoke-Cmd "seed" { & $py -m app.seed_pilot }
    }},
    @{ Label = "11. Открыть .env"; Tip = "Настройки токенов"; Action = {
        $envPath = Join-Path $Root ".env"
        if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root ".env.example") $envPath }
        Invoke-Cmd "notepad .env" { notepad $envPath }
    }},
    @{ Label = "12. Открыть папку data"; Tip = "SQLite, проекты"; Action = {
        $d = Join-Path $Root "data"
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
        Invoke-Cmd "explorer data" { explorer $d }
    }},
    @{ Label = "13. Chrome CDP подсказка"; Tip = "Порт 29229 для GPT"; Action = {
        Write-Log "Chrome: запустите с --remote-debugging-port=29229" "DarkMagenta"
        Write-Log "  chrome.exe --remote-debugging-port=29229 --user-data-dir=%TEMP%\vp-chrome" "Gray"
    }},
    @{ Label = "14. Сброс БД"; Tip = "Удалить data/state.db"; Action = {
        $db = Join-Path $Root "data\state.db"
        if (Test-Path $db) {
            $ans = [System.Windows.Forms.MessageBox]::Show("Удалить state.db?", "Подтверждение", "YesNo", "Warning")
            if ($ans -eq "Yes") { Remove-Item $db -Force; Write-Log "state.db удалён" "DarkOrange" }
        } else { Write-Log "state.db не найден" "Gray" }
    }},
    @{ Label = "15. Открыть Studio в браузере"; Tip = "http://127.0.0.1:8765"; Action = {
        Invoke-Cmd "browser" { Start-Process "http://127.0.0.1:8765" }
    }}
)

$y = 64
$col = 0
foreach ($cmd in $commands) {
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $cmd.Label
    $btn.Size = New-Object System.Drawing.Size(220, 36)
    $btn.Location = New-Object System.Drawing.Point((16 + $col * 228), $y)
    $btn.Add_Click($cmd.Action)
    $tip = New-Object System.Windows.Forms.ToolTip
    $tip.SetToolTip($btn, $cmd.Tip)
    $form.Controls.Add($btn)
    $col++
    if ($col -ge 3) { $col = 0; $y += 44 }
}

Write-Log "Готов. Нажмите «1. Полная установка» на новом компьютере." "DarkGreen"
[void]$form.ShowDialog()
