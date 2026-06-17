CREATE TABLE IF NOT EXISTS prompts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text TEXT NOT NULL UNIQUE,
    merchant_category VARCHAR(50) NOT NULL,
    intent_type VARCHAR(30) NOT NULL,
    tier INTEGER DEFAULT 2,
    keyword_group VARCHAR(200),
    last_run_at TIMESTAMPTZ,
    is_canonical BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prompts_tier_canonical_lastrun
ON prompts (tier, is_canonical, last_run_at NULLS FIRST);

CREATE TABLE IF NOT EXISTS execution_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    engine_name VARCHAR(30) NOT NULL,
    country_code VARCHAR(5) NOT NULL DEFAULT 'IN',
    raw_response_text TEXT NOT NULL,
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_logs_prompt_engine_captured
ON execution_logs (prompt_id, engine_name, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_execution_logs_captured_at
ON execution_logs (captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_execution_logs_engine_captured
ON execution_logs (engine_name, captured_at DESC);

CREATE TABLE IF NOT EXISTS brand_mentions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id UUID REFERENCES execution_logs(id) ON DELETE CASCADE,
    rank_position INT NOT NULL,
    brand_name VARCHAR(200) NOT NULL,
    is_target_brand BOOLEAN DEFAULT FALSE,
    sentiment VARCHAR(10) NOT NULL,
    context_snippet TEXT,
    cited_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_mentions_log_id
ON brand_mentions (log_id);

CREATE INDEX IF NOT EXISTS idx_brand_mentions_log_rank
ON brand_mentions (log_id, rank_position);

CREATE TABLE IF NOT EXISTS ai_hallucinated_coupons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id UUID REFERENCES execution_logs(id) ON DELETE CASCADE,
    coupon_code VARCHAR(200) NOT NULL,
    associated_merchant VARCHAR(200) NOT NULL,
    status_flag VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS app_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(100) UNIQUE NOT NULL,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- SERP tracking tables

CREATE TABLE IF NOT EXISTS serp_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    device VARCHAR(10) NOT NULL DEFAULT 'desktop',
    results_count TEXT,
    raw_html_hash VARCHAR(64),
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_serp_results_prompt_captured
ON serp_results (prompt_id, captured_at DESC);

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
);

CREATE INDEX IF NOT EXISTS idx_serp_organic_domain
ON serp_organic_entries (domain);

CREATE INDEX IF NOT EXISTS idx_serp_organic_serp_id
ON serp_organic_entries (serp_id);

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
);

CREATE INDEX IF NOT EXISTS idx_serp_features_serp_id
ON serp_features (serp_id);

-- Citation analysis tables

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
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_citation_pages_url
ON citation_pages (url);

CREATE TABLE IF NOT EXISTS citation_serp_overlaps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id UUID REFERENCES prompts(id) ON DELETE CASCADE,
    engine_name VARCHAR(30) NOT NULL,
    cited_url TEXT NOT NULL,
    serp_rank INT,
    brand_mention_id UUID REFERENCES brand_mentions(id) ON DELETE CASCADE,
    serp_entry_id UUID REFERENCES serp_organic_entries(id) ON DELETE SET NULL,
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_citation_overlap_prompt
ON citation_serp_overlaps (prompt_id, engine_name);

-- Diagnosis table

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
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_diagnoses_prompt_engine
ON diagnoses (prompt_id, engine_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_diagnoses_priority
ON diagnoses (priority, status, created_at DESC);

-- Applied fixes (verification loop)

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
);

CREATE INDEX IF NOT EXISTS idx_fixes_status
ON applied_fixes (verification_status, last_verified_at);

CREATE INDEX IF NOT EXISTS idx_fixes_prompt_engine
ON applied_fixes (prompt_id, engine_name);

-- Natural human-like search queries (how real people search)
INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('ajio coupon code', 'Fashion', 'Transactional')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('swiggy promo code today', 'Food', 'Transactional')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('best coupon sites india', 'General', 'Commercial')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('grabon coupons review', 'General', 'Direct Brand')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('myntra discount code 2025', 'Fashion', 'Transactional')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('amazon india coupon code', 'Electronics', 'Transactional')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('zomato offers today', 'Food', 'Transactional')
ON CONFLICT (text) DO NOTHING;

INSERT INTO prompts (text, merchant_category, intent_type) VALUES
('flipkart sale coupon', 'Electronics', 'Transactional')
ON CONFLICT (text) DO NOTHING;
