import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { SessionRow } from './types.js';
import { mockFirmAdmin, mockAdvisor, createMockRedis } from '../../test/helpers.js';

// ---- Mocks ----

const mockRedis = createMockRedis();

vi.mock('../../db/redis.js', () => ({
  getRedis: () => mockRedis,
}));

vi.mock('../../shared/audit.js', () => ({
  auditFromContext: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../http/middleware/permission.js', async () => {
  const { ForbiddenError } = await import('../../shared/errors.js');
  return {
    assertPermission: vi.fn((actor: { permissions: string[] }, permission: string) => {
      if (!actor.permissions.includes(permission)) {
        throw new ForbiddenError(`Missing required permission: ${permission}`);
      }
    }),
  };
});

vi.mock('./repository.js', () => ({
  listActiveByUser: vi.fn(),
  findById: vi.fn(),
  revokeSession: vi.fn(),
  revokeAllForUser: vi.fn(),
  revokeRefreshTokensForSession: vi.fn(),
  countActiveByUser: vi.fn(),
  findOldestActiveSessions: vi.fn(),
}));

// ---- Imports (after mocks) ----

import * as repo from './repository.js';
import { auditFromContext } from '../../shared/audit.js';
import { assertPermission } from '../../http/middleware/permission.js';
import {
  listSessions,
  revokeSession,
  revokeAllOtherSessions,
  adminForceRevokeAllSessions,
  enforceMaxConcurrentSessions,
} from './service.js';
import { ForbiddenError, NotFoundError } from '../../shared/errors.js';

// ---- Helpers ----

function makeSessionRow(overrides: Partial<SessionRow> = {}): SessionRow {
  return {
    id: 'session-100',
    user_id: 'user-admin-001',
    firm_id: 'tenant-001',
    ip_address: '127.0.0.1',
    user_agent: 'TestAgent/1.0',
    created_at: new Date('2026-01-01T00:00:00Z'),
    last_active_at: new Date('2026-01-01T01:00:00Z'),
    revoked_at: null,
    ...overrides,
  };
}

// ---- Tests ----

beforeEach(() => {
  vi.clearAllMocks();
  mockRedis._store.clear();
});

describe('listSessions', () => {
  it('returns sessions with current flag set for the actor session', async () => {
    const actor = mockFirmAdmin();
    const rows: SessionRow[] = [
      makeSessionRow({ id: 'session-001' }),
      makeSessionRow({ id: 'session-999' }),
    ];

    vi.mocked(repo.listActiveByUser).mockResolvedValue(rows);

    const result = await listSessions(actor);

    expect(repo.listActiveByUser).toHaveBeenCalledWith(actor.userId, actor.tenantId);
    expect(result).toHaveLength(2);

    const current = result.find((s) => s.id === 'session-001');
    const other = result.find((s) => s.id === 'session-999');

    expect(current?.current).toBe(true);
    expect(other?.current).toBe(false);
  });
});

describe('revokeSession', () => {
  it('revokes in DB, adds to Redis revocation set with 900s TTL, and revokes refresh tokens', async () => {
    const actor = mockFirmAdmin();
    const session = makeSessionRow({ id: 'session-100' });

    vi.mocked(repo.findById).mockResolvedValue(session);
    vi.mocked(repo.revokeSession).mockResolvedValue(session);
    vi.mocked(repo.revokeRefreshTokensForSession).mockResolvedValue(undefined);

    await revokeSession('session-100', actor);

    expect(repo.findById).toHaveBeenCalledWith('session-100');
    expect(repo.revokeSession).toHaveBeenCalledWith('session-100');
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('session-100');
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:session-100', '1', 'EX', 900);
  });

  it('throws NotFoundError if session does not exist or is already revoked', async () => {
    const actor = mockFirmAdmin();

    // Session does not exist
    vi.mocked(repo.findById).mockResolvedValue(undefined);
    await expect(revokeSession('no-such-id', actor)).rejects.toThrow(NotFoundError);

    // Session already revoked
    vi.mocked(repo.findById).mockResolvedValue(
      makeSessionRow({ id: 'revoked-1', revoked_at: new Date() }),
    );
    await expect(revokeSession('revoked-1', actor)).rejects.toThrow(NotFoundError);
  });

  it('throws ForbiddenError if session belongs to a different user', async () => {
    const actor = mockAdvisor(); // userId: user-advisor-001
    const session = makeSessionRow({
      id: 'session-200',
      user_id: 'someone-else',
      firm_id: actor.tenantId,
    });

    vi.mocked(repo.findById).mockResolvedValue(session);

    await expect(revokeSession('session-200', actor)).rejects.toThrow(ForbiddenError);
  });
});

describe('revokeAllOtherSessions', () => {
  it('revokes all sessions except the current one and adds each to Redis', async () => {
    const actor = mockFirmAdmin();
    const revokedRows = [
      makeSessionRow({ id: 'session-aaa' }),
      makeSessionRow({ id: 'session-bbb' }),
    ];

    vi.mocked(repo.revokeAllForUser).mockResolvedValue(revokedRows);
    vi.mocked(repo.revokeRefreshTokensForSession).mockResolvedValue(undefined);

    const count = await revokeAllOtherSessions(actor);

    expect(count).toBe(2);
    expect(repo.revokeAllForUser).toHaveBeenCalledWith(
      actor.userId,
      actor.tenantId,
      actor.sessionId,
    );
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:session-aaa', '1', 'EX', 900);
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:session-bbb', '1', 'EX', 900);
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('session-aaa');
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('session-bbb');
  });

  it('emits auth.all_sessions_revoked audit event with count', async () => {
    const actor = mockFirmAdmin();
    const revokedRows = [
      makeSessionRow({ id: 'session-x1' }),
      makeSessionRow({ id: 'session-x2' }),
      makeSessionRow({ id: 'session-x3' }),
    ];

    vi.mocked(repo.revokeAllForUser).mockResolvedValue(revokedRows);
    vi.mocked(repo.revokeRefreshTokensForSession).mockResolvedValue(undefined);

    await revokeAllOtherSessions(actor);

    expect(auditFromContext).toHaveBeenCalledWith(actor, 'auth.all_sessions_revoked', {
      resourceType: 'session',
      metadata: {
        revokedCount: 3,
        exceptSessionId: actor.sessionId,
      },
    });
  });
});

describe('adminForceRevokeAllSessions', () => {
  it('requires user.manage_roles permission', async () => {
    const advisor = mockAdvisor(); // does NOT have user.manage_roles

    await expect(adminForceRevokeAllSessions('target-user-1', advisor)).rejects.toThrow(
      ForbiddenError,
    );
    expect(assertPermission).toHaveBeenCalledWith(advisor, 'user.manage_roles');
  });

  it('revokes all sessions for the target user', async () => {
    const admin = mockFirmAdmin();
    const revokedRows = [
      makeSessionRow({ id: 'sess-t1', user_id: 'target-user-1' }),
      makeSessionRow({ id: 'sess-t2', user_id: 'target-user-1' }),
    ];

    vi.mocked(repo.revokeAllForUser).mockResolvedValue(revokedRows);
    vi.mocked(repo.revokeRefreshTokensForSession).mockResolvedValue(undefined);

    const count = await adminForceRevokeAllSessions('target-user-1', admin);

    expect(count).toBe(2);
    expect(repo.revokeAllForUser).toHaveBeenCalledWith('target-user-1', admin.tenantId);
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:sess-t1', '1', 'EX', 900);
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:sess-t2', '1', 'EX', 900);
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('sess-t1');
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('sess-t2');
    expect(auditFromContext).toHaveBeenCalledWith(admin, 'auth.all_sessions_revoked', {
      resourceType: 'session',
      metadata: {
        targetUserId: 'target-user-1',
        revokedCount: 2,
        adminAction: true,
      },
    });
  });
});

describe('enforceMaxConcurrentSessions', () => {
  it('does nothing when under the limit', async () => {
    vi.mocked(repo.countActiveByUser).mockResolvedValue(3);

    await enforceMaxConcurrentSessions('user-1', 'firm-1', 10);

    expect(repo.countActiveByUser).toHaveBeenCalledWith('user-1', 'firm-1');
    expect(repo.findOldestActiveSessions).not.toHaveBeenCalled();
    expect(repo.revokeSession).not.toHaveBeenCalled();
  });

  it('revokes oldest sessions when at or above the limit', async () => {
    vi.mocked(repo.countActiveByUser).mockResolvedValue(5);

    const oldest = [
      makeSessionRow({ id: 'old-1', last_active_at: new Date('2025-01-01') }),
      makeSessionRow({ id: 'old-2', last_active_at: new Date('2025-02-01') }),
    ];

    vi.mocked(repo.findOldestActiveSessions).mockResolvedValue(oldest);
    vi.mocked(repo.revokeSession).mockImplementation(async (id) =>
      oldest.find((s) => s.id === id),
    );
    vi.mocked(repo.revokeRefreshTokensForSession).mockResolvedValue(undefined);

    // limit = 5, active = 5 => excess = 5 - 5 + 1 = 1, but we pass 2 oldest rows
    await enforceMaxConcurrentSessions('user-1', 'firm-1', 5);

    expect(repo.findOldestActiveSessions).toHaveBeenCalledWith('user-1', 'firm-1', 1);
    expect(repo.revokeSession).toHaveBeenCalledWith('old-1');
    expect(repo.revokeSession).toHaveBeenCalledWith('old-2');
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:old-1', '1', 'EX', 900);
    expect(mockRedis.set).toHaveBeenCalledWith('revoked:session:old-2', '1', 'EX', 900);
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('old-1');
    expect(repo.revokeRefreshTokensForSession).toHaveBeenCalledWith('old-2');
  });
});
