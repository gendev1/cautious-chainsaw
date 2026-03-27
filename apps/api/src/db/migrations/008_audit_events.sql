CREATE TABLE audit_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id UUID NOT NULL,
  actor_id TEXT NOT NULL,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'service', 'impersonator')),
  action TEXT NOT NULL,
  resource_type TEXT,
  resource_id TEXT,
  metadata JSONB NOT NULL DEFAULT '{}',
  ip_address TEXT,
  user_agent TEXT,
  correlation_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- No FK on firm_id intentionally — audit must survive firm deletion
-- No UPDATE/DELETE should ever be run against this table

CREATE INDEX idx_audit_firm_created ON audit_events (firm_id, created_at);
CREATE INDEX idx_audit_firm_actor ON audit_events (firm_id, actor_id);
CREATE INDEX idx_audit_firm_action ON audit_events (firm_id, action);
CREATE INDEX idx_audit_firm_resource ON audit_events (firm_id, resource_type, resource_id);
