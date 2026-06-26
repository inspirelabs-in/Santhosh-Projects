#!/bin/bash
set -e

echo "=== GEO Agent — Ubuntu Deploy ==="
echo "Sets up: Docker + App + Watchtower (auto-updates) + Cloudflare Tunnel"
echo ""

DEPLOY_DIR="$HOME/grabon-geo"
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# ── 1. Install Docker if missing ──
if ! command -v docker &>/dev/null; then
    echo "[1/5] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "  Docker installed. You may need to log out and back in for group changes."
    echo "  Then re-run this script."
    newgrp docker
else
    echo "[1/5] Docker already installed ✓"
fi

# ── 2. Authenticate to GHCR ──
echo ""
echo "[2/5] GitHub Container Registry login"
echo "  You need a GitHub Personal Access Token (classic) with 'read:packages' scope."
echo "  Create one at: https://github.com/settings/tokens/new"
echo ""
read -rp "GitHub username: " GH_USER
read -rsp "GitHub token (paste, won't echo): " GH_TOKEN
echo ""
echo "$GH_TOKEN" | docker login ghcr.io -u "$GH_USER" --password-stdin
echo "  GHCR login OK ✓"

# ── 3. Collect config ──
echo ""
echo "[3/5] Configuration"

if [ ! -f .env ]; then
    read -rsp "OpenAI API Key: " OPENAI_KEY; echo ""
    read -rp  "CloudProxy URL (or press Enter to skip): " PROXY_URL
    read -rp  "Teams Webhook URL (or press Enter to skip): " TEAMS_URL
    read -rp  "Cloudflare Tunnel Token (or press Enter to skip): " CF_TOKEN

    cat > .env <<ENVEOF
OPENAI_API_KEY=${OPENAI_KEY}
CLOUDPROXY_URL=${PROXY_URL}
TEAMS_WEBHOOK_URL=${TEAMS_URL}
CLOUDFLARE_TUNNEL_TOKEN=${CF_TOKEN}
DB_PASSWORD=postgres
ENVEOF
    echo "  .env created ✓"
else
    echo "  .env already exists, skipping ✓"
fi

# ── 4. Create docker-compose.prod.yml ──
echo ""
echo "[4/5] Creating docker-compose.yml..."

cat > docker-compose.yml <<'COMPOSEEOF'
services:
  db:
    image: postgres:16-alpine
    container_name: grabon-geo-db
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${DB_PASSWORD:-postgres}
      POSTGRES_DB: grabon_geo
      TZ: Asia/Kolkata
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d grabon_geo"]
      interval: 5s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
    restart: unless-stopped

  web:
    image: ghcr.io/inspirelabs-in/grabon-geo:latest
    container_name: grabon-geo-web
    init: true
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      DATABASE_URL: postgresql://postgres:${DB_PASSWORD:-postgres}@db:5432/grabon_geo
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      CLOUDPROXY_URL: ${CLOUDPROXY_URL}
      TEAMS_WEBHOOK_URL: ${TEAMS_WEBHOOK_URL}
      MALLOC_ARENA_MAX: "2"
      TZ: Asia/Kolkata
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)\""]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 4G
        reservations:
          cpus: "1.0"
          memory: 1G
    depends_on:
      db:
        condition: service_healthy
    restart: always

  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    environment:
      WATCHTOWER_CLEANUP: "true"
      WATCHTOWER_POLL_INTERVAL: "300"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${HOME}/.docker/config.json:/config.json:ro
    command: grabon-geo-web
    restart: unless-stopped

  tunnel:
    image: cloudflare/cloudflared:latest
    container_name: cloudflare-tunnel
    command: tunnel run --token ${CLOUDFLARE_TUNNEL_TOKEN}
    depends_on:
      - web
    restart: unless-stopped

volumes:
  postgres_data:
COMPOSEEOF
echo "  docker-compose.yml created ✓"

# ── 5. Start everything ──
echo ""
echo "[5/5] Starting services..."
docker compose pull
docker compose up -d

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Services running:"
echo "  App:        http://localhost:8000"
echo "  Watchtower: checking for updates every 5 minutes"
echo "  Tunnel:     exposing via Cloudflare (if token set)"
echo ""
echo "To restore data from export:"
echo "  cat geo-backup.sql | docker exec -i grabon-geo-db psql -U postgres -d grabon_geo"
echo ""
echo "To check status:  docker compose ps"
echo "To view logs:     docker compose logs -f web"
