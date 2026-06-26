# Export everything needed to clone this instance to another machine
# Run from the GEO/ project directory

$exportDir = "geo-export"
New-Item -ItemType Directory -Force -Path $exportDir | Out-Null

Write-Host "=== GEO Agent Export ===" -ForegroundColor Cyan

# 1. Dump Postgres (all data: keywords, scrapes, cookies, accounts, alerts)
Write-Host "[1/4] Dumping database..." -ForegroundColor Yellow
docker exec grabon-geo-db pg_dump -U postgres -d grabon_geo --no-owner > "$exportDir\geo-backup.sql"
if ($LASTEXITCODE -eq 0) { Write-Host "  OK: Database dumped" -ForegroundColor Green }
else { Write-Host "  FAIL: Database dump failed" -ForegroundColor Red; exit 1 }

# 2. Save web image
Write-Host "[2/4] Saving web image (may take a few minutes)..." -ForegroundColor Yellow
docker save geo-web -o "$exportDir\geo-web-image.tar"
if ($LASTEXITCODE -eq 0) { Write-Host "  OK: Web image saved" -ForegroundColor Green }
else { Write-Host "  FAIL: Image save failed" -ForegroundColor Red; exit 1 }

# 3. Save postgres image
Write-Host "[3/4] Saving postgres image..." -ForegroundColor Yellow
docker save postgres:16-alpine -o "$exportDir\geo-db-image.tar"
if ($LASTEXITCODE -eq 0) { Write-Host "  OK: Postgres image saved" -ForegroundColor Green }
else { Write-Host "  FAIL: Image save failed" -ForegroundColor Red; exit 1 }

# 4. Copy config files
Write-Host "[4/4] Copying config files..." -ForegroundColor Yellow
Copy-Item .env "$exportDir\.env" -ErrorAction SilentlyContinue
Copy-Item docker-compose.yml "$exportDir\docker-compose.yml"
Copy-Item schema.sql "$exportDir\schema.sql"
Write-Host "  OK: Config files copied" -ForegroundColor Green

# Summary
$size = (Get-ChildItem -Path $exportDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host "`n=== Export complete ===" -ForegroundColor Cyan
Write-Host "Location: $exportDir\" -ForegroundColor White
Write-Host "Total size: $([math]::Round($size, 2)) GB" -ForegroundColor White
Write-Host "`nSend this folder to your friend. They run:" -ForegroundColor Gray
Write-Host "  docker load -i geo-db-image.tar" -ForegroundColor Gray
Write-Host "  docker load -i geo-web-image.tar" -ForegroundColor Gray
Write-Host "  docker compose up -d" -ForegroundColor Gray
Write-Host "  docker exec -i grabon-geo-db psql -U postgres -d grabon_geo < geo-backup.sql" -ForegroundColor Gray
