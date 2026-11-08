[CmdletBinding()]
param(
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "compose.yaml"
$appUrl = "http://127.0.0.1:8000/"
$healthUrl = "http://127.0.0.1:8000/health"
$dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$logDirectory = Join-Path $env:LOCALAPPDATA "MyAgent\logs"
$serverProcess = $null

function Write-Step([string]$Message) {
    Write-Host "`n[MyAgent] $Message" -ForegroundColor Cyan
}

function Test-DockerReady {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & docker info --format "{{.ServerVersion}}" 2> $null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Wait-Until(
    [scriptblock]$Condition,
    [int]$TimeoutSeconds,
    [int]$IntervalSeconds = 2
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) {
            return $true
        }
        Start-Sleep -Seconds $IntervalSeconds
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Test-MyAgentReady {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.service -eq "postgres" -and $health.status -eq "ready"
    }
    catch {
        return $false
    }
}

function Get-ListeningProcess {
    return Get-NetTCPConnection `
        -LocalPort 8000 `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

try {
    Write-Host "MyAgent 一键启动" -ForegroundColor Green

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "没有找到 Docker。请先安装 Docker Desktop。"
    }
    if (-not (Test-Path -LiteralPath $composeFile)) {
        throw "没有找到 Compose 配置：$composeFile"
    }

    Write-Step "检查 Docker Engine"
    if (-not (Test-DockerReady)) {
        if (-not (Test-Path -LiteralPath $dockerDesktop)) {
            throw "没有找到 Docker Desktop：$dockerDesktop"
        }
        Write-Host "Docker 尚未运行，正在后台启动……"
        Start-Process `
            -FilePath $dockerDesktop `
            -WindowStyle Hidden |
            Out-Null
        if (-not (Wait-Until ${function:Test-DockerReady} 180)) {
            throw "Docker Desktop 在 180 秒内没有准备好，请手动打开后重试。"
        }
    }
    Write-Host "Docker 已就绪。" -ForegroundColor Green

    $env:POSTGRES_DB = "myagent"
    $env:POSTGRES_USER = "myagent"
    $env:POSTGRES_PASSWORD = "myagent-local"
    $env:POSTGRES_HOST_PORT = "55432"
    $env:COMPOSE_IGNORE_ORPHANS = "true"
    $env:MYAGENT_POSTGRES_DSN = (
        "postgresql://myagent:myagent-local@127.0.0.1:55432/myagent"
    )
    $env:MYAGENT_WORKSPACE_ROOT = $projectRoot
    if ([string]::IsNullOrWhiteSpace($env:MYAGENT_WORKSPACE_TEST_COMMAND)) {
        $env:MYAGENT_WORKSPACE_TEST_COMMAND = (
            "python scripts/verify_current_baseline.py"
        )
    }
    $env:MYAGENT_HTTP_HOST = "127.0.0.1"
    $env:MYAGENT_HTTP_PORT = "8000"

    Write-Step "启动 PostgreSQL 数据库"
    & docker compose `
        --project-directory $projectRoot `
        -f $composeFile `
        up -d --wait --wait-timeout 90 postgres
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL 启动失败，请查看上方 Docker 输出。"
    }
    Write-Host "PostgreSQL 已就绪，现有数据已保留。" -ForegroundColor Green

    Write-Step "检查 MyAgent 服务"
    if (-not (Test-MyAgentReady)) {
        $listener = Get-ListeningProcess
        if ($null -ne $listener) {
            throw (
                "端口 8000 已被其他程序占用（PID $($listener.OwningProcess)）。" +
                "启动器不会结束该程序。"
            )
        }

        $serverCommand = Get-Command `
            "my-agent-chat-http.exe" `
            -CommandType Application `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $serverCommand) {
            throw "没有找到 my-agent-chat-http，请先安装当前 MyAgent 项目。"
        }

        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $stdoutLog = Join-Path $logDirectory "server-$stamp.out.log"
        $stderrLog = Join-Path $logDirectory "server-$stamp.err.log"
        Write-Host "MyAgent 尚未运行，正在后台启动……"
        $serverProcess = Start-Process `
            -FilePath $serverCommand.Source `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -PassThru

        if (-not (Wait-Until ${function:Test-MyAgentReady} 60 1)) {
            throw "MyAgent 在 60 秒内没有准备好。日志目录：$logDirectory"
        }
    }
    Write-Host "MyAgent 已就绪。" -ForegroundColor Green

    if (-not $NoBrowser) {
        Write-Step "打开网页"
        Start-Process -FilePath $appUrl | Out-Null
    }

    Write-Host "`n启动完成。" -ForegroundColor Green
    if (-not $NoBrowser) {
        Start-Sleep -Milliseconds 1200
    }
    exit 0
}
catch {
    Write-Host "`n启动失败：$($_.Exception.Message)" -ForegroundColor Red
    if ($null -ne $serverProcess) {
        Write-Host "运行日志：$logDirectory" -ForegroundColor Yellow
    }
    if (-not $NoBrowser) {
        Read-Host "按 Enter 关闭窗口"
    }
    exit 1
}
