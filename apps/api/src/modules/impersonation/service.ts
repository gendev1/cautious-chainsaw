import * as repo from './repository.js';
import { toImpersonationSessionDto, type ImpersonationSessionDto } from './types.js';
import type { StartImpersonationInput, ListImpersonationSessionsInput } from './schemas.js';
import type { ActorContext } from '../../shared/types.js';
import { ForbiddenError, NotFoundError, WorkflowStateError } from '../../shared/errors.js';
import { emitAuditEvent } from '../../shared/audit.js';
import { signAccessToken } from '../../shared/jwt.js';
import { findUserById } from '../users/repository.js';
import { getUserRoleAssignments } from '../roles/repository.js';

const BLOCKED_ROLES = ['firm_admin', 'support_impersonator'];

export interface StartImpersonationResult {
  session: ImpersonationSessionDto;
  token: string;
}

export async function startImpersonation(
  input: StartImpersonationInput,
  actor: ActorContext,
  opts: { correlationId?: string; ipAddress?: string; userAgent?: string } = {},
): Promise<StartImpersonationResult> {
  // Impersonation tokens cannot start another impersonation
  if (actor.actorType === 'impersonator') {
    throw new ForbiddenError('Cannot start impersonation while already impersonating');
  }

  // Check idempotency key -- return existing session if found
  const existing = await repo.findByIdempotencyKey(actor.tenantId, input.idempotency_key);
  if (existing) {
    // Re-issue a token for the existing active session
    if (existing.ended_at || existing.expires_at < new Date()) {
      throw new WorkflowStateError(
        'Impersonation session for this idempotency key has already ended or expired',
      );
    }

    const targetRoleAssignments = await getUserRoleAssignments(
      existing.target_user_id,
      actor.tenantId,
    );
    const targetRoles = targetRoleAssignments.map((a) => a.role_name);

    // sub = target user (who we're acting as), imp = impersonator (who's actually doing it)
    const token = await signAccessToken({
      sub: existing.target_user_id,
      tid: actor.tenantId,
      act: 'impersonator',
      sid: existing.id,
      roles: targetRoles,
      mfa: false,
      imp: actor.userId,
    });

    return { session: toImpersonationSessionDto(existing), token };
  }

  // Validate target user exists and belongs to the same tenant
  const targetUser = await findUserById(actor.tenantId, input.target_user_id);
  if (!targetUser) {
    throw new NotFoundError('User', input.target_user_id);
  }

  // Reject if target user has a blocked role
  const targetRoleAssignments = await getUserRoleAssignments(
    input.target_user_id,
    actor.tenantId,
  );
  const targetRoles = targetRoleAssignments.map((a) => a.role_name);

  for (const role of targetRoles) {
    if (BLOCKED_ROLES.includes(role)) {
      throw new ForbiddenError(
        `Cannot impersonate a user with the "${role}" role`,
        { role },
      );
    }
  }

  // Create the session
  const expiresAt = new Date(Date.now() + input.duration_minutes * 60 * 1000);

  const session = await repo.create({
    impersonatorUserId: actor.userId,
    targetUserId: input.target_user_id,
    firmId: actor.tenantId,
    reason: input.reason,
    idempotencyKey: input.idempotency_key,
    expiresAt,
  });

  // sub = target user (who we're acting as), imp = impersonator (who's actually doing it)
  const token = await signAccessToken({
    sub: input.target_user_id,
    tid: actor.tenantId,
    act: 'impersonator',
    sid: session.id,
    roles: targetRoles,
    mfa: false,
    imp: actor.userId,
  });

  // Emit audit event
  await emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'support.impersonation_started',
    resourceType: 'impersonation_session',
    resourceId: session.id,
    metadata: {
      targetUserId: input.target_user_id,
      reason: input.reason,
      durationMinutes: input.duration_minutes,
    },
    correlationId: opts.correlationId,
    ipAddress: opts.ipAddress,
    userAgent: opts.userAgent,
  });

  return { session: toImpersonationSessionDto(session), token };
}

export async function endImpersonation(
  sessionId: string,
  actor: ActorContext,
  opts: { correlationId?: string; ipAddress?: string; userAgent?: string } = {},
): Promise<ImpersonationSessionDto> {
  const existing = await repo.findById(sessionId, actor.tenantId);
  if (!existing) {
    throw new NotFoundError('ImpersonationSession', sessionId);
  }

  if (existing.ended_at) {
    throw new WorkflowStateError('Impersonation session has already ended');
  }

  const row = await repo.endSession(sessionId, actor.tenantId);

  // Emit audit event
  await emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'support.impersonation_ended',
    resourceType: 'impersonation_session',
    resourceId: sessionId,
    metadata: {
      targetUserId: existing.target_user_id,
      impersonatorUserId: existing.impersonator_user_id,
    },
    correlationId: opts.correlationId,
    ipAddress: opts.ipAddress,
    userAgent: opts.userAgent,
  });

  return toImpersonationSessionDto(row);
}

export async function listImpersonationSessions(
  input: ListImpersonationSessionsInput,
  actor: ActorContext,
): Promise<{ sessions: ImpersonationSessionDto[]; total: number; hasMore: boolean; nextCursor?: string }> {
  const { rows, total } = await repo.listSessions(actor.tenantId, {
    impersonatorUserId: input.impersonator_user_id,
    targetUserId: input.target_user_id,
    startedAfter: input.started_after,
    startedBefore: input.started_before,
    cursor: input.cursor,
    limit: input.limit,
  });

  const sessions = rows.map(toImpersonationSessionDto);
  const hasMore = rows.length === input.limit;
  const nextCursor = hasMore ? rows[rows.length - 1].id : undefined;

  return { sessions, total, hasMore, nextCursor };
}
