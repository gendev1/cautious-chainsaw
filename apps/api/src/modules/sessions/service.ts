import * as repo from './repository.js';
import { toSessionDto, type SessionDto } from './types.js';
import type { ActorContext } from '../../shared/types.js';
import { ForbiddenError, NotFoundError } from '../../shared/errors.js';
import { assertPermission } from '../../http/middleware/permission.js';
import { auditFromContext } from '../../shared/audit.js';
import { getRedis } from '../../db/redis.js';

const SESSION_REVOCATION_TTL = 900; // 15 minutes — matches access token lifetime
const MAX_CONCURRENT_SESSIONS = 10;

function revocationKey(sessionId: string): string {
  return `revoked:session:${sessionId}`;
}

async function addToRevocationSet(sessionId: string): Promise<void> {
  const redis = getRedis();
  await redis.set(revocationKey(sessionId), '1', 'EX', SESSION_REVOCATION_TTL);
}

export async function listSessions(actor: ActorContext): Promise<SessionDto[]> {
  const rows = await repo.listActiveByUser(actor.userId, actor.tenantId);
  return rows.map((row) => toSessionDto(row, actor.sessionId));
}

export async function revokeSession(
  sessionId: string,
  actor: ActorContext,
): Promise<void> {
  const session = await repo.findById(sessionId);
  if (!session || session.revoked_at) {
    throw new NotFoundError('Session', sessionId);
  }

  // Users can only revoke their own sessions (within the same tenant)
  if (session.user_id !== actor.userId || session.firm_id !== actor.tenantId) {
    throw new ForbiddenError('Cannot revoke a session that does not belong to you');
  }

  const revoked = await repo.revokeSession(sessionId);
  if (!revoked) {
    throw new NotFoundError('Session', sessionId);
  }

  await Promise.all([
    addToRevocationSet(sessionId),
    repo.revokeRefreshTokensForSession(sessionId),
  ]);

  await auditFromContext(actor, 'auth.session_revoked', {
    resourceType: 'session',
    resourceId: sessionId,
    metadata: { revokedSessionId: sessionId },
  });
}

export async function revokeAllOtherSessions(actor: ActorContext): Promise<number> {
  const revoked = await repo.revokeAllForUser(actor.userId, actor.tenantId, actor.sessionId);

  await Promise.all(
    revoked.map((s) =>
      Promise.all([
        addToRevocationSet(s.id),
        repo.revokeRefreshTokensForSession(s.id),
      ]),
    ),
  );

  await auditFromContext(actor, 'auth.all_sessions_revoked', {
    resourceType: 'session',
    metadata: {
      revokedCount: revoked.length,
      exceptSessionId: actor.sessionId,
    },
  });

  return revoked.length;
}

export async function adminForceRevokeAllSessions(
  targetUserId: string,
  actor: ActorContext,
): Promise<number> {
  assertPermission(actor, 'user.manage_roles');

  const revoked = await repo.revokeAllForUser(targetUserId, actor.tenantId);

  await Promise.all(
    revoked.map((s) =>
      Promise.all([
        addToRevocationSet(s.id),
        repo.revokeRefreshTokensForSession(s.id),
      ]),
    ),
  );

  await auditFromContext(actor, 'auth.all_sessions_revoked', {
    resourceType: 'session',
    metadata: {
      targetUserId,
      revokedCount: revoked.length,
      adminAction: true,
    },
  });

  return revoked.length;
}

export async function enforceMaxConcurrentSessions(
  userId: string,
  firmId: string,
  maxSessions: number = MAX_CONCURRENT_SESSIONS,
): Promise<void> {
  const activeCount = await repo.countActiveByUser(userId, firmId);

  if (activeCount < maxSessions) {
    return;
  }

  const excess = activeCount - maxSessions + 1; // +1 to make room for the new session
  const oldest = await repo.findOldestActiveSessions(userId, firmId, excess);

  await Promise.all(
    oldest.map(async (session) => {
      await repo.revokeSession(session.id);
      await Promise.all([
        addToRevocationSet(session.id),
        repo.revokeRefreshTokensForSession(session.id),
      ]);
    }),
  );
}
