import bcrypt from 'bcrypt';
import * as OTPAuth from 'otpauth';
import { getConfig } from '../../config.js';
import { signAccessToken, type TokenPayload } from '../../shared/jwt.js';
import { generateToken, hashToken, encrypt, decrypt } from '../../shared/crypto.js';
import { UnauthorizedError, AppError } from '../../shared/errors.js';
import { getRedis } from '../../db/redis.js';
import * as repo from './repository.js';

const SESSION_REVOCATION_TTL = 900; // 15 min — matches access token lifetime

async function addToRevocationSet(sessionId: string): Promise<void> {
  const redis = getRedis();
  await redis.set(`revoked:session:${sessionId}`, '1', 'EX', SESSION_REVOCATION_TTL);
}
import type { LoginResult, FullTokenResult, MfaEnrollResult, LogoutResult } from './types.js';

const RECOVERY_CODE_COUNT = 8;
const RECOVERY_CODE_LENGTH = 16; // hex chars -> 8 bytes of randomness

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

export async function login(
  email: string,
  password: string,
  firmId: string,
  ipAddress: string | null,
  userAgent: string | null,
): Promise<LoginResult> {
  const user = await repo.findUserByEmailAndFirmId(email, firmId);

  // Constant-ish check regardless of user existence to mitigate timing attacks
  if (!user) {
    await bcrypt.hash(password, 10); // burn time
    throw new UnauthorizedError('Invalid email or password');
  }

  if (user.status === 'disabled') {
    throw new UnauthorizedError('Account is disabled');
  }
  if (user.status === 'invited') {
    throw new UnauthorizedError('Account has not been activated yet');
  }
  if (user.firm_status !== 'active') {
    throw new UnauthorizedError('Your organization is not active');
  }
  if (!user.password_hash) {
    throw new UnauthorizedError('Invalid email or password');
  }

  const valid = await bcrypt.compare(password, user.password_hash);
  if (!valid) {
    throw new UnauthorizedError('Invalid email or password');
  }

  // Create session
  const session = await repo.createSession(user.id, user.firm_id, ipAddress, userAgent);
  const roles = await repo.getUserRoles(user.id, user.firm_id);

  // Check if MFA is enrolled and verified
  const mfaFactor = await repo.findVerifiedMfaFactor(user.id, 'totp');
  if (mfaFactor) {
    // Return partial result — user must complete MFA challenge
    return { sessionId: session.id, mfaRequired: true, userId: user.id, firmId: user.firm_id };
  }

  // No MFA — issue full tokens
  return issueFullTokens(user.id, user.firm_id, session.id, roles, false);
}

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------

export async function refresh(rawToken: string): Promise<FullTokenResult> {
  const config = getConfig();
  const tokenHash = hashToken(rawToken);
  const stored = await repo.findRefreshTokenByHash(tokenHash);

  if (!stored) {
    throw new UnauthorizedError('Invalid refresh token');
  }

  // Theft detection: if the token has been revoked, someone is reusing it.
  // Revoke ALL tokens for this session as a safety measure.
  if (stored.revoked_at) {
    await repo.revokeAllSessionRefreshTokens(stored.session_id);
    await repo.revokeSession(stored.session_id);
    await addToRevocationSet(stored.session_id);
    throw new UnauthorizedError('Refresh token reuse detected — session revoked');
  }

  if (new Date(stored.expires_at) < new Date()) {
    throw new UnauthorizedError('Refresh token has expired');
  }

  // Verify session is still active
  const session = await repo.findActiveSession(stored.session_id);
  if (!session) {
    throw new UnauthorizedError('Session has been revoked');
  }

  // Rotate: revoke the old token
  await repo.revokeRefreshToken(stored.id);

  // Fetch user info for new access token
  const user = await repo.findUserById(stored.user_id);
  if (!user || user.status !== 'active') {
    throw new UnauthorizedError('User account is no longer active');
  }

  const roles = await repo.getUserRoles(user.id, user.firm_id);
  const hasMfa = !!(await repo.findVerifiedMfaFactor(user.id, 'totp'));

  // Issue new refresh token
  const newRawToken = generateToken();
  const newHash = hashToken(newRawToken);
  const expiresAt = new Date(Date.now() + config.JWT_REFRESH_TOKEN_TTL * 1000);
  await repo.createRefreshToken(user.id, user.firm_id, session.id, newHash, expiresAt);

  // Update session activity
  await repo.touchSession(session.id);

  // Issue new access token
  const accessToken = await signAccessToken({
    sub: user.id,
    tid: user.firm_id,
    act: 'user',
    sid: session.id,
    roles,
    mfa: hasMfa,
  });

  return {
    accessToken,
    refreshToken: newRawToken,
    expiresIn: config.JWT_ACCESS_TOKEN_TTL,
    mfa: hasMfa,
    userId: user.id,
    firmId: user.firm_id,
  };
}

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

export async function logout(rawToken: string): Promise<LogoutResult | null> {
  const tokenHash = hashToken(rawToken);
  const stored = await repo.findRefreshTokenByHash(tokenHash);

  if (!stored) {
    return null;
  }

  await repo.revokeRefreshToken(stored.id);
  return { userId: stored.user_id, firmId: stored.firm_id };
}

// ---------------------------------------------------------------------------
// MFA Enroll
// ---------------------------------------------------------------------------

export async function mfaEnroll(
  userId: string,
  userEmail: string,
): Promise<MfaEnrollResult> {
  // Generate TOTP secret
  const totp = new OTPAuth.TOTP({
    issuer: 'WealthAdvisor',
    label: userEmail,
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
  });

  const secretEncrypted = encrypt(totp.secret.base32);
  const factor = await repo.upsertMfaFactor(userId, 'totp', secretEncrypted);

  // Generate recovery codes
  const recoveryCodes: string[] = [];
  const codeHashes: string[] = [];
  for (let i = 0; i < RECOVERY_CODE_COUNT; i++) {
    const code = generateToken(8); // 16 hex chars
    recoveryCodes.push(code);
    codeHashes.push(hashToken(code));
  }

  // Replace existing recovery codes
  await repo.deleteRecoveryCodes(userId);
  await repo.insertRecoveryCodes(userId, codeHashes);

  return {
    factorId: factor.id,
    provisioningUri: totp.toString(),
    recoveryCodes,
  };
}

// ---------------------------------------------------------------------------
// MFA Verify — confirms newly enrolled factor
// ---------------------------------------------------------------------------

export async function mfaVerify(userId: string, code: string): Promise<void> {
  const factor = await repo.findMfaFactor(userId, 'totp');
  if (!factor) {
    throw new AppError('MFA_NOT_ENROLLED', 'No MFA factor enrolled', 400);
  }
  if (factor.verified_at) {
    throw new AppError('MFA_ALREADY_VERIFIED', 'MFA factor is already verified', 400);
  }

  const secret = decrypt(factor.secret_encrypted);
  const totp = new OTPAuth.TOTP({
    issuer: 'WealthAdvisor',
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
    secret: OTPAuth.Secret.fromBase32(secret),
  });

  const delta = totp.validate({ token: code, window: 1 });
  if (delta === null) {
    throw new UnauthorizedError('Invalid TOTP code');
  }

  await repo.markMfaFactorVerified(factor.id);
}

// ---------------------------------------------------------------------------
// MFA Challenge — verify TOTP during login, upgrade partial session
// ---------------------------------------------------------------------------

export async function mfaChallenge(
  sessionId: string,
  code: string,
): Promise<FullTokenResult> {
  const session = await repo.findActiveSession(sessionId);
  if (!session) {
    throw new UnauthorizedError('Invalid or expired session');
  }

  const user = await repo.findUserById(session.user_id);
  if (!user || user.status !== 'active') {
    throw new UnauthorizedError('User account is no longer active');
  }

  const factor = await repo.findVerifiedMfaFactor(user.id, 'totp');
  if (!factor) {
    throw new UnauthorizedError('MFA is not configured');
  }

  const secret = decrypt(factor.secret_encrypted);
  const totp = new OTPAuth.TOTP({
    issuer: 'WealthAdvisor',
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
    secret: OTPAuth.Secret.fromBase32(secret),
  });

  const delta = totp.validate({ token: code, window: 1 });
  if (delta === null) {
    throw new UnauthorizedError('Invalid TOTP code');
  }

  const roles = await repo.getUserRoles(user.id, user.firm_id);
  return issueFullTokens(user.id, user.firm_id, session.id, roles, true);
}

// ---------------------------------------------------------------------------
// MFA Recover — use recovery code to bypass TOTP, issue full tokens
// ---------------------------------------------------------------------------

export async function mfaRecover(
  sessionId: string,
  recoveryCode: string,
): Promise<FullTokenResult> {
  const session = await repo.findActiveSession(sessionId);
  if (!session) {
    throw new UnauthorizedError('Invalid or expired session');
  }

  const user = await repo.findUserById(session.user_id);
  if (!user || user.status !== 'active') {
    throw new UnauthorizedError('User account is no longer active');
  }

  const codeHash = hashToken(recoveryCode);
  const code = await repo.findUnusedRecoveryCode(user.id, codeHash);
  if (!code) {
    throw new UnauthorizedError('Invalid recovery code');
  }

  await repo.markRecoveryCodeUsed(code.id);

  const roles = await repo.getUserRoles(user.id, user.firm_id);
  return issueFullTokens(user.id, user.firm_id, session.id, roles, true);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function issueFullTokens(
  userId: string,
  firmId: string,
  sessionId: string,
  roles: string[],
  mfa: boolean,
): Promise<FullTokenResult> {
  const config = getConfig();

  const payload: TokenPayload = {
    sub: userId,
    tid: firmId,
    act: 'user',
    sid: sessionId,
    roles,
    mfa,
  };

  const accessToken = await signAccessToken(payload);

  const rawRefreshToken = generateToken();
  const refreshTokenHash = hashToken(rawRefreshToken);
  const expiresAt = new Date(Date.now() + config.JWT_REFRESH_TOKEN_TTL * 1000);
  await repo.createRefreshToken(userId, firmId, sessionId, refreshTokenHash, expiresAt);

  return {
    accessToken,
    refreshToken: rawRefreshToken,
    expiresIn: config.JWT_ACCESS_TOKEN_TTL,
    mfa,
    userId,
    firmId,
  };
}
