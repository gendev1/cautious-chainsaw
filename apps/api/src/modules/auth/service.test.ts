import { describe, it, expect, vi, beforeEach } from 'vitest';

// ---------------------------------------------------------------------------
// Mocks — vi.hoisted ensures these are available to vi.mock factories
// ---------------------------------------------------------------------------

const {
  mockRepo,
  mockRedis,
  mockValidate,
  mockToString,
  mockBcrypt,
} = vi.hoisted(() => {
  const fn = vi.fn;
  const store = new Map<string, string>();
  return {
    mockRepo: {
      findUserByEmailAndFirmId: fn(),
      createSession: fn(),
      getUserRoles: fn(),
      findVerifiedMfaFactor: fn(),
      findRefreshTokenByHash: fn(),
      revokeAllSessionRefreshTokens: fn(),
      revokeSession: fn(),
      revokeRefreshToken: fn(),
      findActiveSession: fn(),
      findUserById: fn(),
      createRefreshToken: fn(),
      touchSession: fn(),
      upsertMfaFactor: fn(),
      deleteRecoveryCodes: fn(),
      insertRecoveryCodes: fn(),
      findMfaFactor: fn(),
      markMfaFactorVerified: fn(),
      findUnusedRecoveryCode: fn(),
      markRecoveryCodeUsed: fn(),
    },
    mockRedis: {
      get: fn(async (key: string) => store.get(key) ?? null),
      set: fn(async (key: string, value: string, ..._args: unknown[]) => {
        store.set(key, value);
        return 'OK';
      }),
      del: fn(async (...keys: string[]) => {
        keys.forEach((k) => store.delete(k));
        return keys.length;
      }),
      _store: store,
    },
    mockValidate: fn().mockReturnValue(0),
    mockToString: fn().mockReturnValue(
      'otpauth://totp/WealthAdvisor:user@test.com?secret=JBSWY3DPEHPK3PXP&issuer=WealthAdvisor',
    ),
    mockBcrypt: {
      hash: fn().mockResolvedValue('hashed'),
      compare: fn().mockResolvedValue(true),
    },
  };
});

vi.mock('./repository.js', () => mockRepo);

vi.mock('../../shared/jwt.js', () => ({
  signAccessToken: vi.fn().mockResolvedValue('mock-access-token'),
}));

vi.mock('../../shared/crypto.js', () => ({
  generateToken: vi.fn().mockReturnValue('mock-raw-token'),
  hashToken: vi.fn().mockReturnValue('mock-token-hash'),
  encrypt: vi.fn().mockReturnValue('encrypted-secret'),
  decrypt: vi.fn().mockReturnValue('JBSWY3DPEHPK3PXP'),
}));

vi.mock('../../db/redis.js', () => ({
  getRedis: () => mockRedis,
}));

vi.mock('bcrypt', () => ({
  default: mockBcrypt,
}));

vi.mock('otpauth', () => ({
  TOTP: vi.fn().mockImplementation(() => ({
    secret: { base32: 'JBSWY3DPEHPK3PXP' },
    validate: mockValidate,
    toString: mockToString,
  })),
  Secret: {
    fromBase32: vi.fn().mockReturnValue('decoded-secret'),
  },
}));

vi.mock('../../config.js', () => ({
  getConfig: () => ({
    JWT_ACCESS_TOKEN_TTL: 900,
    JWT_REFRESH_TOKEN_TTL: 604800,
  }),
}));

// ---------------------------------------------------------------------------
// Import SUT after mocks are set up
// ---------------------------------------------------------------------------

import { login, refresh, logout, mfaEnroll, mfaVerify, mfaChallenge, mfaRecover } from './service.js';
import { UnauthorizedError, AppError } from '../../shared/errors.js';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const FIRM_ID = 'firm-001';
const USER_ID = 'user-001';
const SESSION_ID = 'session-001';

function activeUser(overrides: Record<string, unknown> = {}) {
  return {
    id: USER_ID,
    firm_id: FIRM_ID,
    email: 'alice@example.com',
    password_hash: '$2b$10$somehash',
    display_name: 'Alice',
    status: 'active',
    firm_slug: 'acme',
    firm_status: 'active',
    ...overrides,
  };
}

function activeSession(overrides: Record<string, unknown> = {}) {
  return {
    id: SESSION_ID,
    user_id: USER_ID,
    firm_id: FIRM_ID,
    ip_address: '127.0.0.1',
    user_agent: 'vitest',
    created_at: new Date(),
    last_active_at: new Date(),
    revoked_at: null,
    ...overrides,
  };
}

function storedRefreshToken(overrides: Record<string, unknown> = {}) {
  return {
    id: 'rt-001',
    user_id: USER_ID,
    firm_id: FIRM_ID,
    session_id: SESSION_ID,
    token_hash: 'mock-token-hash',
    expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000),
    revoked_at: null,
    created_at: new Date(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  // Restore bcrypt.compare default
  mockBcrypt.compare.mockResolvedValue(true);
  mockValidate.mockReturnValue(0);
});

// ===========================================================================
// login
// ===========================================================================

describe('login', () => {
  it('returns accessToken, refreshToken, userId, and firmId when MFA is not enrolled', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(activeUser());
    mockRepo.createSession.mockResolvedValue(activeSession());
    mockRepo.getUserRoles.mockResolvedValue(['advisor']);
    mockRepo.findVerifiedMfaFactor.mockResolvedValue(null);
    mockRepo.createRefreshToken.mockResolvedValue({});

    const result = await login('alice@example.com', 'password123', FIRM_ID, '127.0.0.1', 'vitest');

    expect(result).toMatchObject({
      accessToken: 'mock-access-token',
      refreshToken: 'mock-raw-token',
      userId: USER_ID,
      firmId: FIRM_ID,
      mfa: false,
    });
    expect(result).toHaveProperty('expiresIn', 900);
  });

  it('returns partial result with mfaRequired when user has verified MFA factor', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(activeUser());
    mockRepo.createSession.mockResolvedValue(activeSession());
    mockRepo.getUserRoles.mockResolvedValue(['advisor']);
    mockRepo.findVerifiedMfaFactor.mockResolvedValue({
      id: 'factor-001',
      user_id: USER_ID,
      type: 'totp',
      secret_encrypted: 'encrypted-secret',
      verified_at: new Date(),
      created_at: new Date(),
    });

    const result = await login('alice@example.com', 'password123', FIRM_ID, '127.0.0.1', 'vitest');

    expect(result).toMatchObject({
      sessionId: SESSION_ID,
      mfaRequired: true,
      userId: USER_ID,
      firmId: FIRM_ID,
    });
    expect(result).not.toHaveProperty('accessToken');
  });

  it('throws UnauthorizedError when password is wrong', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(activeUser());
    mockBcrypt.compare.mockResolvedValue(false);

    await expect(login('alice@example.com', 'wrong', FIRM_ID, null, null)).rejects.toThrow(
      UnauthorizedError,
    );
  });

  it('throws UnauthorizedError when user is not found (no distinction from bad password)', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(null);

    await expect(login('nobody@example.com', 'pass', FIRM_ID, null, null)).rejects.toThrow(
      UnauthorizedError,
    );
    // Should still burn time via bcrypt.hash to mitigate timing attacks
    expect(mockBcrypt.hash).toHaveBeenCalled();
  });

  it('throws UnauthorizedError when user is disabled', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(activeUser({ status: 'disabled' }));

    await expect(login('alice@example.com', 'pass', FIRM_ID, null, null)).rejects.toThrow(
      UnauthorizedError,
    );
  });

  it('throws UnauthorizedError when user is in invited state (not yet registered)', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(activeUser({ status: 'invited' }));

    await expect(login('alice@example.com', 'pass', FIRM_ID, null, null)).rejects.toThrow(
      UnauthorizedError,
    );
  });

  it('throws UnauthorizedError when the firm is inactive', async () => {
    mockRepo.findUserByEmailAndFirmId.mockResolvedValue(activeUser({ firm_status: 'inactive' }));

    await expect(login('alice@example.com', 'pass', FIRM_ID, null, null)).rejects.toThrow(
      UnauthorizedError,
    );
  });
});

// ===========================================================================
// refresh
// ===========================================================================

describe('refresh', () => {
  it('returns new token pair with userId and firmId for a valid refresh token', async () => {
    mockRepo.findRefreshTokenByHash.mockResolvedValue(storedRefreshToken());
    mockRepo.findActiveSession.mockResolvedValue(activeSession());
    mockRepo.revokeRefreshToken.mockResolvedValue(undefined);
    mockRepo.findUserById.mockResolvedValue(activeUser());
    mockRepo.getUserRoles.mockResolvedValue(['advisor']);
    mockRepo.findVerifiedMfaFactor.mockResolvedValue(null);
    mockRepo.createRefreshToken.mockResolvedValue({});
    mockRepo.touchSession.mockResolvedValue(undefined);

    const result = await refresh('raw-token');

    expect(result).toMatchObject({
      accessToken: 'mock-access-token',
      refreshToken: 'mock-raw-token',
      expiresIn: 900,
      userId: USER_ID,
      firmId: FIRM_ID,
    });
    expect(mockRepo.revokeRefreshToken).toHaveBeenCalledWith('rt-001');
    expect(mockRepo.touchSession).toHaveBeenCalledWith(SESSION_ID);
  });

  it('throws UnauthorizedError when the refresh token has expired', async () => {
    mockRepo.findRefreshTokenByHash.mockResolvedValue(
      storedRefreshToken({ expires_at: new Date(Date.now() - 1000) }),
    );

    await expect(refresh('expired-token')).rejects.toThrow(UnauthorizedError);
  });

  it('detects theft when a revoked token is reused — revokes all session tokens and adds to Redis', async () => {
    mockRepo.findRefreshTokenByHash.mockResolvedValue(
      storedRefreshToken({ revoked_at: new Date() }),
    );
    mockRepo.revokeAllSessionRefreshTokens.mockResolvedValue(undefined);
    mockRepo.revokeSession.mockResolvedValue(undefined);

    await expect(refresh('stolen-token')).rejects.toThrow(UnauthorizedError);

    expect(mockRepo.revokeAllSessionRefreshTokens).toHaveBeenCalledWith(SESSION_ID);
    expect(mockRepo.revokeSession).toHaveBeenCalledWith(SESSION_ID);
    expect(mockRedis.set).toHaveBeenCalledWith(
      `revoked:session:${SESSION_ID}`,
      '1',
      'EX',
      900,
    );
  });

  it('throws UnauthorizedError when token hash is not found', async () => {
    mockRepo.findRefreshTokenByHash.mockResolvedValue(null);

    await expect(refresh('unknown-token')).rejects.toThrow(UnauthorizedError);
  });
});

// ===========================================================================
// logout
// ===========================================================================

describe('logout', () => {
  it('revokes the token and returns userId and firmId', async () => {
    mockRepo.findRefreshTokenByHash.mockResolvedValue(storedRefreshToken());
    mockRepo.revokeRefreshToken.mockResolvedValue(undefined);

    const result = await logout('raw-token');

    expect(result).toEqual({ userId: USER_ID, firmId: FIRM_ID });
    expect(mockRepo.revokeRefreshToken).toHaveBeenCalledWith('rt-001');
  });

  it('returns null for an unknown token (silent success)', async () => {
    mockRepo.findRefreshTokenByHash.mockResolvedValue(null);

    const result = await logout('unknown-token');

    expect(result).toBeNull();
  });
});

// ===========================================================================
// mfaEnroll
// ===========================================================================

describe('mfaEnroll', () => {
  it('generates TOTP secret, recovery codes, and returns provisioning URI', async () => {
    mockRepo.upsertMfaFactor.mockResolvedValue({
      id: 'factor-001',
      user_id: USER_ID,
      type: 'totp',
      secret_encrypted: 'encrypted-secret',
      verified_at: null,
      created_at: new Date(),
    });
    mockRepo.deleteRecoveryCodes.mockResolvedValue(undefined);
    mockRepo.insertRecoveryCodes.mockResolvedValue(undefined);

    const result = await mfaEnroll(USER_ID, 'alice@example.com');

    expect(result.factorId).toBe('factor-001');
    expect(result.provisioningUri).toContain('otpauth://totp/');
    expect(result.recoveryCodes).toHaveLength(8);
    expect(mockRepo.upsertMfaFactor).toHaveBeenCalledWith(USER_ID, 'totp', 'encrypted-secret');
    expect(mockRepo.deleteRecoveryCodes).toHaveBeenCalledWith(USER_ID);
    expect(mockRepo.insertRecoveryCodes).toHaveBeenCalledWith(
      USER_ID,
      expect.arrayContaining([expect.any(String)]),
    );
  });
});

// ===========================================================================
// mfaVerify
// ===========================================================================

describe('mfaVerify', () => {
  it('marks the factor as verified when code is valid', async () => {
    mockRepo.findMfaFactor.mockResolvedValue({
      id: 'factor-001',
      user_id: USER_ID,
      type: 'totp',
      secret_encrypted: 'encrypted-secret',
      verified_at: null,
      created_at: new Date(),
    });
    mockRepo.markMfaFactorVerified.mockResolvedValue(undefined);
    mockValidate.mockReturnValue(0);

    await mfaVerify(USER_ID, '123456');

    expect(mockRepo.markMfaFactorVerified).toHaveBeenCalledWith('factor-001');
  });

  it('throws UnauthorizedError when the TOTP code is invalid', async () => {
    mockRepo.findMfaFactor.mockResolvedValue({
      id: 'factor-001',
      user_id: USER_ID,
      type: 'totp',
      secret_encrypted: 'encrypted-secret',
      verified_at: null,
      created_at: new Date(),
    });
    mockValidate.mockReturnValue(null);

    await expect(mfaVerify(USER_ID, '000000')).rejects.toThrow(UnauthorizedError);
  });
});

// ===========================================================================
// mfaChallenge
// ===========================================================================

describe('mfaChallenge', () => {
  it('upgrades to full tokens when TOTP code is valid', async () => {
    mockRepo.findActiveSession.mockResolvedValue(activeSession());
    mockRepo.findUserById.mockResolvedValue(activeUser());
    mockRepo.findVerifiedMfaFactor.mockResolvedValue({
      id: 'factor-001',
      user_id: USER_ID,
      type: 'totp',
      secret_encrypted: 'encrypted-secret',
      verified_at: new Date(),
      created_at: new Date(),
    });
    mockRepo.getUserRoles.mockResolvedValue(['advisor']);
    mockRepo.createRefreshToken.mockResolvedValue({});
    mockValidate.mockReturnValue(0);

    const result = await mfaChallenge(SESSION_ID, '123456');

    expect(result).toMatchObject({
      accessToken: 'mock-access-token',
      refreshToken: 'mock-raw-token',
      mfa: true,
      userId: USER_ID,
      firmId: FIRM_ID,
    });
  });
});

// ===========================================================================
// mfaRecover
// ===========================================================================

describe('mfaRecover', () => {
  it('issues full tokens and marks recovery code as used when code is valid', async () => {
    mockRepo.findActiveSession.mockResolvedValue(activeSession());
    mockRepo.findUserById.mockResolvedValue(activeUser());
    mockRepo.findUnusedRecoveryCode.mockResolvedValue({
      id: 'rc-001',
      user_id: USER_ID,
      code_hash: 'mock-token-hash',
      used_at: null,
      created_at: new Date(),
    });
    mockRepo.markRecoveryCodeUsed.mockResolvedValue(undefined);
    mockRepo.getUserRoles.mockResolvedValue(['advisor']);
    mockRepo.createRefreshToken.mockResolvedValue({});

    const result = await mfaRecover(SESSION_ID, 'recovery-code-1');

    expect(result).toMatchObject({
      accessToken: 'mock-access-token',
      refreshToken: 'mock-raw-token',
      mfa: true,
      userId: USER_ID,
      firmId: FIRM_ID,
    });
    expect(mockRepo.markRecoveryCodeUsed).toHaveBeenCalledWith('rc-001');
  });

  it('throws UnauthorizedError when recovery code is invalid', async () => {
    mockRepo.findActiveSession.mockResolvedValue(activeSession());
    mockRepo.findUserById.mockResolvedValue(activeUser());
    mockRepo.findUnusedRecoveryCode.mockResolvedValue(null);

    await expect(mfaRecover(SESSION_ID, 'bad-code')).rejects.toThrow(UnauthorizedError);
  });
});
