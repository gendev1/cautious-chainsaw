// ---------------------------------------------------------------------------
// Database row types
// ---------------------------------------------------------------------------

export interface UserRow {
  id: string;
  firm_id: string;
  email: string;
  password_hash: string | null;
  display_name: string;
  status: 'invited' | 'active' | 'disabled';
}

export interface SessionRow {
  id: string;
  user_id: string;
  firm_id: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: Date;
  last_active_at: Date;
  revoked_at: Date | null;
}

export interface RefreshTokenRow {
  id: string;
  user_id: string;
  firm_id: string;
  session_id: string;
  token_hash: string;
  expires_at: Date;
  revoked_at: Date | null;
  created_at: Date;
}

export interface MfaFactorRow {
  id: string;
  user_id: string;
  type: string;
  secret_encrypted: string;
  verified_at: Date | null;
  created_at: Date;
}

export interface MfaRecoveryCodeRow {
  id: string;
  user_id: string;
  code_hash: string;
  used_at: Date | null;
  created_at: Date;
}

export interface UserRoleRow {
  role_name: string;
}

// ---------------------------------------------------------------------------
// Service return types
// ---------------------------------------------------------------------------

export interface FullTokenResult {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  mfa: boolean;
  userId: string;
  firmId: string;
}

export interface PartialTokenResult {
  sessionId: string;
  mfaRequired: true;
  userId: string;
  firmId: string;
}

export type LoginResult = FullTokenResult | PartialTokenResult;

export interface LogoutResult {
  userId: string;
  firmId: string;
}

export interface MfaEnrollResult {
  factorId: string;
  provisioningUri: string;
  recoveryCodes: string[];
}
