-- Person contacts table
CREATE TABLE IF NOT EXISTS persons (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    name VARCHAR(256) NOT NULL,
    title VARCHAR(256),
    email VARCHAR(256),
    phone VARCHAR(64),
    linkedin_url TEXT,
    source VARCHAR(64) NOT NULL DEFAULT 'vane_research',
    confidence NUMERIC(4,3) NOT NULL DEFAULT 0.5,
    verified BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_persons_brand_id ON persons(brand_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_persons_brand_email ON persons(brand_id, email) WHERE email IS NOT NULL;

-- Notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    type VARCHAR(64) NOT NULL,
    title VARCHAR(256) NOT NULL,
    message TEXT NOT NULL,
    brand_id BIGINT REFERENCES brands(id) ON DELETE SET NULL,
    entity_type VARCHAR(32),
    entity_id BIGINT,
    read BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_notifications_read_created ON notifications(read, created_at);

-- Lead lifecycle events table
CREATE TABLE IF NOT EXISTS lead_events (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    from_status VARCHAR(32),
    to_status VARCHAR(32),
    note TEXT,
    actor VARCHAR(128),
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_lead_events_brand_created ON lead_events(brand_id, created_at);
