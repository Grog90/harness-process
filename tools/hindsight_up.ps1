# Поднять Hindsight: Docker Desktop -> демон -> контейнер -> ожидание /health.
#
#   pwsh -File tools\hindsight_up.ps1              # поднять и дождаться ответа API
#   pwsh -File tools\hindsight_up.ps1 -TimeoutSeconds 60
#
# Зачем отдельный скрипт: у Docker Desktop флаг AutoStart в settings-store.json может быть
# выключен, и тогда контейнер с restart=unless-stopped не поднимается — он ждёт демон,
# которого нет. Скрипт вызывается задачей планировщика при входе и вручную.
param(
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
$DockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$Container = "hindsight"
$HealthUrl = "http://127.0.0.1:8888/health"
$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)

function Test-Health {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-Daemon {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (Test-Health) {
    "hindsight уже отвечает: $HealthUrl"
    exit 0
}

if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path -LiteralPath $DockerExe)) {
        "не найден $DockerExe"
        exit 1
    }
    "запускаю Docker Desktop..."
    Start-Process -FilePath $DockerExe | Out-Null
} else {
    "Docker Desktop уже запущен"
}

$daemon = $false
while ((Get-Date) -lt $Deadline) {
    if (Test-Daemon) { $daemon = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $daemon) {
    "демон Docker не поднялся за $TimeoutSeconds с"
    exit 1
}
"демон готов"

$state = (docker inspect $Container --format '{{.State.Status}}' 2>$null)
if ($state -ne "running") {
    "контейнер в состоянии '$state' — стартую"
    docker start $Container | Out-Null
}

while ((Get-Date) -lt $Deadline) {
    if (Test-Health) {
        "hindsight здоров: $HealthUrl"
        exit 0
    }
    Start-Sleep -Seconds 3
}

"контейнер запущен, но /health не ответил за $TimeoutSeconds с — смотри docker logs $Container"
exit 1
