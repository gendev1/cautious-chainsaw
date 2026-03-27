import { getDb } from '../../db/client.js';
import type { RoleRow, PermissionRow, UserRoleAssignmentWithName } from './types.js';

export async function listRoles(): Promise<RoleRow[]> {
  const sql = getDb();
  return sql<RoleRow[]>`
    SELECT id, name, description, is_system, created_at
    FROM roles
    ORDER BY name
  `;
}

export async function listPermissions(): Promise<PermissionRow[]> {
  const sql = getDb();
  return sql<PermissionRow[]>`
    SELECT id, name, description, created_at
    FROM permissions
    ORDER BY name
  `;
}

export async function getUserRoleAssignments(
  userId: string,
  firmId: string,
): Promise<UserRoleAssignmentWithName[]> {
  const sql = getDb();
  return sql<UserRoleAssignmentWithName[]>`
    SELECT
      ura.id, ura.user_id, ura.firm_id, ura.role_id,
      r.name AS role_name,
      ura.assigned_by, ura.assigned_at, ura.revoked_at
    FROM user_role_assignments ura
    JOIN roles r ON r.id = ura.role_id
    WHERE ura.user_id = ${userId}
      AND ura.firm_id = ${firmId}
      AND ura.revoked_at IS NULL
    ORDER BY ura.assigned_at
  `;
}

export async function assignRoles(
  userId: string,
  firmId: string,
  roleIds: string[],
  assignedBy: string,
): Promise<UserRoleAssignmentWithName[]> {
  const sql = getDb();

  return sql.begin(async (tx: any) => {
    // Soft-revoke all current active assignments for this user+firm
    await tx`
      UPDATE user_role_assignments
      SET revoked_at = now()
      WHERE user_id = ${userId}
        AND firm_id = ${firmId}
        AND revoked_at IS NULL
    `;

    // Insert new assignments
    const rows = [];
    for (const roleId of roleIds) {
      const [row] = await tx<UserRoleAssignmentWithName[]>`
        INSERT INTO user_role_assignments (user_id, firm_id, role_id, assigned_by)
        VALUES (${userId}, ${firmId}, ${roleId}, ${assignedBy})
        RETURNING *,
          (SELECT name FROM roles WHERE id = ${roleId}) AS role_name
      `;
      rows.push(row);
    }

    return rows;
  });
}

export async function resolveUserPermissions(
  userId: string,
  firmId: string,
): Promise<string[]> {
  const sql = getDb();
  const rows = await sql<{ name: string }[]>`
    SELECT DISTINCT p.name
    FROM user_role_assignments ura
    JOIN role_permissions rp ON rp.role_id = ura.role_id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE ura.user_id = ${userId}
      AND ura.firm_id = ${firmId}
      AND ura.revoked_at IS NULL
    ORDER BY p.name
  `;
  return rows.map((r) => r.name);
}
