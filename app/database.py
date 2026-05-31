import asyncio
import logging
import psycopg
from psycopg.rows import dict_row
from app.config import get_settings

log = logging.getLogger("geo.db")

_dsn: str | None = None


def _get_dsn() -> str:
    global _dsn
    if _dsn is None:
        _dsn = get_settings().database_url
    return _dsn


def _run_query(func):
    with psycopg.connect(_get_dsn(), row_factory=dict_row, connect_timeout=10,
                         options="-c timezone=Asia/Kolkata") as conn:
        return func(conn)


async def run_db(func):
    """Async wrapper: runs sync DB callable in thread pool."""
    return await asyncio.to_thread(_run_query, func)


async def close_pool():
    pass


def close_pool_sync():
    pass


def _init_schema_sync():
    with psycopg.connect(_get_dsn(), row_factory=dict_row, connect_timeout=10,
                         options="-c timezone=Asia/Kolkata") as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                text TEXT NOT NULL UNIQUE,
                merchant_category VARCHAR(50) NOT NULL,
                intent_type VARCHAR(30) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS execution_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
                engine_name VARCHAR(30) NOT NULL,
                country_code VARCHAR(5) NOT NULL DEFAULT 'IN',
                raw_response_text TEXT NOT NULL,
                captured_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS brand_mentions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                log_id UUID REFERENCES execution_logs(id) ON DELETE CASCADE,
                rank_position INT NOT NULL,
                brand_name VARCHAR(100) NOT NULL,
                is_target_brand BOOLEAN DEFAULT FALSE,
                sentiment VARCHAR(10) NOT NULL,
                context_snippet TEXT,
                cited_url TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS ai_hallucinated_coupons (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                log_id UUID REFERENCES execution_logs(id) ON DELETE CASCADE,
                coupon_code VARCHAR(50) NOT NULL,
                associated_merchant VARCHAR(100) NOT NULL,
                status_flag VARCHAR(20) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                key VARCHAR(100) UNIQUE NOT NULL,
                value JSONB NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # Migration: model_name -> engine_name
        row = conn.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'execution_logs' AND column_name = 'model_name'
            )
        """).fetchone()
        if row and row["exists"]:
            conn.execute(
                "ALTER TABLE execution_logs RENAME COLUMN model_name TO engine_name"
            )
            log.info("Migrated execution_logs.model_name -> engine_name")

        # Migration: add country_code if missing
        row = conn.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'execution_logs' AND column_name = 'country_code'
            )
        """).fetchone()
        if row and not row["exists"]:
            conn.execute(
                "ALTER TABLE execution_logs ADD COLUMN country_code VARCHAR(5) NOT NULL DEFAULT 'IN'"
            )
            log.info("Added execution_logs.country_code column")

        for col, defn in [
            ("tier", "INTEGER DEFAULT 2"),
            ("keyword_group", "VARCHAR(200)"),
            ("last_run_at", "TIMESTAMPTZ"),
            ("is_canonical", "BOOLEAN DEFAULT TRUE"),
        ]:
            row = conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'prompts' AND column_name = %s
                )
            """, (col,)).fetchone()
            if row and not row["exists"]:
                conn.execute(f"ALTER TABLE prompts ADD COLUMN {col} {defn}")
                log.info(f"Added prompts.{col} column")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_costs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                provider VARCHAR(30) NOT NULL,
                model VARCHAR(60) NOT NULL,
                input_tokens INT NOT NULL DEFAULT 0,
                output_tokens INT NOT NULL DEFAULT 0,
                cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
                purpose VARCHAR(30) NOT NULL DEFAULT 'parser',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_costs_created
            ON api_costs (created_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompts_tier_canonical_lastrun
            ON prompts (tier, is_canonical, last_run_at NULLS FIRST)
        """)

        conn.commit()
        log.info("Database schema initialized")


async def init_schema():
    await asyncio.to_thread(_init_schema_sync)
