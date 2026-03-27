import { z } from 'zod';

export const assignRolesSchema = z.object({
  roles: z
    .array(z.string().min(1))
    .min(1, 'At least one role is required'),
});

export const roleResponseSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  description: z.string().nullable(),
  isSystem: z.boolean(),
  createdAt: z.string(),
});

export const permissionResponseSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  description: z.string().nullable(),
  createdAt: z.string(),
});

export const userRoleAssignmentResponseSchema = z.object({
  id: z.string().uuid(),
  userId: z.string().uuid(),
  firmId: z.string().uuid(),
  roleId: z.string().uuid(),
  roleName: z.string(),
  assignedBy: z.string().uuid().nullable(),
  assignedAt: z.string(),
});

export type AssignRolesInput = z.infer<typeof assignRolesSchema>;
export type RoleResponse = z.infer<typeof roleResponseSchema>;
export type PermissionResponse = z.infer<typeof permissionResponseSchema>;
export type UserRoleAssignmentResponse = z.infer<typeof userRoleAssignmentResponseSchema>;
