# HR Agent control script (docker-first).
#
#   .\app.ps1 start     -> docker compose up -d --build  + caddy + ngrok
#   .\app.ps1 stop      -> docker compose stop  + kills caddy + ngrok
#   .\app.ps1 down      -> docker compose down (removes containers, keeps volumes)
#   .\app.ps1 build     -> docker compose build --no-cache
#   .\app.ps1 status    -> shows what's running (containers + tunnel)
#   .\app.ps1 logs      -> docker compose logs -f (all services)
#   .\app.ps1 logs backend|frontend|worker  -> single service
#   .\app.ps1 restart   -> stop then start
#   .\app.ps1 setup     -> first-time: build images + run migrations
#
# Devops handoff: every app service runs in docker. Only caddy + ngrok stay
# native (Windows-side tunnel layer). On a Linux host these two get replaced
# by a real domain + reverse proxy.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "down", "build", "status", "logs", "restart", "setup")]
    [string]$Command = "start",

    [Parameter(Position = 1)]
    [string]$Service = ""
)

$ErrorActionPreference = "Continue"
$Root        = "D:\HR Agent"
$LogDir      = "C:\v\hr-logs"
$NgrokDomain = "chili-congested-brethren.ngrok-free.dev"
$ApiUrl      = "https://$NgrokDomain"
$BackendPort = 8001
$FrontendPort = 3100
$CaddyPort   = 8080
$CaddyExe    = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\CaddyServer.Caddy_Microsoft.Winget.Source_8wekyb3d8bbwe\caddy.exe"
$NgrokExe    = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"

function Ensure-LogDir {
    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir | Out-Null
    }
}

function Test-Port($p) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect("localhost", $p)
        $c.Close()
        return $true
    } catch { return $false }
}

function Test-HttpOk($url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch { return $false }
}

function Stop-ByName($names) {
    foreach ($n in $names) {
        Get-Process $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

function Stop-ByPort($port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conn) {
        try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Cmd-Start {
    Ensure-LogDir

    Write-Host "[1/3] docker compose up (postgres + redis + minio + backend + worker + frontend + mailhog)" -ForegroundColor Cyan
    Push-Location $Root
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker compose failed. Aborting." -ForegroundColor Red
        Pop-Location
        return
    }
    Pop-Location

    Write-Host "[2/3] caddy (:$CaddyPort -> backend:$BackendPort + frontend:$FrontendPort)" -ForegroundColor Cyan
    if (Test-Port $CaddyPort) {
        Write-Host "  already serving -> skip" -ForegroundColor DarkGray
    } else {
        Stop-ByName @("caddy")
        Copy-Item "$Root\Caddyfile" "C:\v\Caddyfile" -Force
        Start-Process -FilePath $CaddyExe -ArgumentList "run","--config","C:\v\Caddyfile" `
            -WindowStyle Hidden `
            -RedirectStandardOutput "$LogDir\caddy.out" `
            -RedirectStandardError  "$LogDir\caddy.err" | Out-Null
    }

    Write-Host "[3/3] ngrok ($NgrokDomain -> :$CaddyPort)" -ForegroundColor Cyan
    if (Test-HttpOk "$ApiUrl/healthz") {
        Write-Host "  tunnel live -> skip" -ForegroundColor DarkGray
    } else {
        Stop-ByName @("ngrok")
        Start-Process -FilePath $NgrokExe `
            -ArgumentList "http","--domain=$NgrokDomain","$CaddyPort" `
            -WindowStyle Hidden `
            -RedirectStandardOutput "$LogDir\ngrok.out" `
            -RedirectStandardError  "$LogDir\ngrok.err" | Out-Null
    }

    Write-Host "waiting for backend to accept connections..." -ForegroundColor DarkGray
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk "http://localhost:$BackendPort/healthz") { break }
        Start-Sleep -Seconds 2
    }

    Cmd-Status
    Write-Host ""
    Write-Host "  Recruiter UI : $ApiUrl/login   (or http://localhost:$FrontendPort)" -ForegroundColor Green
    Write-Host "  Backend API  : $ApiUrl"                                              -ForegroundColor Green
    Write-Host "  Mailhog UI   : http://localhost:8025"                                -ForegroundColor Green
    Write-Host "  Logs         : .\app.ps1 logs   |   .\app.ps1 logs backend"          -ForegroundColor DarkGray
    Write-Host "  Native logs  : $LogDir (caddy / ngrok only)"                         -ForegroundColor DarkGray
    Write-Host ""
}

function Cmd-Stop {
    Write-Host "stopping containers + tunnel..." -ForegroundColor Yellow
    Push-Location $Root
    docker compose stop 2>&1 | Out-Null
    Pop-Location
    Stop-ByPort $CaddyPort
    Stop-ByName @("ngrok")
    Write-Host "stopped." -ForegroundColor Green
}

function Cmd-Down {
    Write-Host "removing containers (volumes preserved)..." -ForegroundColor Yellow
    Push-Location $Root
    docker compose down 2>&1
    Pop-Location
    Stop-ByPort $CaddyPort
    Stop-ByName @("ngrok")
}

function Cmd-Build {
    Push-Location $Root
    docker compose build --no-cache
    Pop-Location
}

function Cmd-Status {
    Push-Location $Root
    docker compose ps --format "table {{.Service}}\t{{.State}}\t{{.Ports}}"
    Pop-Location
    Write-Host ""
    $caddy = if (Test-Port $CaddyPort) { "[ok]" } else { "[--]" }
    Write-Host ("  caddy        :{0,-5} {1}" -f $CaddyPort, $caddy)
    try {
        $code = (Invoke-WebRequest -Uri "$ApiUrl/healthz" -UseBasicParsing -TimeoutSec 4).StatusCode
        Write-Host ("  ngrok        $ApiUrl  HTTP $code") -ForegroundColor Green
    } catch {
        Write-Host ("  ngrok        $ApiUrl  unreachable") -ForegroundColor DarkGray
    }
}

function Cmd-Logs {
    Push-Location $Root
    if ($Service) {
        docker compose logs -f --tail=200 $Service
    } else {
        docker compose logs -f --tail=200
    }
    Pop-Location
}

function Cmd-Restart {
    Cmd-Stop
    Start-Sleep 2
    Cmd-Start
}

function Cmd-Setup {
    Write-Host "[setup 1/2] building images" -ForegroundColor Cyan
    Push-Location $Root
    docker compose build
    Pop-Location

    Write-Host "[setup 2/2] starting stack (entrypoint runs alembic upgrade head on backend)" -ForegroundColor Cyan
    Push-Location $Root
    docker compose up -d
    Pop-Location

    Write-Host "done. run '.\app.ps1 start' to bring up caddy + ngrok." -ForegroundColor Green
}

switch ($Command) {
    "start"   { Cmd-Start }
    "stop"    { Cmd-Stop }
    "down"    { Cmd-Down }
    "build"   { Cmd-Build }
    "status"  { Cmd-Status }
    "logs"    { Cmd-Logs }
    "restart" { Cmd-Restart }
    "setup"   { Cmd-Setup }
}
