import { getRedis } from '../../db/redis.js';
import { ForbiddenError, NotFoundError } from '../../shared/errors.js';
import { auditFromContext } from '../../shared/audit.js';
import { assertPermission } from '../../http/middleware/permission.js';
import type { ActorContext } from '../../shared/types.js';
import * as repo from './repository.js';
import {
  toRoleDto,
  toPermissionDto,
  toUserRoleAssignmentDto,
  type RoleDto,
  type PermissionDto,
  type UserRoleAssignmentDto,
} from './types.js';

export async function listAllRoles(): Promise<RoleDto[]> {
  const rows = await repo.listRoles();
  return rows.map(toRoleDto);
}

export async function listAllPermissions(): Promise<PermissionDto[]> {
  const rows = await repo.listPermissions();
  return rows.map(toPermissionDto);
}

export async function getUserRoles(
  userId: string,
  firmId: string,
): Promise<UserRoleAssignmentDto[]> {
  const rows = await repo.getUserRoleAssignments(userId, firmId);
  return rows.map(toUserRoleAssignmentDto);
}

export async function assignRolesToUser(
  targetUserId: string,
  roleNames: string[],
  actor: ActorContext,
  opts: { correlationId?: string; ipAddress?: string; userAgent?: string } = {},
): Promise<UserRoleAssignmentDto[]> {
  assertPermission(actor, 'user.manage_roles');

  // Resolve role names to IDs
  const allRoles = await repo.listRoles();
  const roleIds: string[] = [];
  for (const name of roleNames) {
    const role = allRoles.find((r) => r.name === name);
    if (!role) throw new NotFoundError('Role', name);
    roleIds.push(role.id);
  }

  // Capture previous roles for audit
  const previousAssignments = await repo.getUserRoleAssignments(targetUserId, actor.tenantId);
  const previousRoles = previousAssignments.map((a) => a.role_name);

  // Assign new roles
  const newAssignments = await repo.assignRoles(
    targetUserId,
    actor.tenantId,
    roleIds,
    actor.userId,
  );
  const newRoles = newAssignments.map((a) => a.role_name);

  // Invalidate Redis permission cache
  const redis = getRedis();
  await redis.del(`perms:${targetUserId}`);

  // Emit audit event
  await auditFromContext(actor, 'user.roles_changed', {
    resourceType: 'user',
    resourceId: targetUserId,
    metadata: {
      previous_roles: previousRoles,
      new_roles: newRoles,
    },
    correlationId: opts.correlationId,
    ipAddress: opts.ipAddress,
    userAgent: opts.userAgent,
  });

  return newAssignments.map(toUserRoleAssignmentDto);
}
