export interface RoleRow {
  id: string;
  name: string;
  description: string | null;
  is_system: boolean;
  created_at: Date;
}

export interface PermissionRow {
  id: string;
  name: string;
  description: string | null;
  created_at: Date;
}

export interface UserRoleAssignmentRow {
  id: string;
  user_id: string;
  firm_id: string;
  role_id: string;
  assigned_by: string | null;
  assigned_at: Date;
  revoked_at: Date | null;
}

export interface UserRoleAssignmentWithName extends UserRoleAssignmentRow {
  role_name: string;
}

export interface RoleDto {
  id: string;
  name: string;
  description: string | null;
  isSystem: boolean;
  createdAt: string;
}

export interface PermissionDto {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
}

export interface UserRoleAssignmentDto {
  id: string;
  userId: string;
  firmId: string;
  roleId: string;
  roleName: string;
  assignedBy: string | null;
  assignedAt: string;
}

export function toRoleDto(row: RoleRow): RoleDto {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    isSystem: row.is_system,
    createdAt: row.created_at.toISOString(),
  };
}

export function toPermissionDto(row: PermissionRow): PermissionDto {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    createdAt: row.created_at.toISOString(),
  };
}

export function toUserRoleAssignmentDto(row: UserRoleAssignmentWithName): UserRoleAssignmentDto {
  return {
    id: row.id,
    userId: row.user_id,
    firmId: row.firm_id,
    roleId: row.role_id,
    roleName: row.role_name,
    assignedBy: row.assigned_by,
    assignedAt: row.assigned_at.toISOString(),
  };
}
