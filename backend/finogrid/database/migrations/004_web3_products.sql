-- Web3 product layer: append-only RWA evidence and simulated strategy seasons.
-- Apply after 003_mandate.sql. No private keys, identity documents, or raw
-- contracts are stored in this schema.

CREATE TABLE rwa_assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_key VARCHAR(255) NOT NULL UNIQUE,
    asset_class VARCHAR(64) NOT NULL,
    issuer_client_id UUID REFERENCES clients(id) ON DELETE SET NULL,
    jurisdiction CHAR(2),
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT rwa_asset_status_check CHECK (status IN ('draft', 'verified', 'suspended', 'retired'))
);

CREATE TABLE rwa_evidence_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id UUID NOT NULL REFERENCES rwa_assets(id) ON DELETE RESTRICT,
    evidence_type VARCHAR(64) NOT NULL,
    source_uri VARCHAR(2048) NOT NULL,
    content_hash CHAR(64) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    signer VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    previous_event_hash CHAR(64),
    event_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT rwa_evidence_hash_check CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT rwa_evidence_uri_check CHECK (source_uri LIKE 'https://%' OR source_uri LIKE 'ipfs://%')
);
CREATE INDEX idx_rwa_evidence_asset_observed ON rwa_evidence_events(asset_id, observed_at);

CREATE TABLE gamefi_seasons (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    season_key VARCHAR(128) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    scoring_config JSONB NOT NULL DEFAULT '{"return_weight":1,"drawdown_weight":0.5,"win_rate_weight":0.2}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT gamefi_season_status_check CHECK (status IN ('draft', 'open', 'closed', 'archived')),
    CONSTRAINT gamefi_season_time_check CHECK (ends_at > starts_at)
);

CREATE TABLE gamefi_strategy_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    season_id UUID NOT NULL REFERENCES gamefi_seasons(id) ON DELETE CASCADE,
    strategy_key VARCHAR(128) NOT NULL,
    snapshot_hash CHAR(64) NOT NULL,
    return_pct NUMERIC(18,8) NOT NULL,
    max_drawdown_pct NUMERIC(18,8) NOT NULL,
    win_rate_pct NUMERIC(18,8) NOT NULL,
    score NUMERIC(18,8) NOT NULL,
    rank INTEGER,
    completed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(season_id, strategy_key, snapshot_hash)
);
CREATE INDEX idx_gamefi_runs_season_score ON gamefi_strategy_runs(season_id, score DESC);

CREATE OR REPLACE FUNCTION prevent_rwa_evidence_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'rwa_evidence_events is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER rwa_evidence_no_update
BEFORE UPDATE OR DELETE ON rwa_evidence_events
FOR EACH ROW EXECUTE FUNCTION prevent_rwa_evidence_mutation();
