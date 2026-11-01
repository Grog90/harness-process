# Автоматическая отправка фактов в Hindsight.
#
#   pwsh -File tools\push_facts_auto.ps1 -Install   # задача каждые 15 минут (без окна)
#   pwsh -File tools\push_facts_auto.ps1 -Status    # состояние, следующее время, лог
#   pwsh -File tools\push_facts_auto.ps1 -Remove    # снять задачу
#   pwsh -File tools\push_facts_auto.ps1 -Run       # один прогон (так вызывает планировщик)
#
# Задача ставится скрытой (-WindowStyle Hidden + Hidden в настройках): иначе каждые
# 15 минут в интерактивной сессии вспыхивает консольное окно.
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Run
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Root = Split-Path -Parent $Here
$TaskName = "harness-push-facts"
$LogDir = Join-Path $Root ".recall"
$LogPath = Join-Path $LogDir "push.log"
$LockPath = Join-Path $LogDir "push.lock"
$IntervalMinutes = 15
$HindsightHost = "127.0.0.1"
$HindsightPort = 8888

function Write-Log([string]$Text, [switch]$Dedup) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    if ($Dedup -and (Test-Path -LiteralPath $LogPath)) {
        $last = Get-Content -LiteralPath $LogPath -Tail 1
        # строка лога: "2026-10-26 22:16:04  текст" — сравниваем только текст
        if ($last -and (($last -split '\s{2}', 2)[-1]) -eq $Text) { return }
    }
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -LiteralPath $LogPath -Value "$stamp  $Text" -Encoding utf8
}

# Hindsight — локальный демон на 127.0.0.1:8888. Если он не поднят, python всё равно
# завершится отказом, только потратит пару секунд и запишет шум в лог.
function Test-HindsightPort {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync($HindsightHost, $HindsightPort)
        if (-not $connect.Wait(700)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Install-Task {
    $action = 'pwsh -NoProfile -WindowStyle Hidden -File "{0}" -Run' -f $PSCommandPath
    schtasks /Create /TN $TaskName /SC MINUTE /MO $IntervalMinutes /TR $action /F | Out-Null
    # schtasks не умеет скрытость и запрещает запуск на батарее — доводим настройки
    $settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
}

if ($Install) {
    Install-Task
    Write-Log "задача установлена: каждые $IntervalMinutes минут, скрытое окно"
    "установлено: $TaskName (каждые $IntervalMinutes минут, без окна). Лог: $LogPath"
    exit 0
}

if ($Remove) {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Log "задача удалена"
    "удалено: $TaskName"
    exit 0
}

if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        "задача не установлена"
        exit 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    "задача     : {0}" -f $task.State
    "последний  : {0} (код {1})" -f $info.LastRunTime, $info.LastTaskResult
    "следующий  : {0}" -f $info.NextRunTime
    "окно       : {0}" -f $(if ($task.Settings.Hidden) { "скрыто" } else { "ВИДИМО" })
    "hindsight  : {0}" -f $(if (Test-HindsightPort) { "отвечает на ${HindsightHost}:$HindsightPort" } else { "НЕ отвечает на ${HindsightHost}:$HindsightPort" })
    if (Test-Path -LiteralPath $LogPath) {
        "`nпоследние записи:"
        Get-Content -LiteralPath $LogPath -Tail 3
    }
    exit 0
}

if ($Run) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    if (-not (Test-HindsightPort)) {
        Write-Log ("hindsight не отвечает на {0}:{1} — прогон пропущен" -f $HindsightHost, $HindsightPort) -Dedup
        exit 0
    }
    # не запускать второй экземпляр: push идемпотентен, но лишние вызовы LLM ни к чему
    if (Test-Path $LockPath) {
        $age = (Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime
        if ($age.TotalMinutes -lt 30) { exit 0 }
        Remove-Item -LiteralPath $LockPath -Force
    }
    New-Item -ItemType File -Path $LockPath -Force | Out-Null
    try {
        # без этого русский текст из python приезжает в лог мусором (cp866/cp1251)
        $env:PYTHONUTF8 = "1"
        $env:PYTHONIOENCODING = "utf-8"
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $output = & python (Join-Path $Here "push_facts.py") --include-agents 2>&1
        Write-Log (($output | Select-Object -Last 1) -join " ")
        if ($LASTEXITCODE -ne 0) {
            Write-Log ("python завершился с кодом {0}" -f $LASTEXITCODE)
        }
    } catch {
        Write-Log "ОШИБКА: $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

"использование: pwsh -File tools\push_facts_auto.ps1 -Install | -Status | -Remove | -Run"
