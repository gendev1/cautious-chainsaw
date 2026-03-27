CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TYPE firm_status AS ENUM ('provisioning', 'active', 'suspended', 'deactivated');

CREATE TABLE firms (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  status firm_status NOT NULL DEFAULT 'provisioning',
  branding JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_firms_slug ON firms (slug);
CREATE INDEX idx_firms_status ON firms (status) WHERE status = 'active';
