import { vi, describe, it, expect, beforeEach } from 'vitest';
import { mockFirmAdmin, mockViewer, createMockRedis, uuid } from '../../test/helpers.js';
import type { RoleRow, PermissionRow, UserRoleAssignmentWithName } from './types.js';

// ---- Mocks ----

const mockRedis = createMockRedis();
vi.mock('../../db/redis.js', () => ({
  getRedis: () => mockRedis,
}));

const mockAuditFromContext = vi.fn().mockResolvedValue(undefined);
vi.mock('../../shared/audit.js', () => ({
  auditFromContext: (...args: unknown[]) => mockAuditFromContext(...args),
}));

const now = new Date('2025-06-01T00:00:00Z');

const roleAdmin: RoleRow = {
  id: uuid(),
  name: 'firm_admin',
  description: 'Full access',
  is_system: true,
  created_at: now,
};

const roleAdvisor: RoleRow = {
  id: uuid(),
  name: 'advisor',
  description: 'Advisor access',
  is_system: false,
  created_at: now,
};

const permClientRead: PermissionRow = {
  id: uuid(),
  name: 'client.read',
  description: 'Read clients',
  created_at: now,
};

const permOrderSubmit: PermissionRow = {
  id: uuid(),
  name: 'order.submit',
  description: 'Submit orders',
  created_at: now,
};

function makeAssignment(overrides: Partial<UserRoleAssignmentWithName> = {}): UserRoleAssignmentWithName {
  return {
    id: uuid(),
    user_id: 'target-user-001',
    firm_id: 'tenant-001',
    role_id: roleAdmin.id,
    role_name: 'firm_admin',
    assigned_by: 'user-admin-001',
    assigned_at: now,
    revoked_at: null,
    ...overrides,
  };
}

const mockRepo = {
  listRoles: vi.fn<() => Promise<RoleRow[]>>().mockResolvedValue([roleAdmin, roleAdvisor]),
  listPermissions: vi.fn<() => Promise<PermissionRow[]>>().mockResolvedValue([permClientRead, permOrderSubmit]),
  getUserRoleAssignments: vi.fn<() => Promise<UserRoleAssignmentWithName[]>>().mockResolvedValue([]),
  assignRoles: vi.fn<() => Promise<UserRoleAssignmentWithName[]>>().mockResolvedValue([]),
};

vi.mock('./repository.js', () => mockRepo);

// ---- Import SUT after mocks ----

const { listAllRoles, listAllPermissions, getUserRoles, assignRolesToUser } =
  await import('./service.js');

// ---- Tests ----

describe('roles/service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRedis._store.clear();
  });

  // ---------- listAllRoles ----------

  describe('listAllRoles', () => {
    it('returns mapped RoleDtos', async () => {
      const result = await listAllRoles();

      expect(mockRepo.listRoles).toHaveBeenCalledOnce();
      expect(result).toHaveLength(2);
      expect(result[0]).toEqual({
        id: roleAdmin.id,
        name: 'firm_admin',
        description: 'Full access',
        isSystem: true,
        createdAt: now.toISOString(),
      });
      expect(result[1]).toEqual({
        id: roleAdvisor.id,
        name: 'advisor',
        description: 'Advisor access',
        isSystem: false,
        createdAt: now.toISOString(),
      });
    });
  });

  // ---------- listAllPermissions ----------

  describe('listAllPermissions', () => {
    it('returns mapped PermissionDtos', async () => {
      const result = await listAllPermissions();

      expect(mockRepo.listPermissions).toHaveBeenCalledOnce();
      expect(result).toHaveLength(2);
      expect(result[0]).toEqual({
        id: permClientRead.id,
        name: 'client.read',
        description: 'Read clients',
        createdAt: now.toISOString(),
      });
    });
  });

  // ---------- getUserRoles ----------

  describe('getUserRoles', () => {
    it('returns active assignments for a user', async () => {
      const assignment = makeAssignment();
      mockRepo.getUserRoleAssignments.mockResolvedValueOnce([assignment]);

      const result = await getUserRoles('target-user-001', 'tenant-001');

      expect(mockRepo.getUserRoleAssignments).toHaveBeenCalledWith('target-user-001', 'tenant-001');
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual({
        id: assignment.id,
        userId: 'target-user-001',
        firmId: 'tenant-001',
        roleId: roleAdmin.id,
        roleName: 'firm_admin',
        assignedBy: 'user-admin-001',
        assignedAt: now.toISOString(),
      });
    });
  });

  // ---------- assignRolesToUser ----------

  describe('assignRolesToUser', () => {
    const targetUserId = 'target-user-001';
    const actor = mockFirmAdmin();

    it('resolves role names to IDs and assigns', async () => {
      const previousAssignment = makeAssignment({ role_name: 'viewer', role_id: 'old-role-id' });
      mockRepo.getUserRoleAssignments.mockResolvedValueOnce([previousAssignment]);

      const newAssignment = makeAssignment({ role_name: 'advisor', role_id: roleAdvisor.id });
      mockRepo.assignRoles.mockResolvedValueOnce([newAssignment]);

      const result = await assignRolesToUser(targetUserId, ['advisor'], actor);

      expect(mockRepo.listRoles).toHaveBeenCalledOnce();
      expect(mockRepo.assignRoles).toHaveBeenCalledWith(
        targetUserId,
        actor.tenantId,
        [roleAdvisor.id],
        actor.userId,
      );
      expect(result).toHaveLength(1);
      expect(result[0].roleName).toBe('advisor');
    });

    it('throws NotFoundError for unknown role name', async () => {
      await expect(
        assignRolesToUser(targetUserId, ['nonexistent_role'], actor),
      ).rejects.toThrow(/nonexistent_role.*not found/i);
    });

    it('invalidates Redis cache key perms:{userId}', async () => {
      const previousAssignment = makeAssignment();
      mockRepo.getUserRoleAssignments.mockResolvedValueOnce([previousAssignment]);
      const newAssignment = makeAssignment({ role_name: 'firm_admin' });
      mockRepo.assignRoles.mockResolvedValueOnce([newAssignment]);

      await assignRolesToUser(targetUserId, ['firm_admin'], actor);

      expect(mockRedis.del).toHaveBeenCalledWith(`perms:${targetUserId}`);
    });

    it('emits user.roles_changed audit event with previous and new roles', async () => {
      const previousAssignment = makeAssignment({ role_name: 'viewer' });
      mockRepo.getUserRoleAssignments.mockResolvedValueOnce([previousAssignment]);

      const newAssignment = makeAssignment({ role_name: 'advisor', role_id: roleAdvisor.id });
      mockRepo.assignRoles.mockResolvedValueOnce([newAssignment]);

      await assignRolesToUser(targetUserId, ['advisor'], actor, {
        correlationId: 'corr-123',
        ipAddress: '10.0.0.1',
        userAgent: 'TestAgent/1.0',
      });

      expect(mockAuditFromContext).toHaveBeenCalledOnce();
      expect(mockAuditFromContext).toHaveBeenCalledWith(
        actor,
        'user.roles_changed',
        {
          resourceType: 'user',
          resourceId: targetUserId,
          metadata: {
            previous_roles: ['viewer'],
            new_roles: ['advisor'],
          },
          correlationId: 'corr-123',
          ipAddress: '10.0.0.1',
          userAgent: 'TestAgent/1.0',
        },
      );
    });

    it('throws ForbiddenError without user.manage_roles permission', async () => {
      const viewer = mockViewer(); // no user.manage_roles permission

      await expect(
        assignRolesToUser(targetUserId, ['advisor'], viewer),
      ).rejects.toThrow(/Missing required permission.*user\.manage_roles/);
    });
  });
});
