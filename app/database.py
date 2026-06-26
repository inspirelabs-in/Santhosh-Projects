import asyncio
import logging
import threading
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from app.config import get_settings

log = logging.getLogger("geo.db")

_dsn: str | None = None
_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_dsn() -> str:
    global _dsn
    if _dsn is None:
        _dsn = get_settings().database_url
    return _dsn


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        _pool = ConnectionPool(
            conninfo=_get_dsn(),
            min_size=2,
            max_size=15,
            timeout=30,
            max_idle=300,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": 10,
                "options": "-c timezone=Asia/Kolkata",
            },
        )
        log.info("Database connection pool initialized (max_size=15, max_idle=300s)")
        return _pool


def _run_query(func):
    with _get_pool().connection() as conn:
        return func(conn)


async def run_db(func):
    """Async wrapper: runs sync DB callable in thread pool."""
    return await asyncio.to_thread(_run_query, func)


async def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        log.info("Database connection pool closed")


def close_pool_sync():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        log.info("Database connection pool closed (sync)")


def _kill_idle_connections():
    try:
        with psycopg.connect(_get_dsn(), row_factory=dict_row, connect_timeout=10,
                             options="-c timezone=Asia/Kolkata") as conn:
            conn.autocommit = True
            result = conn.execute("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid != pg_backend_pid()
                  AND state IN ('idle', 'idle in transaction')
                  AND query_start < NOW() - INTERVAL '5 minutes'
            """).fetchall()
            if result:
                log.info(f"Cleaned up {len(result)} stale DB connections")
    except Exception as e:
        log.warning(f"Connection cleanup failed (non-fatal): {e}")


def _init_schema_sync():
    _kill_idle_connections()
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

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_execution_logs_prompt_engine_captured
            ON execution_logs (prompt_id, engine_name, captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_execution_logs_captured_at
            ON execution_logs (captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_execution_logs_engine_captured
            ON execution_logs (engine_name, captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_brand_mentions_log_id
            ON brand_mentions (log_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_brand_mentions_log_rank
            ON brand_mentions (log_id, rank_position)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_hallucinated_coupons_log_id
            ON ai_hallucinated_coupons (log_id)
        """)

        # --- SERP tables ---

        conn.execute("""
            CREATE TABLE IF NOT EXISTS serp_results (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
                device VARCHAR(10) NOT NULL DEFAULT 'desktop',
                results_count TEXT,
                raw_html_hash VARCHAR(64),
                captured_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS serp_organic_entries (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                serp_id UUID REFERENCES serp_results(id) ON DELETE CASCADE,
                rank_position INT NOT NULL,
                title TEXT NOT NULL,
                snippet TEXT,
                url TEXT NOT NULL,
                domain VARCHAR(200) NOT NULL,
                has_table BOOLEAN DEFAULT FALSE,
                is_target BOOLEAN DEFAULT FALSE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS serp_features (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                serp_id UUID REFERENCES serp_results(id) ON DELETE CASCADE,
                feature_type VARCHAR(30) NOT NULL,
                rank_position INT,
                title TEXT,
                snippet TEXT,
                url TEXT,
                domain VARCHAR(200),
                question_text TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_results_prompt_captured
            ON serp_results (prompt_id, captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_organic_domain
            ON serp_organic_entries (domain)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_organic_serp_rank
            ON serp_organic_entries (serp_id, rank_position)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_organic_serp_id
            ON serp_organic_entries (serp_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_features_serp_id
            ON serp_features (serp_id)
        """)

        # Migration: add last_serp_at to prompts
        row = conn.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'prompts' AND column_name = 'last_serp_at'
            )
        """).fetchone()
        if row and not row["exists"]:
            conn.execute("ALTER TABLE prompts ADD COLUMN last_serp_at TIMESTAMPTZ")
            log.info("Added prompts.last_serp_at column")

        # --- Citation tables ---

        conn.execute("""
            CREATE TABLE IF NOT EXISTS citation_pages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                url TEXT NOT NULL,
                domain VARCHAR(200) NOT NULL,
                title TEXT,
                meta_description TEXT,
                h1_tags TEXT[],
                word_count INT,
                main_text_preview TEXT,
                schema_types TEXT[],
                canonical_url TEXT,
                fetch_status INT NOT NULL DEFAULT 0,
                fetched_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_citation_pages_url
            ON citation_pages (url)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS citation_serp_overlaps (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
                engine_name VARCHAR(30) NOT NULL,
                cited_url TEXT NOT NULL,
                serp_rank INT,
                brand_mention_id UUID REFERENCES brand_mentions(id) ON DELETE CASCADE,
                serp_entry_id UUID REFERENCES serp_organic_entries(id) ON DELETE SET NULL,
                detected_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_citation_overlap_prompt
            ON citation_serp_overlaps (prompt_id, engine_name)
        """)

        # --- Diagnoses table ---

        conn.execute("""
            CREATE TABLE IF NOT EXISTS diagnoses (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
                engine_name VARCHAR(30) NOT NULL,
                root_causes JSONB NOT NULL DEFAULT '[]',
                action_items JSONB NOT NULL DEFAULT '[]',
                priority VARCHAR(10) NOT NULL DEFAULT 'medium',
                confidence FLOAT NOT NULL DEFAULT 0.0,
                summary TEXT DEFAULT '',
                evidence_snapshot JSONB,
                llm_model VARCHAR(60),
                cost_usd NUMERIC(10,6) DEFAULT 0,
                status VARCHAR(20) DEFAULT 'active',
                revision_count INTEGER DEFAULT 0,
                evidence_hash VARCHAR(32) DEFAULT '',
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_diagnoses_prompt_engine
            ON diagnoses (prompt_id, engine_name, created_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_diagnoses_priority
            ON diagnoses (priority, status, created_at DESC)
        """)

        # Migration: add revision_count + updated_at to diagnoses
        for col, defn in [
            ("revision_count", "INTEGER DEFAULT 0"),
            ("evidence_hash", "VARCHAR(32) DEFAULT ''"),
            ("updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
        ]:
            col_exists = conn.execute(
                """SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'diagnoses' AND column_name = %s
                )""", (col,)
            ).fetchone()
            if col_exists and not col_exists["exists"]:
                conn.execute(f"ALTER TABLE diagnoses ADD COLUMN {col} {defn}")
                log.info(f"Added diagnoses.{col} column")

        # --- Applied fixes table ---

        conn.execute("""
            CREATE TABLE IF NOT EXISTS applied_fixes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                diagnosis_id UUID REFERENCES diagnoses(id) ON DELETE SET NULL,
                prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
                engine_name VARCHAR(30) NOT NULL DEFAULT 'all',
                description TEXT NOT NULL DEFAULT '',
                target_url TEXT,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                verification_status VARCHAR(20) DEFAULT 'monitoring',
                last_verified_at TIMESTAMPTZ,
                verification_result JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fixes_status
            ON applied_fixes (verification_status, last_verified_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fixes_prompt_engine
            ON applied_fixes (prompt_id, engine_name)
        """)

        # --- Response dedup cache ---

        conn.execute("""
            CREATE TABLE IF NOT EXISTS response_cache (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                brand_key VARCHAR(200) NOT NULL,
                engine_name VARCHAR(30) NOT NULL,
                prompt_id UUID REFERENCES prompts(id) ON DELETE SET NULL,
                cached_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (brand_key, engine_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_response_cache_brand_engine
            ON response_cache (brand_key, engine_name, cached_at DESC)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                type VARCHAR(30) NOT NULL DEFAULT 'system',
                title VARCHAR(200) NOT NULL,
                message TEXT NOT NULL,
                severity VARCHAR(10) NOT NULL DEFAULT 'info',
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_unread
            ON notifications (is_read, created_at DESC) WHERE is_read = FALSE
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_created
            ON notifications (created_at DESC)
        """)

        # Migration: add dedup_key + metadata to notifications
        for col, defn in [
            ("dedup_key", "VARCHAR(200)"),
            ("metadata", "JSONB DEFAULT '{}'"),
        ]:
            col_exists = conn.execute(
                """SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'notifications' AND column_name = %s
                )""", (col,)
            ).fetchone()
            if col_exists and not col_exists["exists"]:
                conn.execute(f"ALTER TABLE notifications ADD COLUMN {col} {defn}")
                log.info(f"Added notifications.{col} column")

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedup_key
            ON notifications (dedup_key) WHERE dedup_key IS NOT NULL
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_brand_mentions_brand_lower
            ON brand_mentions (LOWER(brand_name))
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompts_last_serp_at
            ON prompts (last_serp_at ASC NULLS FIRST)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompts_intent_type
            ON prompts (intent_type)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_results_captured_at
            ON serp_results (captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_serp_organic_is_target
            ON serp_organic_entries (serp_id, is_target) WHERE is_target = TRUE
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_brand_mentions_target
            ON brand_mentions (is_target_brand) WHERE is_target_brand = TRUE
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_execution_logs_latest
            ON execution_logs (prompt_id, engine_name, captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_execution_logs_captured_at
            ON execution_logs (captured_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_brand_mentions_log_id
            ON brand_mentions (log_id)
        """)

        conn.commit()
        log.info("Database schema initialized")

        junk_codes = (
            "CODE", "CODES", "COUPON", "COUPONS", "PROMO", "PROMOS",
            "VOUCHER", "VOUCHERS", "DISCOUNT", "DISCOUNTS",
            "DUNIA", "KARO", "GURU", "DIME", "MART", "RAJA", "WAPAS",
            "TIONAL", "TIONS", "IONAL", "ALLY", "MENT", "NESS",
            "AVAILABLE", "MENTIONED", "PROMOTIONAL", "INTERNATIONAL",
            "COUPONCODE", "PROMOCODE", "VOUCHERCODE", "DISCOUNTCODE",
        )
        ph = ",".join(["%s"] * len(junk_codes))
        cur = conn.execute(
            f"DELETE FROM ai_hallucinated_coupons WHERE UPPER(coupon_code) IN ({ph})",
            junk_codes,
        )
        if cur.rowcount:
            log.info(f"Cleaned {cur.rowcount} junk coupon codes from DB")
        conn.commit()


async def init_schema():
    await asyncio.to_thread(_init_schema_sync)
