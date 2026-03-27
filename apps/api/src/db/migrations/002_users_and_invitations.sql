CREATE TYPE user_status AS ENUM ('invited', 'active', 'disabled');
CREATE TYPE invitation_status AS ENUM ('pending', 'accepted', 'expired', 'revoked');

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id UUID NOT NULL REFERENCES firms(id),
  email TEXT NOT NULL,
  password_hash TEXT,
  display_name TEXT NOT NULL,
  status user_status NOT NULL DEFAULT 'invited',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (firm_id, email)
);

CREATE INDEX idx_users_firm_id ON users (firm_id);

CREATE TABLE invitations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id UUID NOT NULL REFERENCES firms(id),
  email TEXT NOT NULL,
  role TEXT NOT NULL,
  display_name TEXT,
  invited_by UUID NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL,
  status invitation_status NOT NULL DEFAULT 'pending',
  expires_at TIMESTAMPTZ NOT NULL,
  accepted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_invitations_firm_id ON invitations (firm_id);
CREATE INDEX idx_invitations_token_hash ON invitations (token_hash);
CREATE UNIQUE INDEX idx_invitations_pending_email ON invitations (firm_id, email) WHERE status = 'pending';
