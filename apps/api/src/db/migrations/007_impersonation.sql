CREATE TABLE impersonation_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  impersonator_user_id UUID NOT NULL REFERENCES users(id),
  target_user_id UUID NOT NULL REFERENCES users(id),
  firm_id UUID NOT NULL REFERENCES firms(id),
  reason TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  UNIQUE (firm_id, idempotency_key)
);

CREATE INDEX idx_impersonation_firm ON impersonation_sessions (firm_id);
CREATE INDEX idx_impersonation_impersonator ON impersonation_sessions (impersonator_user_id);
