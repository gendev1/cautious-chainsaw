CREATE TYPE household_status AS ENUM ('active', 'inactive', 'closed');

CREATE TABLE households (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  status household_status NOT NULL DEFAULT 'active',
  primary_advisor_id UUID NOT NULL REFERENCES users(id),
  service_team_json JSONB NOT NULL DEFAULT '[]',
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL
);

CREATE INDEX idx_households_tenant_created_at ON households (tenant_id, created_at DESC);
CREATE INDEX idx_households_tenant_status ON households (tenant_id, status);
CREATE INDEX idx_households_tenant_primary_advisor ON households (tenant_id, primary_advisor_id);
