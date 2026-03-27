CREATE TABLE service_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  firm_id UUID REFERENCES firms(id),
  key_hash TEXT NOT NULL,
  previous_key_hash TEXT,
  key_grace_expires_at TIMESTAMPTZ,
  permissions JSONB NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  rotated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX idx_service_accounts_name ON service_accounts (name);
