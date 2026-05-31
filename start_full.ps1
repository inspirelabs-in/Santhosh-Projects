# start_full.ps1 — Start all grabon-intel infrastructure and run a test lead
# Usage: powershell -ExecutionPolicy Bypass -File start_full.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n=== Step 1: Docker Compose Up ===" -ForegroundColor Cyan
docker compose up -d postgres searxng temporal temporal-ui
if (-not $?) { Write-Host "ERROR: docker compose failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 2: Wait for Postgres ===" -ForegroundColor Cyan
$retries = 0
do {
    Start-Sleep -Seconds 3
    $retries++
    $healthy = docker inspect --format "{{.State.Health.Status}}" grabon_intel-postgres-1 2>$null
    Write-Host "  postgres: $healthy (attempt $retries/20)"
} while ($healthy -ne "healthy" -and $retries -lt 20)
if ($healthy -ne "healthy") { Write-Host "ERROR: Postgres not healthy after 60s" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 3: Alembic Migrations ===" -ForegroundColor Cyan
python -m alembic upgrade head
if (-not $?) { Write-Host "ERROR: alembic failed" -ForegroundColor Red; exit 1 }

Write-Host "`n=== Step 4: Wait for Temporal ===" -ForegroundColor Cyan
$retries = 0
do {
    Start-Sleep -Seconds 5
    $retries++
    try { $r = Test-NetConnection -ComputerName localhost -Port 7233 -WarningAction SilentlyContinue; $up = $r.TcpTestSucceeded } catch { $up = $false }
    Write-Host "  temporal port 7233: $up (attempt $retries/20)"
} while (-not $up -and $retries -lt 20)

Write-Host "`n=== Step 5: Check SearXNG ===" -ForegroundColor Cyan
$retries = 0
do {
    Start-Sleep -Seconds 2
    $retries++
    try { $r = Test-NetConnection -ComputerName localhost -Port 8888 -WarningAction SilentlyContinue; $up = $r.TcpTestSucceeded } catch { $up = $false }
    Write-Host "  searxng port 8888: $up (attempt $retries/10)"
} while (-not $up -and $retries -lt 10)

Write-Host "`n=== Step 6: Start API Server (background) ===" -ForegroundColor Cyan
Start-Process -NoNewWindow -FilePath python -ArgumentList "-m", "uvicorn", "grabon_intel.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"

Start-Sleep -Seconds 3

Write-Host "`n=== Step 7: Start Temporal Worker (background) ===" -ForegroundColor Cyan
Start-Process -NoNewWindow -FilePath python -ArgumentList "-m", "grabon_intel.cli", "worker", "start"

Start-Sleep -Seconds 2

Write-Host "`n=== Step 8: Test Single Lead ===" -ForegroundColor Cyan
python test_single_lead.py "mamaearth.in"

Write-Host "`n=== Done ===" -ForegroundColor Green
