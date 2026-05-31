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
