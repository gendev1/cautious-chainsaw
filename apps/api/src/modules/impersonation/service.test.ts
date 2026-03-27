import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mockFirmAdmin, uuid } from '../../test/helpers.js';
import type { ImpersonationSessionRow } from './types.js';
import type { ActorContext } from '../../shared/types.js';

// ---- Mocks ----

vi.mock('./repository.js', () => ({
  findByIdempotencyKey: vi.fn(),
  findById: vi.fn(),
  create: vi.fn(),
  endSession: vi.fn(),
}));

vi.mock('../users/repository.js', () => ({
  findUserById: vi.fn(),
}));

vi.mock('../roles/repository.js', () => ({
  getUserRoleAssignments: vi.fn(),
}));

vi.mock('../../shared/jwt.js', () => ({
  signAccessToken: vi.fn(),
}));

vi.mock('../../shared/audit.js', () => ({
  emitAuditEvent: vi.fn(),
}));

import * as repo from './repository.js';
import { findUserById } from '../users/repository.js';
import { getUserRoleAssignments } from '../roles/repository.js';
import { signAccessToken } from '../../shared/jwt.js';
import { emitAuditEvent } from '../../shared/audit.js';
import { startImpersonation, endImpersonation } from './service.js';
import { ForbiddenError, NotFoundError, WorkflowStateError } from '../../shared/errors.js';

// ---- Helpers ----

function makeSessionRow(overrides: Partial<ImpersonationSessionRow> = {}): ImpersonationSessionRow {
  return {
    id: uuid(),
    impersonator_user_id: 'user-admin-001',
    target_user_id: 'user-target-001',
    firm_id: 'tenant-001',
    reason: 'Support investigation',
    idempotency_key: 'idem-key-001',
    started_at: new Date('2026-03-26T10:00:00Z'),
    expires_at: new Date('2026-03-26T10:30:00Z'),
    ended_at: null,
    ...overrides,
  };
}

function defaultInput() {
  return {
    target_user_id: 'user-target-001',
    reason: 'Support investigation',
    duration_minutes: 30,
    idempotency_key: 'idem-key-001',
  };
}

// ---- Tests ----

describe('impersonation service', () => {
  let actor: ActorContext;

  beforeEach(() => {
    vi.clearAllMocks();
    actor = mockFirmAdmin();

    // Sensible defaults -- individual tests override as needed
    vi.mocked(repo.findByIdempotencyKey).mockResolvedValue(undefined);
    vi.mocked(findUserById).mockResolvedValue({ id: 'user-target-001' } as any);
    vi.mocked(getUserRoleAssignments).mockResolvedValue([
      { role_name: 'client' } as any,
    ]);
    vi.mocked(signAccessToken).mockResolvedValue('mock-jwt-token');
    vi.mocked(emitAuditEvent).mockResolvedValue(undefined as any);
  });

  // ---------- startImpersonation ----------

  describe('startImpersonation', () => {
    it('creates a session and returns a JWT with act:"impersonator", sub=targetUserId, imp=impersonatorUserId', async () => {
      const sessionRow = makeSessionRow();
      vi.mocked(repo.create).mockResolvedValue(sessionRow);

      const result = await startImpersonation(defaultInput(), actor);

      // Session was persisted
      expect(repo.create).toHaveBeenCalledOnce();
      expect(repo.create).toHaveBeenCalledWith(
        expect.objectContaining({
          impersonatorUserId: actor.userId,
          targetUserId: 'user-target-001',
          firmId: actor.tenantId,
          reason: 'Support investigation',
          idempotencyKey: 'idem-key-001',
        }),
      );

      // Token was signed with correct claims
      expect(signAccessToken).toHaveBeenCalledWith(
        expect.objectContaining({
          sub: 'user-target-001',
          tid: actor.tenantId,
          act: 'impersonator',
          sid: sessionRow.id,
          imp: actor.userId,
          mfa: false,
        }),
      );

      expect(result.token).toBe('mock-jwt-token');
      expect(result.session.targetUserId).toBe('user-target-001');
      expect(result.session.impersonatorUserId).toBe(actor.userId);
    });

    it('is idempotent: same idempotency_key returns existing session and token', async () => {
      const existingRow = makeSessionRow({
        expires_at: new Date(Date.now() + 60_000 * 30), // still active
      });
      vi.mocked(repo.findByIdempotencyKey).mockResolvedValue(existingRow);

      const result = await startImpersonation(defaultInput(), actor);

      // Must NOT create a new session
      expect(repo.create).not.toHaveBeenCalled();

      // Re-issues a token for the existing session
      expect(signAccessToken).toHaveBeenCalledWith(
        expect.objectContaining({
          sub: existingRow.target_user_id,
          act: 'impersonator',
          sid: existingRow.id,
          imp: actor.userId,
        }),
      );

      expect(result.session.id).toBe(existingRow.id);
      expect(result.token).toBe('mock-jwt-token');
    });

    it('rejects if the caller is already impersonating (actorType === "impersonator")', async () => {
      const impersonatingActor = mockFirmAdmin({ actorType: 'impersonator' });

      await expect(
        startImpersonation(defaultInput(), impersonatingActor),
      ).rejects.toThrow(ForbiddenError);

      await expect(
        startImpersonation(defaultInput(), impersonatingActor),
      ).rejects.toThrow('Cannot start impersonation while already impersonating');
    });

    it('rejects if target user has the firm_admin role', async () => {
      vi.mocked(getUserRoleAssignments).mockResolvedValue([
        { role_name: 'firm_admin' } as any,
      ]);

      await expect(
        startImpersonation(defaultInput(), actor),
      ).rejects.toThrow(ForbiddenError);

      await expect(
        startImpersonation(defaultInput(), actor),
      ).rejects.toThrow('Cannot impersonate a user with the "firm_admin" role');
    });

    it('rejects if target user has the support_impersonator role', async () => {
      vi.mocked(getUserRoleAssignments).mockResolvedValue([
        { role_name: 'support_impersonator' } as any,
      ]);

      await expect(
        startImpersonation(defaultInput(), actor),
      ).rejects.toThrow(ForbiddenError);

      await expect(
        startImpersonation(defaultInput(), actor),
      ).rejects.toThrow('Cannot impersonate a user with the "support_impersonator" role');
    });

    it('throws NotFoundError if the target user does not exist', async () => {
      vi.mocked(findUserById).mockResolvedValue(null as any);

      await expect(
        startImpersonation(defaultInput(), actor),
      ).rejects.toThrow(NotFoundError);
    });

    it('emits a support.impersonation_started audit event', async () => {
      const sessionRow = makeSessionRow();
      vi.mocked(repo.create).mockResolvedValue(sessionRow);

      const opts = {
        correlationId: 'corr-001',
        ipAddress: '10.0.0.1',
        userAgent: 'TestAgent/1.0',
      };

      await startImpersonation(defaultInput(), actor, opts);

      expect(emitAuditEvent).toHaveBeenCalledOnce();
      expect(emitAuditEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          firmId: actor.tenantId,
          actorId: actor.userId,
          actorType: actor.actorType,
          action: 'support.impersonation_started',
          resourceType: 'impersonation_session',
          resourceId: sessionRow.id,
          metadata: expect.objectContaining({
            targetUserId: 'user-target-001',
            reason: 'Support investigation',
            durationMinutes: 30,
          }),
          correlationId: 'corr-001',
          ipAddress: '10.0.0.1',
          userAgent: 'TestAgent/1.0',
        }),
      );
    });
  });

  // ---------- endImpersonation ----------

  describe('endImpersonation', () => {
    it('sets ended_at on the session', async () => {
      const sessionRow = makeSessionRow();
      const endedRow = makeSessionRow({ ended_at: new Date('2026-03-26T10:15:00Z') });

      vi.mocked(repo.findById).mockResolvedValue(sessionRow);
      vi.mocked(repo.endSession).mockResolvedValue(endedRow);

      const result = await endImpersonation(sessionRow.id, actor);

      expect(repo.endSession).toHaveBeenCalledWith(sessionRow.id, actor.tenantId);
      expect(result.endedAt).toBe('2026-03-26T10:15:00.000Z');
    });

    it('throws WorkflowStateError if the session has already ended', async () => {
      const endedRow = makeSessionRow({ ended_at: new Date('2026-03-26T10:15:00Z') });
      vi.mocked(repo.findById).mockResolvedValue(endedRow);

      await expect(
        endImpersonation(endedRow.id, actor),
      ).rejects.toThrow(WorkflowStateError);

      await expect(
        endImpersonation(endedRow.id, actor),
      ).rejects.toThrow('Impersonation session has already ended');
    });

    it('throws NotFoundError if the session does not exist', async () => {
      vi.mocked(repo.findById).mockResolvedValue(undefined);

      await expect(
        endImpersonation('nonexistent-id', actor),
      ).rejects.toThrow(NotFoundError);
    });

    it('emits a support.impersonation_ended audit event', async () => {
      const sessionRow = makeSessionRow();
      const endedRow = makeSessionRow({ ended_at: new Date() });

      vi.mocked(repo.findById).mockResolvedValue(sessionRow);
      vi.mocked(repo.endSession).mockResolvedValue(endedRow);

      const opts = {
        correlationId: 'corr-002',
        ipAddress: '10.0.0.2',
        userAgent: 'TestAgent/2.0',
      };

      await endImpersonation(sessionRow.id, actor, opts);

      expect(emitAuditEvent).toHaveBeenCalledOnce();
      expect(emitAuditEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          firmId: actor.tenantId,
          actorId: actor.userId,
          actorType: actor.actorType,
          action: 'support.impersonation_ended',
          resourceType: 'impersonation_session',
          resourceId: sessionRow.id,
          metadata: expect.objectContaining({
            targetUserId: sessionRow.target_user_id,
            impersonatorUserId: sessionRow.impersonator_user_id,
          }),
          correlationId: 'corr-002',
          ipAddress: '10.0.0.2',
          userAgent: 'TestAgent/2.0',
        }),
      );
    });
  });

  // ---------- Token semantics ----------

  describe('token semantics', () => {
    it('sub = target user (acting as), imp = impersonator (who is really doing it)', async () => {
      const sessionRow = makeSessionRow();
      vi.mocked(repo.create).mockResolvedValue(sessionRow);

      await startImpersonation(defaultInput(), actor);

      const tokenPayload = vi.mocked(signAccessToken).mock.calls[0][0];

      // sub is the target user the impersonator is acting as
      expect(tokenPayload.sub).toBe('user-target-001');

      // imp is the real person behind the keyboard
      expect(tokenPayload.imp).toBe(actor.userId);

      // act marks this as an impersonation token
      expect(tokenPayload.act).toBe('impersonator');
    });
  });
});
