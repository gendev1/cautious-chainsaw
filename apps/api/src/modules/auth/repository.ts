import { getDb } from '../../db/client.js';
import type {
  UserRow,
  SessionRow,
  RefreshTokenRow,
  MfaFactorRow,
  MfaRecoveryCodeRow,
  UserRoleRow,
} from './types.js';

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

export async function findUserByEmailAndFirmId(
  email: string,
  firmId: string,
): Promise<(UserRow & { firm_slug: string; firm_status: string }) | null> {
  const sql = getDb();
  const rows = await sql<(UserRow & { firm_slug: string; firm_status: string })[]>`
    SELECT u.id, u.firm_id, u.email, u.password_hash, u.display_name, u.status,
           f.slug AS firm_slug, f.status AS firm_status
    FROM users u
    JOIN firms f ON f.id = u.firm_id
    WHERE u.email = ${email}
      AND u.firm_id = ${firmId}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function getUserRoles(userId: string, firmId: string): Promise<string[]> {
  const sql = getDb();
  const rows = await sql<UserRoleRow[]>`
    SELECT r.name AS role_name
    FROM user_role_assignments ura
    JOIN roles r ON r.id = ura.role_id
    WHERE ura.user_id = ${userId}
      AND ura.firm_id = ${firmId}
      AND ura.revoked_at IS NULL
  `;
  return rows.map((r) => r.role_name);
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export async function createSession(
  userId: string,
  firmId: string,
  ipAddress: string | null,
  userAgent: string | null,
): Promise<SessionRow> {
  const sql = getDb();
  const rows = await sql<SessionRow[]>`
    INSERT INTO sessions (user_id, firm_id, ip_address, user_agent)
    VALUES (${userId}, ${firmId}, ${ipAddress}, ${userAgent})
    RETURNING *
  `;
  return rows[0];
}

export async function findActiveSession(sessionId: string): Promise<SessionRow | null> {
  const sql = getDb();
  const rows = await sql<SessionRow[]>`
    SELECT * FROM sessions
    WHERE id = ${sessionId} AND revoked_at IS NULL
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function revokeSession(sessionId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE sessions SET revoked_at = now() WHERE id = ${sessionId}
  `;
}

export async function touchSession(sessionId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE sessions SET last_active_at = now() WHERE id = ${sessionId}
  `;
}

// ---------------------------------------------------------------------------
// Refresh tokens
// ---------------------------------------------------------------------------

export async function createRefreshToken(
  userId: string,
  firmId: string,
  sessionId: string,
  tokenHash: string,
  expiresAt: Date,
): Promise<RefreshTokenRow> {
  const sql = getDb();
  const rows = await sql<RefreshTokenRow[]>`
    INSERT INTO refresh_tokens (user_id, firm_id, session_id, token_hash, expires_at)
    VALUES (${userId}, ${firmId}, ${sessionId}, ${tokenHash}, ${expiresAt})
    RETURNING *
  `;
  return rows[0];
}

export async function findRefreshTokenByHash(
  tokenHash: string,
): Promise<RefreshTokenRow | null> {
  const sql = getDb();
  const rows = await sql<RefreshTokenRow[]>`
    SELECT * FROM refresh_tokens
    WHERE token_hash = ${tokenHash}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function revokeRefreshToken(tokenId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE refresh_tokens SET revoked_at = now() WHERE id = ${tokenId}
  `;
}

export async function revokeAllSessionRefreshTokens(sessionId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE refresh_tokens SET revoked_at = now()
    WHERE session_id = ${sessionId} AND revoked_at IS NULL
  `;
}

// ---------------------------------------------------------------------------
// MFA factors
// ---------------------------------------------------------------------------

export async function findVerifiedMfaFactor(
  userId: string,
  type = 'totp',
): Promise<MfaFactorRow | null> {
  const sql = getDb();
  const rows = await sql<MfaFactorRow[]>`
    SELECT * FROM mfa_factors
    WHERE user_id = ${userId} AND type = ${type} AND verified_at IS NOT NULL
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function findMfaFactor(
  userId: string,
  type = 'totp',
): Promise<MfaFactorRow | null> {
  const sql = getDb();
  const rows = await sql<MfaFactorRow[]>`
    SELECT * FROM mfa_factors
    WHERE user_id = ${userId} AND type = ${type}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function upsertMfaFactor(
  userId: string,
  type: string,
  secretEncrypted: string,
): Promise<MfaFactorRow> {
  const sql = getDb();
  const rows = await sql<MfaFactorRow[]>`
    INSERT INTO mfa_factors (user_id, type, secret_encrypted)
    VALUES (${userId}, ${type}, ${secretEncrypted})
    ON CONFLICT (user_id, type) DO UPDATE
      SET secret_encrypted = EXCLUDED.secret_encrypted,
          verified_at = NULL,
          created_at = now()
    RETURNING *
  `;
  return rows[0];
}

export async function markMfaFactorVerified(factorId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE mfa_factors SET verified_at = now() WHERE id = ${factorId}
  `;
}

// ---------------------------------------------------------------------------
// MFA recovery codes
// ---------------------------------------------------------------------------

export async function deleteRecoveryCodes(userId: string): Promise<void> {
  const sql = getDb();
  await sql`
    DELETE FROM mfa_recovery_codes WHERE user_id = ${userId}
  `;
}

export async function insertRecoveryCodes(
  userId: string,
  codeHashes: string[],
): Promise<void> {
  const sql = getDb();
  const values = codeHashes.map((h) => ({ user_id: userId, code_hash: h }));
  await sql`
    INSERT INTO mfa_recovery_codes ${sql(values, 'user_id', 'code_hash')}
  `;
}

export async function findUnusedRecoveryCode(
  userId: string,
  codeHash: string,
): Promise<MfaRecoveryCodeRow | null> {
  const sql = getDb();
  const rows = await sql<MfaRecoveryCodeRow[]>`
    SELECT * FROM mfa_recovery_codes
    WHERE user_id = ${userId} AND code_hash = ${codeHash} AND used_at IS NULL
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function markRecoveryCodeUsed(codeId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE mfa_recovery_codes SET used_at = now() WHERE id = ${codeId}
  `;
}

// ---------------------------------------------------------------------------
// Lookup helpers (for refresh / MFA challenge flows)
// ---------------------------------------------------------------------------

export async function findUserById(userId: string): Promise<UserRow | null> {
  const sql = getDb();
  const rows = await sql<UserRow[]>`
    SELECT id, firm_id, email, password_hash, display_name, status
    FROM users WHERE id = ${userId} LIMIT 1
  `;
  return rows[0] ?? null;
}
