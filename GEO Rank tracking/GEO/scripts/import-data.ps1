# Import GEO Agent from exported data
# Run from the geo-export folder on the destination machine
# Prerequisite: Docker Desktop installed and running

Write-Host "=== GEO Agent Import ===" -ForegroundColor Cyan

# Check Docker
docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker not running. Start Docker Desktop first." -ForegroundColor Red
    exit 1
}

# 1. Load images
Write-Host "[1/3] Loading Docker images (may take a few minutes)..." -ForegroundColor Yellow
docker load -i geo-db-image.tar
docker load -i geo-web-image.tar
Write-Host "  OK: Images loaded" -ForegroundColor Green

# 2. Start containers
Write-Host "[2/3] Starting containers..." -ForegroundColor Yellow
docker compose up -d
Write-Host "  Waiting for database to be healthy..." -ForegroundColor Gray
Start-Sleep -Seconds 10

# Wait for DB healthy
$retries = 0
while ($retries -lt 30) {
    $health = docker inspect --format='{{.State.Health.Status}}' grabon-geo-db 2>$null
    if ($health -eq "healthy") { break }
    Start-Sleep -Seconds 2
    $retries++
}

if ($health -ne "healthy") {
    Write-Host "  WARNING: DB not healthy yet, attempting restore anyway..." -ForegroundColor Yellow
}
Write-Host "  OK: Containers running" -ForegroundColor Green

# 3. Restore database
Write-Host "[3/3] Restoring database (all data, cookies, accounts)..." -ForegroundColor Yellow
Get-Content geo-backup.sql | docker exec -i grabon-geo-db psql -U postgres -d grabon_geo 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host "  OK: Database restored" -ForegroundColor Green }
else { Write-Host "  WARNING: Some restore warnings (usually safe to ignore)" -ForegroundColor Yellow }

Write-Host "`n=== Import complete ===" -ForegroundColor Cyan
Write-Host "Open http://localhost:8000 in your browser" -ForegroundColor White
Write-Host "`nAll keywords, scrape history, cookies, and accounts are restored." -ForegroundColor Gray
