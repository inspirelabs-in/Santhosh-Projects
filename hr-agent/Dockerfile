# =====================================================================
# Hiring Agent -- single-container image.
#
# Bundles every service the stack needs into one container:
#   - Postgres 16 + pgvector          (port 5432)
#   - Redis 7                         (port 6379)
#   - MinIO (S3-compatible storage)   (ports 9000 / 9001)
#   - MailHog (dev SMTP catcher)      (ports 1025 / 8025)
#   - Temporal dev server             (ports 7233 / 8233)
#   - FastAPI backend (uvicorn)       (port 8000)
#   - Temporal worker                 (background)
#   - Next.js frontend (standalone)   (port 3100)
#
# Process supervision: supervisord.
# Persistent data lives under /data (mount a volume there if desired).
# =====================================================================

# ---------- Stage 1: build the Next.js frontend ----------------------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------- Stage 2: runtime image -----------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PGDATA=/data/postgres \
    PATH=/usr/lib/postgresql/16/bin:$PATH

# Base OS deps + Postgres 16 + pgvector + Redis + supervisor + Node runtime
# plus backend system deps (tesseract, poppler, libmagic) used by the parser.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg wget lsb-release \
        build-essential \
        tesseract-ocr poppler-utils libmagic1 \
        redis-server supervisor sudo \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-16 postgresql-16-pgvector nodejs \
 && rm -rf /var/lib/apt/lists/*

# Download single-binary services: Temporal CLI, MinIO, MailHog.
ARG TARGETARCH=amd64
RUN set -eux; \
    curl -fsSL "https://temporal.download/cli/archive/latest?platform=linux&arch=${TARGETARCH}" \
        -o /tmp/temporal.tgz; \
    tar -xzf /tmp/temporal.tgz -C /usr/local/bin temporal; \
    rm /tmp/temporal.tgz; \
    curl -fsSL "https://dl.min.io/server/minio/release/linux-${TARGETARCH}/minio" \
        -o /usr/local/bin/minio; \
    chmod +x /usr/local/bin/minio; \
    curl -fsSL "https://github.com/mailhog/MailHog/releases/download/v1.0.1/MailHog_linux_${TARGETARCH}" \
        -o /usr/local/bin/mailhog; \
    chmod +x /usr/local/bin/mailhog

# Install backend Python deps.
WORKDIR /app/backend
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml ./
RUN uv pip install --system --no-cache -e ".[dev]"

# Copy backend source + alembic.
COPY backend/src ./src
COPY backend/tests ./tests
COPY backend/alembic.ini ./
COPY backend/.env ./.env

# Copy built frontend (standalone output + static + public).
WORKDIR /app/frontend
COPY --from=frontend-build /fe/public ./public
COPY --from=frontend-build /fe/.next/standalone ./
COPY --from=frontend-build /fe/.next/static ./.next/static

# Supervisor + entrypoint.
WORKDIR /app
COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /data/postgres /data/minio /data/temporal /var/log/hiring-agent \
 && chown -R postgres:postgres /data/postgres

EXPOSE 3100 8000 5432 6379 7233 8233 9000 9001 8025 1025
VOLUME ["/data"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
