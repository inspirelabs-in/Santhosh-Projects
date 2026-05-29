#!/usr/bin/env bash
# Entrypoint for the single-container Hiring Agent image.
#
# First-boot responsibilities:
#   1. Initialise Postgres data dir if empty (initdb).
#   2. Rewrite backend/.env so every service name points at 127.0.0.1
#      (compose hostnames like "postgres", "redis" don't exist in-container).
#   3. Hand off to supervisord, which runs every service.
#
# Supervisor also runs this script with `run-init` to perform the one-shot
# DB/bucket bootstrap after Postgres + MinIO have come up.

set -euo pipefail

ENV_FILE=/app/backend/.env
PG_BIN=/usr/lib/postgresql/16/bin
PGDATA=/data/postgres
MC=/tmp/mc

rewrite_env() {
    # Replace compose-style hostnames with localhost so the app can reach
    # every colocated service through the loopback interface.
    [ -f "$ENV_FILE" ] || return 0
    sed -i \
        -e 's#@postgres:5432#@127.0.0.1:5432#g' \
        -e 's#redis://redis:#redis://127.0.0.1:#g' \
        -e 's#http://minio:9000#http://127.0.0.1:9000#g' \
        -e 's#=mailhog$#=127.0.0.1#g' \
        -e 's#=mailhog #=127.0.0.1 #g' \
        -e 's#SMTP_HOST=mailhog#SMTP_HOST=127.0.0.1#g' \
        -e 's#LANGFUSE_HOST=http://langfuse:3000#LANGFUSE_HOST=http://127.0.0.1:3000#g' \
        "$ENV_FILE"
}

init_postgres_datadir() {
    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        echo "[entrypoint] initialising postgres data dir at $PGDATA"
        mkdir -p "$PGDATA"
        chown -R postgres:postgres "$PGDATA"
        sudo -u postgres "$PG_BIN/initdb" -D "$PGDATA" \
            --username=agent --pwfile=<(echo "${DB_PASSWORD:-agent_dev_pw}") \
            --auth-host=scram-sha-256 --encoding=UTF8 --locale=C >/dev/null
        # Allow local TCP auth with password.
        echo "host all all 127.0.0.1/32 scram-sha-256" \
            >> "$PGDATA/pg_hba.conf"
    fi
}

wait_for_pg() {
    for _ in $(seq 1 60); do
        if sudo -u postgres "$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "[init] postgres did not become ready in time" >&2
    return 1
}

wait_for_minio() {
    for _ in $(seq 1 60); do
        if curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "[init] minio did not become ready in time" >&2
    return 1
}

run_init() {
    # Bootstrap databases + extensions + MinIO buckets. Idempotent.
    wait_for_pg

    local pw="${DB_PASSWORD:-agent_dev_pw}"
    export PGPASSWORD="$pw"

    psql -h 127.0.0.1 -U agent -d postgres -tc \
        "SELECT 1 FROM pg_database WHERE datname='hiring_agent'" | grep -q 1 \
        || psql -h 127.0.0.1 -U agent -d postgres -c "CREATE DATABASE hiring_agent OWNER agent"

    for db in langfuse; do
        psql -h 127.0.0.1 -U agent -d postgres -tc \
            "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
            || psql -h 127.0.0.1 -U agent -d postgres -c "CREATE DATABASE $db OWNER agent"
    done

    psql -h 127.0.0.1 -U agent -d hiring_agent <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL

    # MinIO buckets via the `mc` one-shot client.
    wait_for_minio
    if [ ! -x "$MC" ]; then
        curl -fsSL "https://dl.min.io/client/mc/release/linux-amd64/mc" -o "$MC"
        chmod +x "$MC"
    fi
    "$MC" alias set local http://127.0.0.1:9000 minioadmin minioadmin >/dev/null
    "$MC" mb -p local/hiring-agent-resumes >/dev/null 2>&1 || true
    "$MC" mb -p local/hiring-agent-consent >/dev/null 2>&1 || true

    # Apply alembic migrations (idempotent; head == no-op).
    if [ -d /app/backend ]; then
        cd /app/backend
        if command -v alembic >/dev/null 2>&1; then
            alembic upgrade head || echo "[init] alembic upgrade failed (continuing)"
        fi
        cd - >/dev/null
    fi

    echo "[init] done"
}

# ---- dispatch --------------------------------------------------------
if [ "${1:-}" = "run-init" ]; then
    run_init
    exit 0
fi

rewrite_env
init_postgres_datadir

exec "$@"
